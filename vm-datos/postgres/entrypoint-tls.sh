#!/bin/sh
# RedVital — envoltorio del entrypoint oficial de PostgreSQL.
# PostgreSQL exige que su llave TLS pertenezca al usuario postgres con modo
# 0600, y los secretos de compose llegan con el dueño y el modo del anfitrión.
# Corre como root antes del entrypoint oficial (que luego baja a postgres):
# copia certificado y llave a un directorio propio con los permisos exigidos.
set -eu
install -d -o postgres -g postgres -m 0700 /var/lib/postgresql/tls
install -o postgres -g postgres -m 0644 /run/secrets/postgres_tls_cert /var/lib/postgresql/tls/servidor.crt
install -o postgres -g postgres -m 0600 /run/secrets/postgres_tls_key /var/lib/postgresql/tls/servidor.key
exec docker-entrypoint.sh "$@"
