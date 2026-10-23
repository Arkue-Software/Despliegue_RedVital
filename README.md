	# RedVital — Despliegue

Composición de RedVital en contenedores: **3 VM por ambiente** (QA y producción) y **1 VM de Tools** compartida.

```
                 público 80/443
                       │
┌──────────────── VM de borde ────────────────┐
│ caddy ──/api/*──► apisix      web (estático) │   TLS, cabeceras, 1 MiB, límite de tasa
└──────────────────────┬──────────────────────┘
                       │ 8080 / 8082 / 8083 (solo desde borde)
┌────────────── VM de servicios ──────────────┐
│ identity-service  campaign-service           │
│ donation-service  kafka (+ kafka-init)       │   Campañas ⇄ Donación SOLO por Kafka
└──────────────────────┬──────────────────────┘
                       │ 5432 / 5433 / 5434 TLS (solo desde servicios)
┌──────────────── VM de datos ────────────────┐
│ db-identidad  db-campana  db-donacion        │   una base por servicio
│ migraciones Flyway, semillas de QA           │
└─────────────────────────────────────────────┘
VM de Tools (compartida): Prometheus · Grafana · Kafbat, solo por túnel SSH
```

| Carpeta | Contenido |
|---|---|
| `vm-datos/` | Tres PostgreSQL 16 con TLS obligatorio (`pg_hba.conf`: `hostssl` + SCRAM; sin TLS se rechaza), migraciones, credenciales de servicio y semillas |
| `vm-servicios/` | Identidad, Campañas, Donación, Kafka 4.3.1 y su trabajo de inicialización |
| `vm-borde/` | Caddy de borde, la web y APISIX 3.19 |
| `vm-tools/` | Prometheus, Grafana y Kafbat |
| `kafka/` | Imagen `redvital/kafka:4.3.1`: KRaft, SASL/SCRAM, ACL, catálogo de temas |
| `ambientes/` | Un `.env` por ambiente con las IP de sus VM (sin secretos) |
| `semillas/qa/` | Campañas (con su EV-03 en la bandeja) y umbrales sintéticos de QA |
| `scripts/` | Copia del código a las VM, secretos, firewall y configuración de Tools |

Los compose construyen desde los repos hermanos (`REPOS=../..`): `Servicios_RedVital`, `BDs_RedVital`, `FrontEnd`, `Tools_RedVital`. Cuando exista un registro de imágenes, basta con publicar las imágenes `redvital/*:<VERSION>` y desplegar con `up --no-build`.

## Seguridad

- **La web solo habla con APISIX.** El navegador llama a `/api/v1/*` en el mismo origen; Caddy lo envía a APISIX, que valida el token (firma, emisor, vigencia, contra el JWKS de Identidad) y el rol de la ruta antes de enrutar. `/api/internal/*` responde 404 en Caddy y en APISIX.
- **Puertos.** Cada puerto se publica solo en la IP de su VM, y `scripts/firewall.sh` lo abre únicamente al origen que lo necesita, en la cadena `DOCKER-USER` (ufw no ve los puertos de Docker). APISIX y la web no publican nada; Tools escucha solo en `127.0.0.1`.
- **Secretos** como archivos en `/run/secrets`, generados por `scripts/generar-secretos.sh` en `secretos/<ambiente>/` (directorio `0700`, fuera de git). Nunca en variables de entorno ni en la imagen.
- **Bases.** Una por servicio, con usuario propietario (migraciones) y usuario de servicio con permisos por columna. TLS 1.2+ obligatorio y verificación de la CA del ambiente desde los servicios (`verify-ca`).
- **Kafka.** SASL/SCRAM-SHA-512 por servicio, `StandardAuthorizer` sin permisos por defecto, ACL de la Tabla 30 del DD, temas creados solo por `kafka-init`.
- **Contenedores** sin root, con `cap_drop: ALL`, `no-new-privileges` y sistema de archivos de solo lectura en los servicios, la web y Caddy.
- **Borde.** HSTS, CSP `default-src 'self'` (la web no carga nada de terceros, ni fuentes), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, cuerpo máximo de 1 MiB, límite de tasa en las rutas públicas (60/min) y en el inicio de sesión (10/min).

## Desplegar un ambiente

> - **Operar, desplegar y probar QA:** [`docs/guia-despliegue-qa.md`](docs/guia-despliegue-qa.md), la guía del equipo.
> - **Montar VM nuevas desde cero** (instalar Docker, generar y repartir secretos, firewall): [`docs/preparacion-inicial-vm.md`](docs/preparacion-inicial-vm.md).

### Qué va en cada VM

| VM | Repos clonados (uno al lado del otro) | Puertos que publica |
|---|---|---|
| Datos | `Despliegue_RedVital`, `BDs_RedVital` | 5432, 5433, 5434 (servicios); 9100, 9180 (Tools) |
| Servicios | `Despliegue_RedVital`, `Servicios_RedVital` | 8080, 8082, 8083 (borde); 9094, 9404, 8183, 9100, 9180 (Tools) |
| Borde | `Despliegue_RedVital`, `FrontEnd`, `Tools_RedVital` | 80, 443 (público); 9091, 9100, 9180 (Tools) |
| Tools | `Despliegue_RedVital` | ninguno (127.0.0.1, por túnel SSH) |

Requisitos de cada VM: Docker Engine con el plugin compose y el backend de firewall por defecto (iptables, que crea la cadena `DOCKER-USER`), reloj sincronizado (chrony/NTP: el token admite 30 s de desfase) y salida a internet para construir. Sin internet, construye en otra máquina y lleva las imágenes con `docker save` / `docker load`, luego `up --no-build`.

### 1. Secretos (en una máquina de confianza, una sola vez)

```bash
cp ambientes/qa.env.example ambientes/qa.env
```

Edita las IP **de la red privada entre VM**, `DOMINIO` y `TLS_MODO` antes de generar: el certificado de PostgreSQL lleva la IP de la VM de datos.

```bash
scripts/generar-secretos.sh qa
```

Empaqueta lo que necesita cada VM (y nada más) y cópialo junto con `ambientes/qa.env`:

```bash
for vm in datos servicios borde tools; do scripts/empaquetar-secretos.sh qa $vm; done
```

En cada VM, desde la raíz de `Despliegue_RedVital`: `tar -xzf secretos-qa-<vm>.tar.gz`. La llave de la CA (`postgres_ca_llave`) y la clave de las cuentas de QA no salen de la máquina de confianza.

**Correo de los avisos.** El trabajador de Notificaciones envía por SMTP (en QA, una cuenta de Gmail del proyecto). En la VM de servicios: la contraseña de aplicación va en `secretos/qa/notificaciones_smtp_clave` (el script deja el archivo vacío) y la cuenta en `SMTP_USUARIO` de `ambientes/qa.env`. Sin ellas todo funciona, pero los correos esperan en Kafka hasta que se configuren. Paso a paso: `docs/guia-despliegue-qa.md`, sección 8.2.

### 2. Levantar, en este orden

```bash
docker compose -f vm-datos/docker-compose.yml --env-file ambientes/qa.env up -d
```

```bash
docker compose -f vm-servicios/docker-compose.yml --env-file ambientes/qa.env up -d --build
```

```bash
docker compose -f vm-borde/docker-compose.yml --env-file ambientes/qa.env up -d --build
```

Cada paso termina cuando `docker compose ... ps -a` muestra los servicios `healthy` y los trabajos (`migracion-*`, `credenciales-identidad`, `semilla-*`, `kafka-init`) en `Exited (0)`.

### 3. Firewall (en datos, servicios y borde)

Primero revisa las reglas, luego instálalas como servicio (se reaplican en cada arranque de Docker):

```bash
scripts/firewall.sh qa datos --simular
```

```bash
sudo scripts/firewall.sh qa datos --instalar
```

`ufw` no filtra los puertos de Docker; úsalo solo para el SSH de la VM.

### 4. VM de Tools

```bash
scripts/generar-secretos.sh tools
```

```bash
scripts/renderizar-tools.sh
```

```bash
docker compose -f vm-tools/docker-compose.yml up -d
```

`renderizar-tools.sh` lee `ambientes/qa.env` y `ambientes/produccion.env` (los que existan) y copia la clave SCRAM de solo lectura `kafka_inspeccion` de cada ambiente: en la VM de Tools deben estar `secretos/<ambiente>/kafka_inspeccion`. Acceso por túnel:

```bash
ssh -L 3000:127.0.0.1:3000 -L 9090:127.0.0.1:9090 -L 8080:127.0.0.1:8080 usuario@vm-tools
```

Grafana (`admin`) y Kafbat (`admin`) usan las claves de `secretos/tools/grafana_admin` y `secretos/tools/kafbat_admin`.

### 5. Verificar

Desde un equipo de pruebas con acceso al borde, con la raíz de la CA de QA (ver «TLS» abajo) y el archivo `secretos/qa/clave_cuentas_qa`:

```bash
python3 pruebas/extremo_a_extremo.py --base https://qa.redvital.local --ca redvital-qa-raiz.crt --secretos secretos/qa
```

Recorre el sistema solo por el borde (46 comprobaciones). Incluye el límite de 10 inicios de sesión por minuto: entre dos corridas, espera un minuto.

Para comprobar que ningún servicio espera a Kafka, en la VM de servicios (detiene el broker 60 s y lo vuelve a encender):

```bash
python3 pruebas/resiliencia_kafka.py --base https://qa.redvital.local --ca redvital-qa-raiz.crt --secretos secretos/qa
``` El firewall se comprueba desde una máquina que NO debería llegar (debe agotar el tiempo) y desde la que sí:

```bash
nc -zvw3 IP_VM_DATOS 5434
```

## QA

- `COMPOSE_PROFILES=semillas,metricas`. El perfil `semillas` aplica cuentas U3–U7, campañas y umbrales sintéticos. **Nunca en producción.**
- **Cuentas de prueba** (todas con la clave de `secretos/qa/clave_cuentas_qa`):

| Correo | Perfil | Jurisdicción |
|---|---|---|
| `operador@redvital.test` | U3 operador | Banco Aurora (Medellín) |
| `admin.banco@redvital.test` | U4 administrador de banco | Banco Aurora |
| `operador.ceiba@redvital.test` | U3 operador | Banco Ceiba (Bello) |
| `coordinador@redvital.test` | U5 coordinador | Antioquia |
| `admin.nacional@redvital.test` | U6 administrador nacional | Nacional |
| `auditor@redvital.test` | U7 auditor | Nacional |

  El donante (U2) se crea desde la propia web, en *Crear cuenta*.
- **TLS.** Con `TLS_MODO=internal`, Caddy firma con su CA local. Para que los navegadores de QA confíen en ella, distribuye su raíz:

```bash
docker compose -f vm-borde/docker-compose.yml --env-file ambientes/qa.env exec caddy cat /data/caddy/pki/authorities/local/root.crt > redvital-qa-raiz.crt
```

### Prueba en un solo equipo

Para probar el sistema completo en una máquina, todas las «VM» pueden ser el puente `docker0`: en `ambientes/qa.env` pon `IP_VM_*=172.17.0.1`, `DOMINIO=localhost` e `IP_PUBLICA_BORDE=127.0.0.1`, y abre `https://localhost`.

## Producción

`ambientes/produccion.env`: `ASPNETCORE_AMBIENTE=Production`, `COMPOSE_PROFILES=metricas` (sin semillas). Identidad solo publica la llave de `secretos/produccion/identidad_clave_firma`: un token firmado con la llave de pruebas no valida. Con dominio público, `TLS_MODO` es el correo de la cuenta ACME. El listener EXTERNO de Kafka usa `SASL_SSL` en producción, y Kafbat necesita el almacén de confianza de su CA.

## Pendiente conocido

- **Servicio Institucional:** no existe. Sin él no hay transferencias, bitácora ni nombres de banco, y U7 no tiene módulos.
- **Cierre de campaña:** el catálogo de eventos del DD no tiene un `campaign-closed`. Donación valida las campañas por fechas; se propone un EV-08 a la Arquitecta.
- **Registro de imágenes:** hoy cada VM construye. Con un registro, publicar `redvital/*:<VERSION>` y desplegar con `--no-build`.
