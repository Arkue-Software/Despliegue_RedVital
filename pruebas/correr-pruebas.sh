#!/usr/bin/env bash
# RedVital — corre en TU MÁQUINA las pruebas de todos los repos, sin levantar
# ningún ambiente. Todo corre en contenedores: no necesitas Java, .NET ni Node.
#
#   pruebas/correr-pruebas.sh                 todas
#   pruebas/correr-pruebas.sh donacion web    solo esas
#
# Componentes: bds identidad campanas donacion notificaciones web gateway contratos secretos
#
# Espera los repos uno al lado del otro (variable REPOS, por defecto la carpeta
# que contiene a este repo):
#   BDs_RedVital/databases  Servicios_RedVital/services  Tools_RedVital/tools
#   FrontEnd/frontend-main  Despliegue_RedVital
set -uo pipefail

AQUI=$(cd "$(dirname "$0")/.." && pwd)
REPOS=${REPOS:-$(cd "$AQUI/.." && pwd)}
BDS=$REPOS/BDs_RedVital/databases
SERVICIOS=$REPOS/Servicios_RedVital/services
GATEWAY=$REPOS/Tools_RedVital/tools/APISIX-config
WEB=$REPOS/FrontEnd/frontend-main
USUARIO="$(id -u):$(id -g)"
REGISTROS=$(mktemp -d)
trap 'rm -rf "$REGISTROS"' EXIT

# Las imágenes de compilación corren como root y escriben bin/, obj/ y target/
# dentro del repo: al terminar se devuelven a tu usuario.
devolver() { echo "chown -R $USUARIO /src 2>/dev/null"; }

bds() { bash "$BDS/pruebas/correr-local.sh"; }

identidad() {
  docker run --rm -v redvital-nuget:/root/.nuget/packages -v "$SERVICIOS/identity:/src" -w /src \
    mcr.microsoft.com/dotnet/sdk:10.0 sh -c "dotnet test RedVital.Identidad.sln -c Release --nologo; r=\$?; $(devolver); exit \$r"
}

campanas() {
  # Las pruebas de eventos levantan PostgreSQL con Testcontainers y aplican las
  # migraciones reales del repo databases.
  docker run --rm --network host -v /var/run/docker.sock:/var/run/docker.sock \
    -v redvital-nuget:/root/.nuget/packages -v "$SERVICIOS/campaign:/src" -v "$BDS:/bds:ro" \
    -e REDVITAL_MIGRACIONES_CAMPANAS=/bds/campanas -e TESTCONTAINERS_HOST_OVERRIDE=127.0.0.1 -w /src \
    mcr.microsoft.com/dotnet/sdk:10.0 sh -c "dotnet test RedVital.Campanas.sln -c Release --nologo; r=\$?; $(devolver); exit \$r"
}

donacion() {
  # Unitarias, ArchUnit e integración con PostgreSQL y Kafka reales (Testcontainers).
  docker run --rm --network host -v /var/run/docker.sock:/var/run/docker.sock \
    -v redvital-m2:/root/.m2 -v "$SERVICIOS/donation:/src" -v "$BDS:/bds:ro" \
    -e TESTCONTAINERS_HOST_OVERRIDE=127.0.0.1 -w /src maven:3.9-eclipse-temurin-25 \
    sh -c "mvn -B verify -Dredvital.migraciones.dir=/bds/donacion; r=\$?; $(devolver); exit \$r"
}

notificaciones() {
  # Unitarias e integración con Kafka real (Testcontainers) y SMTP en memoria (GreenMail).
  docker run --rm --network host -v /var/run/docker.sock:/var/run/docker.sock \
    -v redvital-m2:/root/.m2 -v "$SERVICIOS/notifications:/src" \
    -e TESTCONTAINERS_HOST_OVERRIDE=127.0.0.1 -w /src maven:3.9-eclipse-temurin-25 \
    sh -c "mvn -B verify; r=\$?; $(devolver); exit \$r"
}

web() {
  # Tipos y compilación de producción (el front aún no tiene pruebas automáticas).
  docker run --rm -u "$USUARIO" -e HOME=/tmp -v "$WEB:/app" -w /app node:24 \
    sh -c '[ -d node_modules ] || npm ci --no-audit --no-fund; npx tsc -b --noEmit && npx vite build'
}

gateway() { bash "$GATEWAY/pruebas/correr-local.sh"; }

contratos() {
  # Valida los contratos AsyncAPI de los productores de eventos.
  docker run --rm -e HOME=/tmp -v redvital-npm:/tmp/.npm -v "$SERVICIOS:/s:ro" node:24 sh -c '
    npx -y @asyncapi/cli@3 config analytics --disable >/dev/null 2>&1
    r=0
    for c in donation campaign; do
      echo "-- $c/contratos/asyncapi.yaml"
      npx -y @asyncapi/cli@3 validate /s/$c/contratos/asyncapi.yaml || r=1
    done
    exit $r'
}

# Escanea con gitleaks (1) exactamente los archivos que entrarían en un commit,
# respetando .gitignore, y (2) el historial ya subido de los repos con git.
secretos() {
  local fallos=0 nombre dir temporal lista
  for par in "databases:$BDS" "services:$SERVICIOS" "tools:$REPOS/Tools_RedVital/tools" \
             "web:$WEB" "despliegue:$AQUI"; do
    nombre=${par%%:*}; dir=${par#*:}
    [ -d "$dir" ] || { echo "-- $nombre: no existe $dir, se omite"; continue; }
    temporal=$(mktemp -d); lista=$(mktemp)
    if [ -d "$dir/.git" ]; then
      git -C "$dir" ls-files -co --exclude-standard > "$lista"
    else
      git --git-dir="$temporal/.indice" init -q
      git --git-dir="$temporal/.indice" --work-tree="$dir" ls-files -co --exclude-standard > "$lista"
      rm -rf "$temporal/.indice"
    fi
    rsync -a --files-from="$lista" "$dir/" "$temporal/arbol/"
    echo "-- $nombre: $(wc -l < "$lista") archivos que se subirían"
    docker run --rm -v "$temporal/arbol:/repo:ro" zricethezav/gitleaks:latest dir /repo --no-banner --redact \
      || fallos=$((fallos + 1))
    if [ -d "$dir/.git" ]; then
      echo "-- $nombre: historial ya subido"
      docker run --rm -v "$dir:/repo:ro" zricethezav/gitleaks:latest git /repo --no-banner --redact \
        || echo "   (revisa los hallazgos del historial: si son reales, hay que rotar ese secreto)"
    fi
    rm -rf "$temporal" "$lista"
  done
  return "$fallos"
}

TODOS="bds identidad campanas donacion notificaciones web gateway contratos secretos"
PEDIDOS=${*:-$TODOS}
declare -A RESULTADO DURACION

for c in $PEDIDOS; do
  if ! declare -F "$c" >/dev/null || [[ " $TODOS " != *" $c "* ]]; then
    echo "Componente desconocido: $c (opciones: $TODOS)" >&2
    exit 2
  fi
  echo "==================== $c"
  inicio=$(date +%s)
  if "$c" > "$REGISTROS/$c.log" 2>&1; then RESULTADO[$c]=OK; else RESULTADO[$c]=FALLA; fi
  DURACION[$c]=$(( $(date +%s) - inicio ))
  if [ "${RESULTADO[$c]}" = OK ]; then
    tail -n 3 "$REGISTROS/$c.log"
  else
    tail -n 40 "$REGISTROS/$c.log"
  fi
done

echo
echo "==================== Resumen"
fallas=0
for c in $PEDIDOS; do
  printf '  %-10s %-6s %4ss\n' "$c" "${RESULTADO[$c]}" "${DURACION[$c]}"
  [ "${RESULTADO[$c]}" = OK ] || fallas=$((fallas + 1))
done
[ "$fallas" -eq 0 ] && echo "Todo en verde." || echo "$fallas componente(s) con fallas."
exit "$fallas"
