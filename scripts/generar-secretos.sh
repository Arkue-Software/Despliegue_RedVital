#!/usr/bin/env bash
# RedVital — genera los secretos de un ambiente en secretos/<ambiente>/.
#
#   scripts/generar-secretos.sh qa
#   scripts/generar-secretos.sh produccion
#   scripts/generar-secretos.sh tools        (solo la clave de Grafana)
#
# Idempotente: un secreto que ya existe no se toca (rotar = borrar el archivo y
# volver a correr, sabiendo lo que implica; la clave HMAC del documento, en
# particular, no se rota sin re-calcular los resúmenes).
#
# Nunca imprime un secreto. Los archivos quedan 0644 dentro de un directorio
# 0700: compose los monta como archivos y cada contenedor corre con su propio
# usuario, así que deben ser legibles; el directorio impide que otro usuario
# del anfitrión llegue a ellos.
#
# Requiere openssl y docker (las huellas ASP.NET de las credenciales se
# calculan con la herramienta de Identidad, en el contenedor del SDK).
set -euo pipefail

AMBIENTE=${1:?Uso: generar-secretos.sh <qa|produccion|tools>}
RAIZ=$(cd "$(dirname "$0")/.." && pwd)
REPOS=$(cd "$RAIZ/.." && pwd)
DIR="$RAIZ/secretos/$AMBIENTE"
umask 077
mkdir -p "$DIR"
chmod 700 "$RAIZ/secretos" "$DIR"

aleatorio() { openssl rand -base64 48 | tr -d '/+=\n' | cut -c1-"${1:-40}"; }

# secreto <nombre> [longitud]: crea un secreto aleatorio si no existe.
secreto() {
  local archivo="$DIR/$1"
  if [ ! -s "$archivo" ]; then
    aleatorio "${2:-40}" > "$archivo"
    echo "  creado  $1"
  fi
  chmod 644 "$archivo"
}

if [ "$AMBIENTE" = tools ]; then
  secreto grafana_admin 32
  secreto kafbat_admin 32
  echo "Secretos de Tools en $DIR"
  exit 0
fi

ENV="$RAIZ/ambientes/$AMBIENTE.env"
[ -f "$ENV" ] || { echo "Falta $ENV (copia $ENV.example)" >&2; exit 1; }
IP_VM_DATOS=$(grep -E '^IP_VM_DATOS=' "$ENV" | cut -d= -f2)

echo "1/5 Bases de datos (superusuario, propietario y servicio de cada base)"
for base in identidad campana donacion; do
  secreto "postgres_superusuario_$base"
  secreto "${base}_propietario"
  secreto "${base}_servicio"
done

echo "2/5 TLS de PostgreSQL (CA del ambiente y certificado del servidor)"
if [ ! -s "$DIR/postgres_ca" ]; then
  openssl req -x509 -newkey rsa:3072 -sha256 -days 1825 -nodes \
    -keyout "$DIR/postgres_ca_llave" -out "$DIR/postgres_ca" \
    -subj "/O=RedVital/CN=RedVital PostgreSQL CA $AMBIENTE" 2>/dev/null
  chmod 600 "$DIR/postgres_ca_llave"
  rm -f "$DIR/postgres_tls_cert" "$DIR/postgres_tls_key"
  echo "  creada  CA de PostgreSQL"
fi
if [ ! -s "$DIR/postgres_tls_cert" ]; then
  extension=$(mktemp)
  printf 'subjectAltName=IP:%s,DNS:db-identidad,DNS:db-campana,DNS:db-donacion,DNS:localhost\nextendedKeyUsage=serverAuth\n' \
    "$IP_VM_DATOS" > "$extension"
  openssl req -newkey rsa:3072 -nodes -keyout "$DIR/postgres_tls_key" -out "$DIR/postgres_tls.csr" \
    -subj "/O=RedVital/CN=$IP_VM_DATOS" 2>/dev/null
  openssl x509 -req -in "$DIR/postgres_tls.csr" -CA "$DIR/postgres_ca" -CAkey "$DIR/postgres_ca_llave" \
    -CAcreateserial -days 825 -sha256 -extfile "$extension" -out "$DIR/postgres_tls_cert" 2>/dev/null
  rm -f "$DIR/postgres_tls.csr" "$extension" "$DIR/postgres_ca.srl"
  echo "  creado  certificado del servidor ($IP_VM_DATOS)"
fi
chmod 644 "$DIR/postgres_ca" "$DIR/postgres_tls_cert" "$DIR/postgres_tls_key"

echo "3/5 Identidad (llave de firma RS256 y credenciales de servicio)"
if [ ! -s "$DIR/identidad_clave_firma" ]; then
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out "$DIR/identidad_clave_firma" 2>/dev/null
  echo "  creada  llave de firma"
fi
chmod 644 "$DIR/identidad_clave_firma"
secreto gateway-secreto-cliente 48
secreto donacion_secreto_identidad 48
secreto notificaciones_secreto_identidad 48
secreto donacion_clave_hmac 64
if [ "$AMBIENTE" = qa ]; then
  # Clave común de las cuentas sintéticas U3–U7 de QA. Se lee de este archivo.
  secreto clave_cuentas_qa 20
fi

echo "4/5 Kafka (administrador y un usuario SCRAM por servicio)"
for usuario in inicializacion donacion campanias notificaciones inspeccion; do
  secreto "kafka_$usuario"
done
# La contraseña SMTP no se genera: es la contraseña de aplicación de la cuenta de
# correo del proyecto. El archivo vacío deja arrancar al trabajador; mientras
# siga vacío, los avisos esperan en Kafka.
if [ ! -e "$DIR/notificaciones_smtp_clave" ]; then
  : > "$DIR/notificaciones_smtp_clave"
  echo "  creado  notificaciones_smtp_clave (vacío: escribe ahí la contraseña de aplicación de Gmail)"
fi
chmod 644 "$DIR/notificaciones_smtp_clave"

echo "5/5 Semillas de Identidad (huellas ASP.NET, nunca la credencial en claro)"
# Se regenera también si a un ambiente anterior le falta el cliente notificaciones.
if [ ! -s "$DIR/credenciales_servicio.sql" ] || ! grep -q "'notificaciones'" "$DIR/credenciales_servicio.sql" \
   || { [ "$AMBIENTE" = qa ] && [ ! -s "$DIR/semilla_identidad.sql" ]; }; then
  GATEWAY_SECRETO=$(cat "$DIR/gateway-secreto-cliente") \
  DONACION_SECRETO=$(cat "$DIR/donacion_secreto_identidad") \
  NOTIFICACIONES_SECRETO=$(cat "$DIR/notificaciones_secreto_identidad") \
  CLAVE_CUENTAS=$( [ "$AMBIENTE" = qa ] && cat "$DIR/clave_cuentas_qa" || true ) \
  docker run --rm -e GATEWAY_SECRETO -e DONACION_SECRETO -e NOTIFICACIONES_SECRETO -e CLAVE_CUENTAS \
    -v redvital-nuget:/root/.nuget/packages \
    -v "$REPOS/Servicios_RedVital/services/identity/herramientas/LlaveDePrueba:/fuente:ro" \
    -v "$DIR:/salida" \
    mcr.microsoft.com/dotnet/sdk:10.0 sh -ec '
      cp -r /fuente /tmp/herramienta && rm -rf /tmp/herramienta/obj /tmp/herramienta/bin
      dotnet build /tmp/herramienta -c Release -o /tmp/llave --nologo -v q >/dev/null
      {
        echo "-- Credenciales de servicio (gateway, donacion, notificaciones). Generado por generar-secretos.sh."
        dotnet /tmp/llave/LlaveDePrueba.dll credencial-servicio --cliente gateway --secreto "$GATEWAY_SECRETO" | grep "^INSERT"
        dotnet /tmp/llave/LlaveDePrueba.dll credencial-servicio --cliente donacion --secreto "$DONACION_SECRETO" | grep "^INSERT"
        dotnet /tmp/llave/LlaveDePrueba.dll credencial-servicio --cliente notificaciones --secreto "$NOTIFICACIONES_SECRETO" | grep "^INSERT"
      } | sed "s/);\$/) ON CONFLICT (cliente) DO UPDATE SET secreto_hash = EXCLUDED.secreto_hash, activa = TRUE;/" \
        > /salida/credenciales_servicio.sql
      if [ -n "$CLAVE_CUENTAS" ]; then
        dotnet /tmp/llave/LlaveDePrueba.dll semilla-identidad --clave "$CLAVE_CUENTAS" > /salida/semilla_identidad.sql
      fi
      chown '"$(id -u):$(id -g)"' /salida/*.sql
    '
  echo "  generadas las semillas de Identidad"
fi
if [ ! -s "$DIR/semilla_identidad.sql" ]; then
  # Fuera de QA no hay cuentas sintéticas; el archivo existe para que compose valide.
  echo "-- $AMBIENTE: sin cuentas sintéticas." > "$DIR/semilla_identidad.sql"
fi
chmod 644 "$DIR"/*.sql

echo "Secretos de $AMBIENTE en $DIR"
