# syntax=docker/dockerfile:1.7
# RedVital — Caddy de borde: único punto expuesto al público.
# Corre sin root: escucha en 8080/8443 y compose publica 80 y 443 hacia ellos.
FROM caddy:2-alpine
# El binario oficial trae cap_net_bind_service como capacidad de archivo; con
# cap_drop: ALL el kernel se niega a ejecutarlo. No la necesita.
RUN apk add --no-cache libcap \
 && setcap -r /usr/bin/caddy \
 && apk del libcap \
 && addgroup -S -g 10001 borde && adduser -S -u 10001 -G borde borde \
 && mkdir -p /data /config && chown -R borde:borde /data /config
COPY Caddyfile /etc/caddy/Caddyfile
USER borde
EXPOSE 8080 8443
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
