#!/usr/bin/env bash
# RedVital — arma, desde GitHub, las carpetas que esperan los compose. Cada
# componente es un repo de Arkue-Software, y va en una ruta fija junto a
# Despliegue_RedVital:
#
#   identity, campaign, donation,
#   institutional, notifications   Servicios_RedVital/services/<repo>
#   databases                      BDs_RedVital/databases
#   frontend                       FrontEnd/frontend-main
#   tools                          Tools_RedVital/tools
#
#   scripts/clonar-repos.sh                  rama develop
#   RAMA=main scripts/clonar-repos.sh        otra rama
#
# Usa develop porque main solo tiene lo que ya pasó QA: en un repo sin ninguna
# entrega aprobada, main trae únicamente el commit inicial y no hay Dockerfile.
# Si el clon ya existe lo actualiza. Una carpeta con contenido que no es un
# clon de ese repo no se toca: se avisa y se deja como está.
set -euo pipefail

RAIZ=$(cd "$(dirname "$0")/.." && pwd)
export REPOS=${REPOS:-$(cd "$RAIZ/.." && pwd)}
ORG=${ORG:-Arkue-Software}
RAMA=${RAMA:-develop}

COMPONENTES=(
  "identity|Servicios_RedVital/services/identity"
  "campaign|Servicios_RedVital/services/campaign"
  "donation|Servicios_RedVital/services/donation"
  "institutional|Servicios_RedVital/services/institutional"
  "notifications|Servicios_RedVital/services/notifications"
  "databases|BDs_RedVital/databases"
  "frontend|FrontEnd/frontend-main"
  "tools|Tools_RedVital/tools"
)

for entrada in "${COMPONENTES[@]}"; do
  IFS='|' read -r repo ruta <<< "$entrada"
  destino="$REPOS/$ruta"
  url="https://github.com/$ORG/$repo.git"
  if [ -d "$destino/.git" ]; then
    echo "==> $repo: actualizando $ruta ($RAMA)"
    git -C "$destino" fetch -q origin
    git -C "$destino" checkout -q "$RAMA"
    git -C "$destino" pull -q --ff-only origin "$RAMA"
  elif [ -d "$destino" ] && [ -n "$(ls -A "$destino")" ]; then
    echo "==> $repo: $ruta ya tiene contenido y no es un clon; no se toca"
  else
    echo "==> $repo: clonando en $ruta ($RAMA)"
    mkdir -p "$(dirname "$destino")"
    git clone -q --branch "$RAMA" "$url" "$destino"
  fi
done

"$RAIZ/scripts/verificar-codigo.sh"
