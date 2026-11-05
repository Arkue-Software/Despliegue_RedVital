#!/usr/bin/env bash
# RedVital — comprueba que cada componente esté donde los compose lo esperan.
#
#   scripts/verificar-codigo.sh                  datos, servicios y borde
#   scripts/verificar-codigo.sh servicios        solo lo que usa esa VM
#
# Si falta una carpeta, Docker Compose no lo dice: responde «additional
# privileges requested: pass --allow=fs.read=…», o «failed to read dockerfile»
# si la carpeta salió de la rama main, que solo tiene el commit inicial. Este
# script nombra lo que falta antes de llegar ahí.
set -euo pipefail

RAIZ=$(cd "$(dirname "$0")/.." && pwd)
REPOS=${REPOS:-$(cd "$RAIZ/.." && pwd)}
ROLES=${*:-datos servicios borde}
fallas=0

exigir() {
  [ -e "$REPOS/$1" ] && return 0
  echo "Falta $REPOS/$1" >&2
  fallas=$((fallas + 1))
}

for rol in $ROLES; do
  case "$rol" in
    datos)
      for base in identidad campanas donacion institucional notifications; do
        exigir "BDs_RedVital/databases/$base/00_roles.sql"
        exigir "BDs_RedVital/databases/$base/migraciones"
      done
      exigir BDs_RedVital/databases/init/roles.sh ;;
    servicios)
      for servicio in identity campaign donation institutional notifications; do
        exigir "Servicios_RedVital/services/$servicio/Dockerfile"
      done ;;
    borde)
      exigir FrontEnd/frontend-main/Dockerfile
      exigir Tools_RedVital/tools/APISIX-config/config/apisix.yaml ;;
    *)
      echo "Rol desconocido: $rol (datos, servicios o borde)" >&2
      exit 1 ;;
  esac
done

if [ "$fallas" -gt 0 ]; then
  echo "$fallas archivos faltan. Arma las carpetas desde GitHub, rama develop: scripts/clonar-repos.sh" >&2
  exit 1
fi
echo "Código completo para: $ROLES"
