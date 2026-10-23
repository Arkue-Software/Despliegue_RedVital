#!/usr/bin/env bash
# RedVital — reglas de firewall de una VM, en la cadena DOCKER-USER.
#
#   sudo scripts/firewall.sh <qa|produccion> <datos|servicios|borde>
#   scripts/firewall.sh qa datos --simular      (solo imprime las reglas)
#   sudo scripts/firewall.sh qa datos --instalar (servicio systemd: reaplica tras cada arranque de Docker)
#
# Docker publica los puertos con DNAT antes de que el tráfico llegue a INPUT,
# así que ufw no los ve: las reglas van en DOCKER-USER. Como el paquete ya
# viene traducido al puerto del contenedor (5432 para las tres bases), se
# filtra por el destino ORIGINAL (--ctorigdst / --ctorigdstport).
#
# Cada puerto publicado se abre solo al origen que lo necesita y se descarta
# para cualquier otro. El tráfico entre contenedores de la misma VM no pasa
# por la IP de la VM y no se ve afectado. Idempotente: rehace la cadena
# REDVITAL en cada ejecución. No toca SSH: ese acceso se gestiona aparte.
set -euo pipefail

AMBIENTE=${1:?Uso: firewall.sh <qa|produccion> <datos|servicios|borde> [--simular]}
VM=${2:?Uso: firewall.sh <qa|produccion> <datos|servicios|borde> [--simular]}
SIMULAR=${3:-}
RAIZ=$(cd "$(dirname "$0")/.." && pwd)
ENV="$RAIZ/ambientes/$AMBIENTE.env"
[ -f "$ENV" ] || { echo "Falta $ENV" >&2; exit 1; }
valor() { grep -E "^$1=" "$ENV" | tail -1 | cut -d= -f2-; }
DATOS=$(valor IP_VM_DATOS); SERVICIOS=$(valor IP_VM_SERVICIOS)
BORDE=$(valor IP_VM_BORDE); TOOLS=$(valor IP_VM_TOOLS)

if [ "$SIMULAR" = --instalar ]; then
  # Las reglas de iptables se pierden al reiniciar: un servicio las reaplica
  # cada vez que arranca Docker (que es quien crea la cadena DOCKER-USER).
  unidad=/etc/systemd/system/redvital-firewall.service
  cat > "$unidad" <<UNIDAD
[Unit]
Description=RedVital: reglas DOCKER-USER de la VM $VM ($AMBIENTE)
After=docker.service
Requires=docker.service
PartOf=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$RAIZ/scripts/firewall.sh $AMBIENTE $VM

[Install]
WantedBy=docker.service
UNIDAD
  systemctl daemon-reload
  systemctl enable --now redvital-firewall.service
  echo "Instalado $unidad (VM $VM, $AMBIENTE)"
  exit 0
fi

ipt() {
  if [ "$SIMULAR" = --simular ]; then echo "iptables $*"; else iptables "$@"; fi
}

# permitir <ip-de-esta-vm> <puerto-publicado> <origen>...: abre el puerto a
# esos orígenes y lo cierra para todos los demás.
permitir() {
  local destino=$1 puerto=$2; shift 2
  for origen in "$@"; do
    if [ "$origen" = "$destino" ]; then
      # El origen vive en esta misma VM (Tools junto al borde). Sus contenedores
      # no llegan con la IP de la VM sino desde los puentes de Docker.
      ipt -A REDVITAL -p tcp -i docker0 -m conntrack --ctorigdst "$destino" --ctorigdstport "$puerto" -j RETURN
      ipt -A REDVITAL -p tcp -i br+ -m conntrack --ctorigdst "$destino" --ctorigdstport "$puerto" -j RETURN
    fi
    ipt -A REDVITAL -p tcp -s "$origen" -m conntrack --ctorigdst "$destino" --ctorigdstport "$puerto" -j RETURN
  done
  ipt -A REDVITAL -p tcp -m conntrack --ctorigdst "$destino" --ctorigdstport "$puerto" -j DROP
}

if [ "$SIMULAR" != --simular ]; then
  iptables -N REDVITAL 2>/dev/null || iptables -F REDVITAL
  iptables -C DOCKER-USER -j REDVITAL 2>/dev/null || iptables -I DOCKER-USER 1 -j REDVITAL
else
  echo "iptables -N REDVITAL (o -F si existe); iptables -I DOCKER-USER 1 -j REDVITAL"
fi
ipt -A REDVITAL -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN

case "$VM" in
  datos)
    # Bases: solo la VM de servicios. Métricas: solo Tools.
    for puerto in 5432 5433 5434; do permitir "$DATOS" "$puerto" "$SERVICIOS"; done
    for puerto in 9100 9180; do permitir "$DATOS" "$puerto" "$TOOLS"; done
    ;;
  servicios)
    # Upstreams de APISIX: solo la VM de borde.
    for puerto in 8080 8082 8083; do permitir "$SERVICIOS" "$puerto" "$BORDE"; done
    # Kafka EXTERNO (Kafbat), JMX, métricas de Donación y exportadores: solo Tools.
    for puerto in 9094 9404 8183 9100 9180; do permitir "$SERVICIOS" "$puerto" "$TOOLS"; done
    ;;
  borde)
    # 80 y 443 quedan abiertos al público. Métricas de APISIX y exportadores: solo Tools.
    for puerto in 9091 9100 9180; do permitir "$BORDE" "$puerto" "$TOOLS"; done
    ;;
  *)
    echo "VM desconocida: $VM (datos | servicios | borde). Tools no publica puertos." >&2
    exit 1
    ;;
esac

ipt -A REDVITAL -j RETURN
[ "$SIMULAR" = --simular ] || echo "Reglas de $VM ($AMBIENTE) aplicadas en DOCKER-USER -> REDVITAL"
