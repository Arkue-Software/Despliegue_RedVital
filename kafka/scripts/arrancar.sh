#!/bin/bash
# RedVital — arranque del broker. Resuelve la configuración desde el entorno y
# los secretos, formatea el almacenamiento la primera vez (con el usuario
# administrador SCRAM, que debe existir antes del primer arranque) y arranca.
set -euo pipefail

SECRETOS=${KAFKA_SECRETOS:-/run/secrets}
leer_secreto() {
  local archivo="$SECRETOS/$1"
  [ -s "$archivo" ] || { echo "Falta el secreto $archivo" >&2; exit 1; }
  tr -d '\r\n' < "$archivo"
}

export KAFKA_CLAVE_ADMIN="$(leer_secreto kafka_inicializacion)"
export KAFKA_HOST_INTERNO="${KAFKA_HOST_INTERNO:-kafka}"
export KAFKA_HOST_EXTERNO="${KAFKA_HOST_EXTERNO:-localhost}"
export KAFKA_PUERTO_EXTERNO="${KAFKA_PUERTO_EXTERNO:-9094}"
export KAFKA_PROTOCOLO_EXTERNO="${KAFKA_PROTOCOLO_EXTERNO:-SASL_PLAINTEXT}"

# En producción el listener EXTERNO cifra con TLS (Herramientas V4.0, sección de seguridad).
KAFKA_TLS_EXTERNO=""
if [ "$KAFKA_PROTOCOLO_EXTERNO" = "SASL_SSL" ]; then
  KAFKA_TLS_EXTERNO="listener.name.externo.ssl.keystore.type=PEM
listener.name.externo.ssl.keystore.location=$SECRETOS/kafka_externo_llavero.pem
"
fi
export KAFKA_TLS_EXTERNO

CONFIG=/var/lib/kafka/server.properties
envsubst < /opt/redvital/config/server.properties.plantilla > "$CONFIG"
chmod 600 "$CONFIG"

if [ ! -f /var/lib/kafka/datos/meta.properties ]; then
  ID_CLUSTER="${KAFKA_ID_CLUSTER:-$(/opt/kafka/bin/kafka-storage.sh random-uuid)}"
  echo "Formateando el almacenamiento KRaft (clúster $ID_CLUSTER)"
  /opt/kafka/bin/kafka-storage.sh format --config "$CONFIG" --cluster-id "$ID_CLUSTER" \
    --add-scram "SCRAM-SHA-512=[name=inicializacion,password=$KAFKA_CLAVE_ADMIN]" --ignore-formatted
fi

export KAFKA_HEAP_OPTS="${KAFKA_HEAP_OPTS:--Xms512m -Xmx512m}"
export KAFKA_OPTS="${KAFKA_OPTS:-} -javaagent:/opt/redvital/jmx_prometheus_javaagent.jar=${KAFKA_PUERTO_METRICAS:-9404}:/opt/redvital/config/jmx-exportador.yaml"
exec /opt/kafka/bin/kafka-server-start.sh "$CONFIG"
