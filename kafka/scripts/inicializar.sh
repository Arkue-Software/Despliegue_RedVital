#!/bin/bash
# RedVital — trabajo efímero de inicialización de Kafka (Herramientas V4.0,
# sección de despliegue). Idempotente: puede correr en cada despliegue.
#   1. Usuarios SCRAM-SHA-512, uno por servicio, con su clave desde /run/secrets.
#   2. Temas del catálogo (catalogo-temas.conf), nunca creados por el broker.
#   3. ACL de la Tabla 30 del DD: cada servicio escribe y lee solo lo suyo.
set -euo pipefail

SECRETOS=${KAFKA_SECRETOS:-/run/secrets}
SERVIDOR=${KAFKA_SERVIDOR:-kafka:9092}
BIN=/opt/kafka/bin
CLIENTE=/tmp/cliente-admin.properties

leer_secreto() { tr -d '\r\n' < "$SECRETOS/$1"; }

umask 077
cat > "$CLIENTE" <<PROPS
security.protocol=SASL_PLAINTEXT
sasl.mechanism=SCRAM-SHA-512
sasl.jaas.config=org.apache.kafka.common.security.scram.ScramLoginModule required username="inicializacion" password="$(leer_secreto kafka_inicializacion)";
PROPS

echo "Esperando al broker en $SERVIDOR"
for _ in $(seq 1 60); do
  if $BIN/kafka-broker-api-versions.sh --bootstrap-server "$SERVIDOR" --command-config "$CLIENTE" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
$BIN/kafka-broker-api-versions.sh --bootstrap-server "$SERVIDOR" --command-config "$CLIENTE" >/dev/null

echo "1/3 Usuarios SCRAM"
for usuario in donacion campanias notificaciones inspeccion; do
  if [ -s "$SECRETOS/kafka_$usuario" ]; then
    $BIN/kafka-configs.sh --bootstrap-server "$SERVIDOR" --command-config "$CLIENTE" --alter \
      --entity-type users --entity-name "$usuario" \
      --add-config "SCRAM-SHA-512=[iterations=8192,password=$(leer_secreto "kafka_$usuario")]" >/dev/null
    echo "  usuario $usuario"
  else
    echo "  sin secreto para $usuario: se omite"
  fi
done

echo "2/3 Temas"
grep -vE '^\s*(#|$)' /opt/redvital/catalogo-temas.conf | while read -r tema particiones retencion; do
  $BIN/kafka-topics.sh --bootstrap-server "$SERVIDOR" --command-config "$CLIENTE" --create --if-not-exists \
    --topic "$tema" --partitions "$particiones" --replication-factor 1 \
    --config retention.ms="$retencion" --config cleanup.policy=delete >/dev/null
  $BIN/kafka-configs.sh --bootstrap-server "$SERVIDOR" --command-config "$CLIENTE" --alter \
    --entity-type topics --entity-name "$tema" --add-config retention.ms="$retencion" >/dev/null
  echo "  $tema ($particiones particiones)"
done

acl() { $BIN/kafka-acls.sh --bootstrap-server "$SERVIDOR" --command-config "$CLIENTE" --add --force "$@" >/dev/null; }
EV01=redvital.donacion.donation-completed.v1
EV02=redvital.donacion.notice-requested.v1
EV03=redvital.campanias.campaign-published.v1

echo "3/3 ACL (DD V3.0, Tabla 30)"
# donacion: escribe EV-01 y EV-02; lee EV-03; escribe y lee sus temas de
# reintento y de mensajes muertos (los lee para reintentar y para alertar).
acl --allow-principal User:donacion --operation Write --operation Describe --topic "$EV01"
acl --allow-principal User:donacion --operation Write --operation Describe --topic "$EV02"
acl --allow-principal User:donacion --operation Read --operation Describe --topic "$EV03"
acl --allow-principal User:donacion --operation Write --operation Read --operation Describe --topic "$EV03.donacion." --resource-pattern-type prefixed
acl --allow-principal User:donacion --operation Read --group donacion --resource-pattern-type prefixed
# campanias: escribe EV-03; lee EV-01; escribe sus mensajes muertos.
acl --allow-principal User:campanias --operation Write --operation Describe --topic "$EV03"
acl --allow-principal User:campanias --operation Read --operation Describe --topic "$EV01"
acl --allow-principal User:campanias --operation Write --operation Describe --topic "$EV01.campanias." --resource-pattern-type prefixed
acl --allow-principal User:campanias --operation Read --group campanias --resource-pattern-type prefixed
# notificaciones: lee EV-02; escribe y lee sus temas de reintento y de
# mensajes muertos.
acl --allow-principal User:notificaciones --operation Read --operation Describe --topic "$EV02"
acl --allow-principal User:notificaciones --operation Write --operation Read --operation Describe --topic "$EV02.notificaciones." --resource-pattern-type prefixed
acl --allow-principal User:notificaciones --operation Read --group notificaciones --resource-pattern-type prefixed
# inspeccion (Kafbat): lee todo salvo EV-02, que lleva identificadores de
# donantes; sin grupo propio que confirme posiciones.
acl --allow-principal User:inspeccion --operation Read --operation Describe --operation DescribeConfigs --topic redvital. --resource-pattern-type prefixed
acl --deny-principal User:inspeccion --operation Read --topic "$EV02"
acl --deny-principal User:inspeccion --operation Read --topic "$EV02." --resource-pattern-type prefixed
acl --allow-principal User:inspeccion --operation Describe --group '*'
acl --allow-principal User:inspeccion --operation Describe --operation DescribeConfigs --cluster

rm -f "$CLIENTE"
echo "Inicialización de Kafka completa"
