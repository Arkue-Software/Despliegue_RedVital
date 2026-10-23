#!/usr/bin/env bash
# RedVital — empaqueta SOLO los secretos que necesita una VM.
#
#   scripts/empaquetar-secretos.sh qa datos       -> secretos-qa-datos.tar.gz
#   scripts/empaquetar-secretos.sh qa servicios
#   scripts/empaquetar-secretos.sh qa borde
#   scripts/empaquetar-secretos.sh qa tools       (la clave SCRAM de solo lectura)
#
# En la VM, desde la raíz de Despliegue_RedVital:
#   tar -xzf secretos-qa-datos.tar.gz && rm secretos-qa-datos.tar.gz
#
# La llave privada de la CA de PostgreSQL (postgres_ca_llave) y la clave de las
# cuentas de QA (clave_cuentas_qa) nunca salen de la máquina que generó los
# secretos: ninguna VM las necesita.
set -euo pipefail

AMBIENTE=${1:?Uso: empaquetar-secretos.sh <ambiente> <datos|servicios|borde|tools>}
VM=${2:?Uso: empaquetar-secretos.sh <ambiente> <datos|servicios|borde|tools>}
RAIZ=$(cd "$(dirname "$0")/.." && pwd)
DIR="secretos/$AMBIENTE"
[ -d "$RAIZ/$DIR" ] || { echo "No existe $DIR: corre antes scripts/generar-secretos.sh $AMBIENTE" >&2; exit 1; }

case "$VM" in
  datos)
    archivos="postgres_tls_cert postgres_tls_key postgres_ca
      postgres_superusuario_identidad identidad_propietario identidad_servicio
      postgres_superusuario_campana campana_propietario campana_servicio
      postgres_superusuario_donacion donacion_propietario donacion_servicio
      credenciales_servicio.sql semilla_identidad.sql" ;;
  servicios)
    archivos="postgres_ca identidad_servicio identidad_clave_firma campana_servicio
      donacion_servicio donacion_secreto_identidad donacion_clave_hmac
      kafka_inicializacion kafka_donacion kafka_campanias kafka_notificaciones kafka_inspeccion
      notificaciones_secreto_identidad notificaciones_smtp_clave" ;;
  borde)
    archivos="gateway-secreto-cliente" ;;
  tools)
    archivos="kafka_inspeccion" ;;
  *)
    echo "VM desconocida: $VM" >&2; exit 1 ;;
esac

rutas=()
for a in $archivos; do
  # La clave SMTP puede ir vacía: se escribe a mano cuando exista la cuenta de correo.
  [ -s "$RAIZ/$DIR/$a" ] || { [ "$a" = notificaciones_smtp_clave ] && [ -e "$RAIZ/$DIR/$a" ]; } \
    || { echo "Falta $DIR/$a" >&2; exit 1; }
  rutas+=("$DIR/$a")
done

salida="$RAIZ/secretos-$AMBIENTE-$VM.tar.gz"
umask 077
# El directorio va con 0700 y los archivos con 0644, como los deja generar-secretos.sh.
tar -C "$RAIZ" --owner=0 --group=0 --numeric-owner --no-recursion -czf "$salida" secretos "$DIR" "${rutas[@]}"
chmod 600 "$salida"
echo "$salida (${#rutas[@]} archivos). Cópialo a la VM de $VM por scp y bórralo de aquí después."
