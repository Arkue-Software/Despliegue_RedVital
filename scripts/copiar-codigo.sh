#!/usr/bin/env bash
# RedVital — copia el código desde esta máquina a las VM, por SSH. Cada VM
# recibe solo los repos que usa su rol.
#
#   scripts/copiar-codigo.sh datos=10.43.99.183 servicios=10.43.99.174 \
#                            borde=10.43.98.220 tools=10.43.100.121
#
#   datos      Despliegue_RedVital  BDs_RedVital
#   servicios  Despliegue_RedVital  Servicios_RedVital
#   borde      Despliegue_RedVital  FrontEnd  Tools_RedVital
#   tools      Despliegue_RedVital
#
# Variables opcionales: USUARIO_VM (estudiante) y DESTINO_VM (Downloads/RedVital,
# relativo al home del usuario en la VM).
#
# Usa rsync: envía solo lo que cambió y borra en la VM lo que se borró aquí, así
# no quedan archivos viejos que se cuelen en una compilación. Nunca envía ni
# borra lo que pertenece a cada VM: secretos, ambientes (*.env), configuración
# generada de Tools, la CA extraída y los respaldos. Tampoco envía dependencias
# ni compilados: cada VM compila dentro de Docker.
# Sirve igual para la primera copia y para cada actualización.
set -euo pipefail

USO="Uso: copiar-codigo.sh <rol>=<ip>...   (rol: datos | servicios | borde | tools)"
[ $# -gt 0 ] || { echo "$USO" >&2; exit 1; }
RAIZ=$(cd "$(dirname "$0")/.." && pwd)
REPOS=$(cd "$RAIZ/.." && pwd)
USUARIO_VM=${USUARIO_VM:-estudiante}
DESTINO_VM=${DESTINO_VM:-Downloads/RedVital}

carpetas_de() {
  case "$1" in
    datos)     echo "Despliegue_RedVital BDs_RedVital" ;;
    servicios) echo "Despliegue_RedVital Servicios_RedVital" ;;
    borde)     echo "Despliegue_RedVital FrontEnd Tools_RedVital" ;;
    tools)     echo "Despliegue_RedVital" ;;
    *)         return 1 ;;
  esac
}

# Valida todo antes de copiar nada.
for par in "$@"; do
  rol=${par%%=*}; ip=${par#*=}
  [ "$rol" != "$par" ] && [ -n "$ip" ] || { echo "Argumento inválido: $par. $USO" >&2; exit 1; }
  carpetas=$(carpetas_de "$rol") || { echo "Rol desconocido: $rol. $USO" >&2; exit 1; }
  for carpeta in $carpetas; do
    [ -d "$REPOS/$carpeta" ] || { echo "Falta $REPOS/$carpeta" >&2; exit 1; }
  done
done

OPCIONES=(
  -az --delete
  --exclude=secretos/ --exclude='secretos-*.tar.gz' --exclude='*.env'
  --exclude=generado/ --exclude='*.crt' --exclude='*.dump'
  --exclude=node_modules/ --exclude=target/ --exclude=bin/ --exclude=obj/
  --exclude=dist/ --exclude=__pycache__/
)

cd "$REPOS"
fallas=0
for par in "$@"; do
  rol=${par%%=*}; ip=${par#*=}
  carpetas=$(carpetas_de "$rol")
  echo "==> $rol ($USUARIO_VM@$ip:$DESTINO_VM): $carpetas"
  # --rsync-path crea la carpeta de destino si la VM aún no la tiene.
  # shellcheck disable=SC2086
  if rsync "${OPCIONES[@]}" --rsync-path="mkdir -p $DESTINO_VM && rsync" \
       $carpetas "$USUARIO_VM@$ip:$DESTINO_VM/"; then
    echo "    copiado"
  else
    echo "    FALLÓ (¿la VM tiene rsync y SSH? Ver la guía, paso 1.2)" >&2
    fallas=$((fallas + 1))
  fi
done
[ "$fallas" -eq 0 ] && echo "Código copiado a $# VM." || echo "$fallas VM con fallas." >&2
exit "$fallas"
