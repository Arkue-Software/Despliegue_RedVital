#!/usr/bin/env bash
# RedVital — genera la configuración de la VM de Tools a partir de los
# ambientes presentes (ambientes/qa.env y ambientes/produccion.env):
#   vm-tools/generado/prometheus.yml   objetivos de cada VM de cada ambiente
#   vm-tools/generado/kafbat.yml       un clúster de Kafka por ambiente
#   secretos/tools/kafka_inspeccion_<ambiente>  copia de la clave SCRAM de lectura
# En la VM de Tools real, copiar antes secretos/<ambiente>/kafka_inspeccion.
set -euo pipefail

RAIZ=$(cd "$(dirname "$0")/.." && pwd)
SALIDA="$RAIZ/vm-tools/generado"
SECRETOS="$RAIZ/secretos/tools"
umask 077
mkdir -p "$SALIDA" "$SECRETOS"

valor() { grep -E "^$2=" "$1" | tail -1 | cut -d= -f2-; }

{
  echo "# Generado por scripts/renderizar-tools.sh: no editar a mano."
  echo "global:"
  echo "  scrape_interval: 30s"
  echo "  evaluation_interval: 30s"
  echo "scrape_configs:"
} > "$SALIDA/prometheus.yml"

{
  echo "# Generado por scripts/renderizar-tools.sh: no editar a mano."
  echo "auth:"
  echo "  type: LOGIN_FORM"
  echo "spring:"
  echo "  security:"
  echo "    user:"
  echo "      name: admin"
  echo "      password: \${kafbat_admin}"
  echo "kafka:"
  echo "  clusters:"
} > "$SALIDA/kafbat.yml"

clusters=0
for ambiente in qa produccion; do
  env="$RAIZ/ambientes/$ambiente.env"
  secreto="$SECRETOS/kafka_inspeccion_$ambiente"
  if [ ! -f "$env" ]; then
    : > "$secreto"; chmod 644 "$secreto"
    echo "  sin ambientes/$ambiente.env: se omite"
    continue
  fi
  datos=$(valor "$env" IP_VM_DATOS); servicios=$(valor "$env" IP_VM_SERVICIOS); borde=$(valor "$env" IP_VM_BORDE)

  cat >> "$SALIDA/prometheus.yml" <<YAML
  - job_name: donacion-$ambiente
    metrics_path: /actuator/prometheus
    static_configs: [{ targets: ["$servicios:8183"], labels: { ambiente: $ambiente, servicio: donacion } }]
  - job_name: kafka-$ambiente
    static_configs: [{ targets: ["$servicios:9404"], labels: { ambiente: $ambiente, servicio: kafka } }]
  - job_name: apisix-$ambiente
    metrics_path: /apisix/prometheus/metrics
    static_configs: [{ targets: ["$borde:9091"], labels: { ambiente: $ambiente, servicio: apisix } }]
  - job_name: nodos-$ambiente
    static_configs:
      - { targets: ["$datos:9100"], labels: { ambiente: $ambiente, vm: datos } }
      - { targets: ["$servicios:9100"], labels: { ambiente: $ambiente, vm: servicios } }
      - { targets: ["$borde:9100"], labels: { ambiente: $ambiente, vm: borde } }
  - job_name: contenedores-$ambiente
    static_configs:
      - { targets: ["$datos:9180"], labels: { ambiente: $ambiente, vm: datos } }
      - { targets: ["$servicios:9180"], labels: { ambiente: $ambiente, vm: servicios } }
      - { targets: ["$borde:9180"], labels: { ambiente: $ambiente, vm: borde } }
YAML

  origen="$RAIZ/secretos/$ambiente/kafka_inspeccion"
  if [ -s "$origen" ]; then cp "$origen" "$secreto"; else : > "$secreto"; fi
  chmod 644 "$secreto"
  protocolo=SASL_PLAINTEXT
  [ "$ambiente" = produccion ] && protocolo=SASL_SSL
  cat >> "$SALIDA/kafbat.yml" <<YAML
    - name: $ambiente
      bootstrapServers: $servicios:9094
      readOnly: true
      properties:
        security.protocol: $protocolo
        sasl.mechanism: SCRAM-SHA-512
        sasl.jaas.config: 'org.apache.kafka.common.security.scram.ScramLoginModule required username="inspeccion" password="\${kafka_inspeccion_$ambiente}";'
YAML
  clusters=$((clusters + 1))
  echo "  ambiente $ambiente: VM datos $datos, servicios $servicios, borde $borde"
done

[ "$clusters" -gt 0 ] || { echo "No hay ningún ambientes/<ambiente>.env" >&2; exit 1; }
chmod 644 "$SALIDA"/*.yml
echo "Configuración de Tools en $SALIDA"
