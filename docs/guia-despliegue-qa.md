# RedVital en QA — Guía del equipo

Cómo funciona el ambiente de QA, cómo se entra, cómo se despliega un cambio, cómo se verifica y con qué scripts se prueba cada cosa.

> **QA ya está montado**, desde el 2026-10-08: Docker instalado, secretos generados y firewall activo. Esta guía es para el trabajo de todos los días.
> Para montar VM nuevas (producción, o una VM de QA reemplazada) está [`preparacion-inicial-vm.md`](preparacion-inicial-vm.md).

**Contenido**
1. [Cómo funciona](#1-cómo-funciona)
2. [Cómo entrar](#2-cómo-entrar)
3. [Encender, apagar y ver el estado](#3-encender-apagar-y-ver-el-estado)
4. [Desplegar un cambio](#4-desplegar-un-cambio)
5. [Verificar que todo funciona](#5-verificar-que-todo-funciona)
6. [Scripts de prueba](#6-scripts-de-prueba)
7. [Consultas útiles a las bases](#7-consultas-útiles-a-las-bases)
8. [Secretos](#8-secretos)
9. [Problemas frecuentes](#9-problemas-frecuentes)
10. [Volver a dejar QA de fábrica](#10-volver-a-dejar-qa-de-fábrica)

---

## 1. Cómo funciona

### 1.1 Las cuatro VM

| VM | IP | Nombre | Qué corre | Para qué sirve |
|---|---|---|---|---|
| **Datos** | `10.43.99.183` | `NBIG242` | Tres PostgreSQL 16: Identidad (5432), Campañas (5433) y Donación (5434). Además, migraciones Flyway y semillas de prueba. | Guarda los datos. Una base por servicio: ningún servicio lee la base de otro. |
| **Servicios** | `10.43.99.174` | `NBIG233` | Identidad (8080), Campañas (8082), Donación (8083), Kafka (9094) y el trabajador de Notificaciones (sin puertos) | La lógica de negocio. Campañas y Donación solo se comunican por Kafka. Notificaciones envía los correos por Gmail. |
| **Borde** | `10.43.98.220` | `NBIG26` | Caddy (80/443), la aplicación web y APISIX | La única puerta pública. Caddy termina HTTPS y APISIX valida el token y el rol antes de pasar la petición. |
| **Tools** | `10.43.100.121` | `NBIG418` | Prometheus, Grafana y Kafbat | Monitoreo y lectura de Kafka. Solo escucha en `127.0.0.1` de esa VM. |

En todas, el usuario es `estudiante` y el despliegue vive en `~/Downloads/RedVital/Despliegue_RedVital`. Cada VM tiene solo los repos de su rol:

| VM | Repos en `~/Downloads/RedVital` |
|---|---|
| Datos | `Despliegue_RedVital`, `BDs_RedVital` |
| Servicios | `Despliegue_RedVital`, `Servicios_RedVital` |
| Borde | `Despliegue_RedVital`, `FrontEnd`, `Tools_RedVital` |
| Tools | `Despliegue_RedVital` |

**Cosas en las VM que no son de RedVital.** No las toques sin preguntar:
- **Servicios:** PostgreSQL, Grafana y Prometheus instalados en Ubuntu, y dos contenedores de un despliegue anterior (`redvital-qa-container` y `redvital-donacion-qa-container`).
- **Borde:** un `actions-runner` de GitHub Actions en `~/actions-runner`, que usa el mismo Docker. Además, contenedores `db-campana` y `db-identidad` que dejó una corrida del CI de bases.

Los contenedores de este despliegue siempre se llaman `redvital-qa-<rol>-<servicio>-1` o `redvital-tools-<servicio>-1`.

### 1.2 El recorrido de una petición

```
Navegador ──HTTPS 443──► Caddy (Borde)
                           ├─ /api/*  ──► APISIX ──valida token y rol──► servicio en 10.43.99.174
                           └─ lo demás ─► web (archivos estáticos de React)

Identidad / Campañas / Donación ──TLS──► su propia base en 10.43.99.183

Campañas ──EV-03 campaña publicada──► Kafka ──► Donación   (proyección local de campañas)
Donación ──EV-01 donación registrada──► Kafka ──► Campañas (contador de donaciones)
```

**Paso a paso:**

1. **El navegador** solo conoce `https://10.43.98.220`. Nunca habla con un servicio ni con una base.
2. **Caddy** termina HTTPS, añade las cabeceras de seguridad, limita el cuerpo a 1 MiB y reparte: `/api/*` va a APISIX y todo lo demás a la web.
3. **APISIX** comprueba el token: firma, emisor y vigencia, contra la llave pública de Identidad. También comprueba que el rol del token pueda usar esa ruta. Si no puede, responde 401 o 403 y le reporta la denegación a Identidad para la auditoría. Si puede, quita el prefijo `/api` y pasa la petición al servicio.
4. **El servicio** aplica sus propias reglas (de qué banco es la unidad, en qué estado está) y lee y escribe en su base por TLS.
5. **Los eventos** se guardan primero en una «bandeja de salida» dentro de la misma transacción del cambio. Un publicador los envía a Kafka después. Si Kafka está caído, la operación igual responde bien y el evento sale cuando Kafka vuelve.
6. **Los consumidores** son idempotentes: un evento repetido no se aplica dos veces. Ningún servicio le pregunta nada a otro por Kafka ni espera su respuesta: cada uno trabaja con su copia local (Donación valida la jornada con su proyección de campañas).
7. **Cuando un evento falla**, se distingue por qué:
   - **Falla el mensaje mismo:** se reintenta y, si sigue fallando, va a su tema `.dlt` (mensajes muertos). En Donación los reintentos van por los temas `.donacion.retry-0`, `-1` y `-2` (2, 10 y 50 s), así que los eventos que vienen detrás siguen pasando. En Campañas son tres reintentos rápidos (1, 2 y 4 s). Un mensaje mal formado va directo al `.dlt`.
   - **Falla una dependencia** (la base no responde): el evento no tiene la culpa. El consumidor lo vuelve a intentar cada 5 s hasta que la base vuelva y no lo manda al `.dlt`.
   - **Falla Kafka:** ningún servicio se cae ni deja de atender. Los consumidores se reconectan solos y los servicios arrancan aunque Kafka no esté listo.
8. **Los correos** los envía el trabajador de Notificaciones, nunca un servicio mientras atiende una petición. Al crear una cuenta, Donación deja un aviso `cuenta_creada` en su bandeja. El trabajador lo toma de Kafka, le pide a Donación el correo y el nombre del donante (el correo nunca viaja por Kafka) y envía la confirmación por Gmail. Si Gmail no responde, el aviso espera en Kafka y sale cuando vuelva; el registro nunca espera al correo.

### 1.3 Seguridad

| Capa | Qué la protege |
|---|---|
| Entrada | Solo Borde es pública, y solo en 80 y 443. HTTP redirige a HTTPS. Lleva HSTS, CSP `default-src 'self'` y `X-Frame-Options: DENY`. |
| API | Token JWT de 15 minutos, más una cookie de renovación `HttpOnly`. APISIX valida rol por ruta. `/api/internal/*` siempre da 404. |
| Límite de tasa | 60 peticiones por minuto en las rutas públicas y 10 por minuto en el inicio de sesión. |
| Red entre VM | El firewall (`redvital-firewall` en Datos, Servicios y Borde) abre cada puerto solo al origen que lo necesita y descarta lo demás. |
| Bases | TLS obligatorio. Cada servicio verifica la CA de la base y entra con un usuario de permisos mínimos. |
| Kafka | Usuario SCRAM por servicio, con ACL: cada uno solo escribe y lee sus temas. |
| Secretos | Archivos en `secretos/`, con permisos `0700`. Nunca van en git, en variables de entorno ni en las imágenes. |
| Contenedores | Sin root, sin capacidades, con sistema de archivos de solo lectura. |

**Quién puede conectarse a qué puerto**, según el firewall:

| Destino | Puertos | Solo desde |
|---|---|---|
| Borde `10.43.98.220` | 80, 443 | Cualquiera |
| Borde `10.43.98.220` | 9091, 9100, 9180 (métricas) | Tools |
| Servicios `10.43.99.174` | 8080, 8082, 8083 (API) | Borde |
| Servicios `10.43.99.174` | 9094 (Kafka), 9404, 8183, 9100, 9180 (métricas) | Tools |
| Datos `10.43.99.183` | 5432, 5433, 5434 (bases) | Servicios |
| Datos `10.43.99.183` | 9100, 9180 (métricas) | Tools |
| Cualquier VM | 22 (SSH) | Cualquiera en la red (no lo filtra el firewall de RedVital) |

---

## 2. Cómo entrar

### 2.1 Lo que necesita cada integrante

1. **Estar en una red que llegue a `10.43.96.0/20`**, por ejemplo la del campus. Para comprobarlo:
   ```bash
   nc -zvw3 10.43.98.220 443
   ```
   Debe decir `succeeded`.
2. **Confiar en la CA de QA en el navegador** (2.2). Sin esto, el navegador muestra una advertencia de certificado.
3. **Para operar** (desplegar, ver logs, entrar a Tools): acceso SSH a las VM con el usuario `estudiante`. Lo recomendable es instalar tu llave en vez de escribir la contraseña cada vez:
   ```bash
   ls ~/.ssh/id_ed25519.pub || ssh-keygen -t ed25519
   ```
   ```bash
   for ip in 10.43.99.183 10.43.99.174 10.43.98.220 10.43.100.121; do ssh-copy-id estudiante@$ip; done
   ```

### 2.2 Confiar en la CA de QA

QA usa HTTPS firmado por una CA propia de QA, que crea Caddy. Su certificado es público, no es un secreto. Bájalo a tu PC:

```bash
scp estudiante@10.43.98.220:Downloads/RedVital/Despliegue_RedVital/redvital-qa-raiz.crt ~/
```

Impórtalo una vez por navegador:
- **Firefox:** Configuración → Privacidad y seguridad → Certificados → Ver certificados → Autoridades → Importar. Marca «Confiar en esta CA para identificar sitios web».
- **Chrome o Brave:** Configuración → Privacidad y seguridad → Seguridad → Administrar certificados → «Instalados por ti» (o la pestaña «Autoridades») → Importar.

Para `curl`, usa `--cacert ~/redvital-qa-raiz.crt`. Si es solo una prueba rápida, `-k` también sirve.

### 2.3 URLs

| Qué | URL | Desde dónde | Para qué |
|---|---|---|---|
| **Aplicación web** | `https://10.43.98.220` | Cualquier PC de la red, con la CA importada | Usar RedVital como cualquier usuario |
| **API** | `https://10.43.98.220/api/v1/…` | Igual | Probar la API con `curl` o Postman. Es lo mismo que usa la web. |
| HTTP | `http://10.43.98.220` | Igual | Solo redirige a HTTPS |
| **Grafana** | `http://127.0.0.1:13000` | Tu PC, con el túnel a Tools (2.5) | Paneles de métricas |
| **Prometheus** | `http://127.0.0.1:19090/targets` | Tu PC, con el túnel | Ver si se están leyendo todas las métricas |
| **Kafbat** | `http://127.0.0.1:18080` | Tu PC, con el túnel | Ver temas, mensajes y grupos de Kafka, solo lectura |
| Salud de Identidad | `http://10.43.99.174:8080/salud` | Solo desde la VM de Servicios | Diagnóstico |
| Salud de Campañas | `http://10.43.99.174:8082/salud` | Solo desde la VM de Servicios | Diagnóstico |
| Salud de Donación | `http://10.43.99.174:8183/actuator/health/readiness` | Solo desde la VM de Servicios | Diagnóstico |
| Llave pública de Identidad | `http://10.43.99.174:8080/.well-known/jwks.json` | Solo desde la VM de Servicios | La que usa APISIX para validar tokens |
| Bases de datos | — | Solo con `docker compose exec` en la VM de Datos (sección 7) | Consultas de diagnóstico |

Las URL de la VM de Servicios no responden desde tu PC: el firewall solo deja entrar a Borde y a Tools. Es intencional.

### 2.4 Cuentas de prueba

Las seis cuentas sembradas **comparten una sola clave**. Para verla, desde tu PC:

```bash
ssh estudiante@10.43.99.174 cat Downloads/RedVital/Despliegue_RedVital/secretos/qa/clave_cuentas_qa
```

No la pegues en chats, tickets ni documentos.

| Rol | Correo | Nombre | Ámbito | Qué puede hacer en esta versión |
|---|---|---|---|---|
| **U1 Visitante** | — | — | Público | Ver jornadas, registrar su intención de donar en 3 pasos y consultarla por código |
| **U2 Donante** | El que crees en «Crear cuenta» | — | Su perfil | Perfil, elegibilidad, consentimientos, historial y reconocimientos |
| **U3 Operador** | `operador@redvital.test` | Andrés Mejía | Banco Aurora | Buscar donante, registrar donación, fraccionar, tamizar, reservar, despachar y desechar; ver inventario y alertas |
| **U3 Operador** (otro banco) | `operador.ceiba@redvital.test` | Lina Ospina | Banco Ceiba | Lo mismo, solo en su banco. Sirve para probar que no ve lo de Aurora. |
| **U4 Administrador de banco** | `admin.banco@redvital.test` | Catalina Duarte | Banco Aurora | Inventario, umbrales, atender alertas, ver unidades, y publicar y cerrar jornadas de su banco |
| **U5 Coordinador territorial** | `coordinador@redvital.test` | Jorge Salazar | Antioquia (05) | Ver campañas, solo lectura |
| **U6 Administrador nacional** | `admin.nacional@redvital.test` | Marcela Torres | Nacional | Ver campañas, solo lectura |
| **U7 Auditor** | `auditor@redvital.test` | Ricardo Peña | Nacional | Inicia sesión y ve el aviso de que aún no tiene módulos |

Las funciones de U5, U6 y U7 están limitadas porque dependen del Servicio Institucional, que aún no existe.

**Datos sembrados:**
- **Jornadas:** cinco. Cuatro publicadas o cerradas, entre ellas «Ciudad Universitaria» y «Parque de Bello», y una en borrador, «Centro Administrativo La Alpujarra».
- **Umbrales:** los de inventario del Banco Aurora.

**Para cuentas de donante nuevas,** usa un documento y un correo distintos cada vez: un documento repetido responde 409.

### 2.5 Entrar a Tools (Grafana, Prometheus y Kafbat)

Tools solo escucha dentro de su VM. Desde tu PC, abre un túnel SSH y déjalo corriendo en una terminal:

```bash
ssh -N -L 13000:127.0.0.1:3000 -L 19090:127.0.0.1:9090 -L 18080:127.0.0.1:8080 estudiante@10.43.100.121
```

Mientras esté abierto, usa las URL de 2.3. En Grafana y Kafbat el usuario es `admin`, y las claves se ven así:

```bash
ssh estudiante@10.43.100.121 cat Downloads/RedVital/Despliegue_RedVital/secretos/tools/grafana_admin
```

```bash
ssh estudiante@10.43.100.121 cat Downloads/RedVital/Despliegue_RedVital/secretos/tools/kafbat_admin
```

**Qué ver:**

| Herramienta | Qué mirar |
|---|---|
| Prometheus | En `Status → Targets`, 9 objetivos en `UP`: Donación, Kafka y APISIX, más los nodos y contenedores de Datos, Servicios y Borde. |
| Grafana | El origen de datos `Prometheus` ya viene configurado. En `Explore` puedes consultar, por ejemplo, `up{ambiente="qa"}`. |
| Kafbat | El clúster `qa` en `online`. En `Topics` están los temas `redvital.*`, y puedes ver los mensajes de cada evento. En `Consumers`, los grupos `donacion` y `campanias` deben tener retraso (lag) 0. Los mensajes de EV-02 (`notice-requested`) no se pueden leer desde Kafbat: llevan identificadores de donantes, y la ACL lo impide a propósito. |

### 2.6 Mapa de la API

Todas las rutas van con el prefijo `https://10.43.98.220/api/v1`. «Rol en APISIX» es el primer filtro; después, cada servicio aplica sus propias reglas, como el banco, el estado o la jurisdicción.

| Método | Ruta | Rol en APISIX | Para qué |
|---|---|---|---|
| POST | `/sesiones` | Público, 10 por minuto | Iniciar sesión: devuelve el token y deja la cookie de renovación |
| POST | `/sesiones/renovacion` | Público, con la cookie | Renovar el token |
| DELETE | `/sesiones/actual` | Cualquier cuenta | Cerrar sesión |
| GET | `/usuarios/me` | Cualquier cuenta | Datos del usuario y su rol |
| GET | `/campanias` | Público | Listado de jornadas |
| GET | `/campanias/{id}` | U2 a U6 | Detalle de una jornada |
| POST | `/campanias/{id}/publicacion`, `/campanias/{id}/cierre` | U4 | Publicar o cerrar una jornada; publicar emite el evento EV-03 |
| POST | `/intenciones` | Público | Registro anónimo de intención de donar |
| GET | `/intenciones/{codigo}` | Público | Consultar por código; el operador ve más datos |
| POST | `/donantes` | Público | Crear cuenta de donante |
| GET, PUT, POST | `/donantes/me`, `/donantes/me/…` | U2 | Perfil, elegibilidad, historial, reconocimientos y consentimientos |
| POST | `/donantes/busqueda` | U3 | Buscar donante por documento |
| POST | `/donaciones`, `/donaciones/{id}/fraccionamiento` | U3 | Registrar una donación y fraccionarla; registrar emite el evento EV-01 |
| GET | `/donaciones/{id}` | U3 a U6 | Detalle de una donación |
| GET | `/unidades`, `/unidades/{id}`, `/unidades/{id}/eventos` | U3 a U7 | Unidades y su recorrido |
| POST, DELETE | `/unidades/{id}/…` | U3 | Ingreso a tamizaje, tamizaje, reserva, despacho y disposición final |
| GET | `/inventario` | U3 a U6 | Inventario del banco |
| PUT | `/inventario/umbrales` | U4 | Configurar umbrales |
| GET | `/alertas` | U3 a U5 | Alertas de escasez y de vencimiento |
| POST | `/alertas/{id}/atencion` | U4 | Atender una alerta |

**Ejemplo con `curl`** desde tu PC. Primero guarda el token:

```bash
TOKEN=$(curl -s --cacert ~/redvital-qa-raiz.crt -H 'Content-Type: application/json' -d '{"correo":"operador@redvital.test","credencial":"'"$(ssh estudiante@10.43.99.174 cat Downloads/RedVital/Despliegue_RedVital/secretos/qa/clave_cuentas_qa)"'"}' https://10.43.98.220/api/v1/sesiones | python3 -c 'import json,sys; print(json.load(sys.stdin)["token_acceso"])')
```

Luego úsalo:

```bash
curl -s --cacert ~/redvital-qa-raiz.crt -H "Authorization: Bearer $TOKEN" https://10.43.98.220/api/v1/unidades | python3 -m json.tool | head -30
```

Toda respuesta trae la cabecera `X-Correlacion-Id`. Si reportas un error, inclúyela: con ella se encuentra la petición en los logs de los tres servicios.

---

## 3. Encender, apagar y ver el estado

Todos los comandos se ejecutan **en la VM indicada**, desde la carpeta del despliegue:

```bash
cd ~/Downloads/RedVital/Despliegue_RedVital
```

Para no repetir la parte larga, en cada VM puedes definir un atajo. Escribe en esa terminal la línea de su rol:

| VM | Atajo |
|---|---|
| Datos | `alias dc='docker compose -f vm-datos/docker-compose.yml --env-file ambientes/qa.env'` |
| Servicios | `alias dc='docker compose -f vm-servicios/docker-compose.yml --env-file ambientes/qa.env'` |
| Borde | `alias dc='docker compose -f vm-borde/docker-compose.yml --env-file ambientes/qa.env'` |
| Tools | `alias dc='docker compose -f vm-tools/docker-compose.yml'` |

Con eso, `dc ps` equivale al comando completo. Dura solo mientras esa terminal esté abierta. **Los comandos de esta guía están siempre completos**, así que funcionan sin el atajo.

### 3.1 Ver el estado

| VM | Comando | Qué debe mostrar |
|---|---|---|
| Datos | `docker compose -f vm-datos/docker-compose.yml --env-file ambientes/qa.env ps -a` | `db-identidad`, `db-campana` y `db-donacion` en `(healthy)`; `migracion-*`, `credenciales-identidad` y `semilla-*` en `Exited (0)`; `node-exporter` y `cadvisor` en `Up` |
| Servicios | `docker compose -f vm-servicios/docker-compose.yml --env-file ambientes/qa.env ps -a` | `identity-service`, `campaign-service`, `donation-service`, `notification-worker` y `kafka` en `(healthy)`; `kafka-init` en `Exited (0)` |
| Borde | `docker compose -f vm-borde/docker-compose.yml --env-file ambientes/qa.env ps` | `web` en `(healthy)`; `apisix` y `caddy` en `Up` |
| Tools | `docker compose -f vm-tools/docker-compose.yml ps` | `prometheus`, `grafana` y `kafbat` en `Up` |

Los `Exited (0)` son trabajos de una sola vez (migraciones, semillas, inicialización de Kafka) que terminaron bien. Un `Exited (1)` o un `Restarting` indica un problema.

### 3.2 Apagar y encender

**Apagar** sin perder datos, en el orden **Tools → Borde → Servicios → Datos**:

| VM | Comando |
|---|---|
| Tools | `docker compose -f vm-tools/docker-compose.yml down` |
| Borde | `docker compose -f vm-borde/docker-compose.yml --env-file ambientes/qa.env down` |
| Servicios | `docker compose -f vm-servicios/docker-compose.yml --env-file ambientes/qa.env down` |
| Datos | `docker compose -f vm-datos/docker-compose.yml --env-file ambientes/qa.env down` |

**Encender** en el orden inverso, **Datos → Servicios → Borde → Tools**, con el mismo comando cambiando `down` por `up -d`. El orden importa: los servicios necesitan las bases, y el borde necesita los servicios.

**Si una VM se reinicia,** los contenedores vuelven solos (`restart: unless-stopped`), y el firewall también. Si se reinicia Datos, los servicios se reconectan en uno o dos minutos.

### 3.3 Logs

| Quiero ver… | VM | Comando |
|---|---|---|
| Donación, en vivo | Servicios | `docker compose -f vm-servicios/docker-compose.yml --env-file ambientes/qa.env logs -f --tail 100 donation-service` |
| Identidad o Campañas | Servicios | El mismo, con `identity-service` o `campaign-service` |
| Correos enviados | Servicios | El mismo, con `notification-worker`. Cada aviso deja una línea `Aviso <id> (<tipo>): <resultado>`, sin el correo ni el nombre. |
| Kafka | Servicios | El mismo, con `kafka` |
| Peticiones de APISIX | Borde | `docker compose -f vm-borde/docker-compose.yml --env-file ambientes/qa.env logs -f --tail 100 apisix` |
| Caddy | Borde | El mismo, con `caddy` |
| Una base | Datos | `docker compose -f vm-datos/docker-compose.yml --env-file ambientes/qa.env logs --tail 100 db-donacion` |
| Todo lo de una petición | Servicios y Borde | Busca su correlación: `… logs donation-service \| grep <X-Correlacion-Id>` |

`Ctrl+C` sale del modo en vivo.

### 3.4 Reiniciar un solo servicio

```bash
docker compose -f vm-servicios/docker-compose.yml --env-file ambientes/qa.env restart donation-service
```

Cambia la VM, el archivo y el nombre según el caso. `restart` no recompila: para llevar código nuevo, ver la sección 4.

---

## 4. Desplegar un cambio

### 4.1 El flujo

1. **En tu PC**, ten los cinco repos actualizados en la misma carpeta, uno al lado del otro: `BDs_RedVital`, `Servicios_RedVital`, `FrontEnd`, `Tools_RedVital` y `Despliegue_RedVital`.
2. **Antes de desplegar**, pasa las pruebas del componente en tu PC (sección 6.2).
3. **Copia el código a las VM.** Desde `Despliegue_RedVital`, en tu PC:
   ```bash
   scripts/copiar-codigo.sh datos=10.43.99.183 servicios=10.43.99.174 borde=10.43.98.220 tools=10.43.100.121
   ```
   Puedes copiar solo a las VM afectadas, por ejemplo solo `servicios=10.43.99.174`.
4. **Recompila en la VM que corresponde** (tabla 4.2).
5. **Verifica** (sección 5).
6. **Avisa al equipo.** Que no desplieguen dos personas a la vez.

**Qué hace `copiar-codigo.sh`:**
- Usa `rsync` por SSH y le manda a cada VM solo los repos de su rol.
- Borra en la VM los archivos que ya no existen en tu PC.
- **Nunca** envía ni borra secretos, archivos `*.env`, la configuración generada de Tools, la CA ni los respaldos.
- No envía `node_modules`, `target`, `bin`, `obj` ni `dist`: cada VM compila dentro de Docker.

### 4.2 Qué recompilar según lo que cambió

Todos los comandos van en la VM indicada, desde `~/Downloads/RedVital/Despliegue_RedVital`.

| Cambió… | VM | Comando | Tarda |
|---|---|---|---|
| **Migración nueva** (`BDs_RedVital/*/migraciones`) | Datos | `docker compose -f vm-datos/docker-compose.yml --env-file ambientes/qa.env up -d` | Segundos |
| Donación (`services/donation`) | Servicios | `docker compose -f vm-servicios/docker-compose.yml --env-file ambientes/qa.env up -d --build donation-service` | 2 a 4 min |
| Identidad (`services/identity`) | Servicios | Lo mismo, con `identity-service` | 2 a 3 min |
| Campañas (`services/campaign`) | Servicios | Lo mismo, con `campaign-service` | 2 a 3 min |
| Notificaciones (`services/notifications`) | Servicios | Lo mismo, con `notification-worker` | 2 a 4 min |
| Catálogo de temas o ACL de Kafka (`Despliegue_RedVital/kafka`) | Servicios | `docker compose -f vm-servicios/docker-compose.yml --env-file ambientes/qa.env up -d --build kafka kafka-init` | 2 min |
| Front (`FrontEnd/frontend-main`) | Borde | `docker compose -f vm-borde/docker-compose.yml --env-file ambientes/qa.env up -d --build web` | 3 a 5 min |
| Rutas o plugin de APISIX (`Tools_RedVital/tools/APISIX-config`) | Borde | `docker compose -f vm-borde/docker-compose.yml --env-file ambientes/qa.env restart apisix` | Segundos |
| `Caddyfile` (`vm-borde/caddy`) | Borde | `docker compose -f vm-borde/docker-compose.yml --env-file ambientes/qa.env up -d --build caddy` | 1 min |
| Un `docker-compose.yml` | La de ese compose | `up -d --build` de esa VM, sin nombre de servicio | Varía |
| Configuración de Tools | Tools | `scripts/renderizar-tools.sh && docker compose -f vm-tools/docker-compose.yml up -d --force-recreate` | Segundos |

**Reglas:**
- **Las migraciones van primero.** Si una versión de un servicio necesita una tabla o columna nueva, despliega Datos antes que Servicios: el servicio valida el esquema al arrancar y no arranca si falta algo.
- **Las migraciones no se editan una vez aplicadas.** Flyway las verifica con una suma de control. Para corregir algo, crea una migración nueva (`V008__…`).
- **Un cambio en `qa.env`** debe hacerse igual en las cuatro VM. Si cambió una IP, reinstala además el firewall (sección 5.5).

### 4.3 Volver a la versión anterior

1. Vuelve el repo a la versión anterior en tu PC, con `git checkout` o `git revert`.
2. Repite 4.1.

Las migraciones no se deshacen solas. Si la versión nueva aplicó una migración, la anterior debe tolerar ese esquema; si no, hace falta una migración correctiva.

---

## 5. Verificar que todo funciona

Después de cada despliegue, haz al menos 5.1 y 5.2.

### 5.1 Verificación rápida (2 minutos)

1. **El estado de las VM tocadas** (3.1): nada en `Restarting`, `unhealthy` ni `Exited (1)`.
2. **La cadena completa, desde tu PC.** Pide la lista de jornadas a través de Caddy, APISIX, Campañas y la base:
   ```bash
   curl -s --cacert ~/redvital-qa-raiz.crt https://10.43.98.220/api/v1/campanias | head -c 200; echo
   ```
3. **La web:** `https://10.43.98.220` carga sin advertencia y deja iniciar sesión.

### 5.2 Prueba automática de extremo a extremo

> **Dónde:** VM de Servicios. Ahí están la CA y la clave de las cuentas.

```bash
cd ~/Downloads/RedVital/Despliegue_RedVital && python3 pruebas/extremo_a_extremo.py --base https://10.43.98.220 --ca redvital-qa-raiz.crt --secretos secretos/qa
```

Recorre QA solo por `https://10.43.98.220`, como un navegador, y deja datos de prueba nuevos (una cuenta de donante, una donación y sus unidades):

| Sección | Qué comprueba |
|---|---|
| 1. Borde y seguridad | HTTPS, CSP, HSTS, `X-Frame-Options`, sin cabecera `Server`, `/api/internal` en 404, tokens ausentes o alterados en 401, correlación en cada respuesta |
| 2. Visitante | Listado público, registro anónimo con código, y que el visitante no vea datos sensibles |
| 3. Donante | Crear cuenta, documento repetido en 409, iniciar sesión, perfil, elegibilidad, consentimientos, acceso prohibido a unidades, renovar, cerrar sesión y rechazo posterior |
| 4. Operador | Buscar donante, registrar donación con campaña confirmada por EV-03, fraccionar, tamizar, reglas 400, 409 y 422, desechar, recorrido de 5 eventos, y que otro banco no vea la unidad |
| 5. Kafka | EV-01 sube el contador de la jornada en Campañas; publicar el borrador; el administrador no puede registrar donaciones |
| 6. Límite de tasa | Tras 10 inicios de sesión en un minuto responde 429 |

**Qué esperar:**
- La **primera vez sobre una base nueva:** `46/46 comprobaciones correctas`.
- **Desde la segunda:** `45/45`. La jornada borrador ya quedó publicada, y la prueba lo indica con `[--  ]`.

**Entre dos corridas, espera un minuto.** Si no, la segunda choca con el límite de 10 inicios de sesión por minuto, que es intencional.

### 5.3 Recorrido manual por rol

En `https://10.43.98.220`, con la CA importada y la clave de 2.4:

| Rol | Recorrido | Qué debe pasar |
|---|---|---|
| Visitante | «Quiero donar», 3 pasos | Recibes un código. «Descargar imagen» baja un PNG con el código, su vigencia y la dirección de consulta; «Copiar código» lo copia con guiones. Al consultarlo, sale «pendiente», sin grupo ni municipio, y ofrece de nuevo descargarlo o copiarlo. |
| Donante | «Crear cuenta», 4 pasos, **con un correo tuyo real** | Quedas dentro, en «Mi perfil». Ves elegibilidad, consentimientos separados e historial vacío. En uno o dos minutos llega «Tu cuenta de donante en RedVital está lista», con el botón para entrar (revisa spam la primera vez). |
| Donante | «Cerrar sesión» | Vuelves a la portada. Al recargar una página privada, te pide iniciar sesión. |
| `operador@redvital.test` | Buscar al donante que creaste, registrar una donación en «Ciudad Universitaria», fraccionarla y llevar una unidad hasta «despachada» | Cada acción muestra el estado nuevo y el recorrido de eventos de la unidad. |
| `operador.ceiba@redvital.test` | Buscar la unidad anterior | No la encuentra: es de otro banco. |
| `admin.banco@redvital.test` | Abrir la jornada «Ciudad Universitaria» | El contador de donaciones subió: es el evento EV-01, que viajó de Donación a Campañas. |
| `admin.banco@redvital.test` | Publicar «Centro Administrativo La Alpujarra» | Aparece como publicada; el visitante ya la ve. |
| `admin.banco@redvital.test` | Configurar umbrales y atender una alerta | Se guardan, y la alerta pasa a atendida. |
| `coordinador@` / `admin.nacional@` | Iniciar sesión | Solo ven campañas, en lectura. |
| `auditor@redvital.test` | Iniciar sesión | Ve el aviso de que aún no hay módulos. |

En las herramientas del navegador (`F12` → Red), todas las peticiones deben ir a `https://10.43.98.220/api/v1/…`, nunca a otra IP ni a otro puerto.

### 5.4 Kafka

| Qué | Cómo | Qué esperar |
|---|---|---|
| El broker y los temas | Kafbat (2.5) → clúster `qa` → `Topics` | `online`, con los temas `redvital.donacion.donation-completed.v1`, `redvital.campanias.campaign-published.v1`, `redvital.donacion.notice-requested.v1`, sus `.dlt` y los tres `…campaign-published.v1.donacion.retry-N` |
| Los consumidores al día | Kafbat → `Consumers` | Grupos `donacion`, `campanias`, `donacion.donacion.retry-0..2` y `donacion.donacion.dlt` con retraso (lag) 0 |
| EV-03 llegó a Donación | Consulta 7.2 «proyección» | Las jornadas publicadas están en `campania_publicada` |
| EV-01 llegó a Campañas | Consulta 7.2 «contador» | `donaciones_registradas` sube con cada donación registrada en esa jornada |
| No quedan eventos atascados | Consulta 7.2 «bandeja» | 0 pendientes en las dos bandejas |
| Ningún evento falló | Kafbat → temas `.dlt`; en Prometheus, `redvital_eventos_mensajes_muertos_total` | Sin mensajes; la métrica en 0 |
| Kafka caído no detiene nada | `pruebas/resiliencia_kafka.py` (6.4) | Todas las comprobaciones correctas |

### 5.5 Seguridad

**El firewall está activo**, en Datos, Servicios y Borde:

```bash
sudo iptables -L REDVITAL -n | head -5; systemctl is-active redvital-firewall
```

Debe listar reglas `RETURN` y `DROP`, y decir `active`. Para ver qué permite cada VM sin aplicar nada: `scripts/firewall.sh qa <rol> --simular`, con `<rol>` = `datos`, `servicios` o `borde`.

**El firewall filtra de verdad:**

| Desde | Comando | Qué esperar |
|---|---|---|
| Tu PC | `nc -zvw3 10.43.99.183 5434` | `timed out` |
| Tu PC | `nc -zvw3 10.43.99.174 8083` | `timed out` |
| Tu PC | `nc -zvw3 10.43.98.220 443` | `succeeded` |
| VM de Servicios | `nc -zv 10.43.99.183 5434` | `succeeded` |
| VM de Borde | `nc -zv 10.43.99.174 8083` | `succeeded` |
| VM de Borde | `nc -zvw3 10.43.99.183 5434` | `timed out` |
| VM de Tools | `nc -zv 10.43.99.183 9100` | `succeeded` |

**Reinstalar el firewall** si cambió una IP en `qa.env`. En la VM afectada, con su rol:

```bash
sudo scripts/firewall.sh qa datos --instalar
```

**Ver las denegaciones auditadas:** consulta 7.2 «auditoría». Cada 401 o 403 de APISIX deja una fila.

**QA rechaza tokens firmados con la llave de prueba de desarrollo:** script `t-303.3` (6.3).

### 5.6 Monitoreo

En Prometheus (`http://127.0.0.1:19090/targets`, con el túnel abierto), los 9 objetivos deben estar `UP`.

Si uno está `DOWN`, la columna `Error` dice por qué. Las causas habituales:
- el contenedor de métricas de esa VM está caído (3.1);
- el firewall de esa VM no deja entrar a Tools (`10.43.100.121`).

---

## 6. Scripts de prueba

### 6.1 Resumen

| Script | Dónde se corre | Contra qué | Qué prueba | Tarda |
|---|---|---|---|---|
| `Despliegue_RedVital/pruebas/extremo_a_extremo.py` | VM de Servicios | QA desplegado | Todo el sistema por el borde, 46 comprobaciones (5.2) | 30 s |
| `Despliegue_RedVital/pruebas/resiliencia_kafka.py` | VM de Servicios | QA desplegado | Con Kafka detenido, todo sigue atendiendo sin demora; al volver, los eventos pendientes se entregan (6.4) | 2 min |
| `Despliegue_RedVital/pruebas/correr-pruebas.sh` | Tu PC, con Docker | Nada desplegado: levanta contenedores efímeros | Pruebas unitarias y de integración de todos los componentes (6.2) | 3 min con caché; la primera vez, hasta 20 |
| `BDs_RedVital/databases/pruebas/correr-local.sh` | Tu PC | Bases efímeras | Lo mismo que el CI de bases: migraciones, esquemas, aislamiento y permisos | 20 s |
| `Tools_RedVital/tools/APISIX-config/pruebas/correr-local.sh` | Tu PC | APISIX efímero con servicios simulados | Lo mismo que el CI de APISIX: configuración, arranque y auditoría de un 401 (T-305.4) | 10 s |
| `Tools_RedVital/tools/APISIX-config/pruebas/t-303.3-qa-rechaza-llave-prueba.sh` | Tu PC | QA desplegado | Un token firmado con la llave de desarrollo es rechazado (401) | 1 min |
| `Despliegue_RedVital/scripts/firewall.sh qa <rol> --simular` | La VM de ese rol | Nada | Imprime las reglas que aplicaría el firewall | Segundos |

`t-305.6-gateway.sh`, en la misma carpeta de APISIX, es para el gateway en modo Development con tokens de prueba. **No sirve contra QA.**

### 6.2 Pruebas de los componentes en tu PC: `correr-pruebas.sh`

Corre en contenedores, así que no necesitas Java, .NET ni Node instalados. Solo Docker. Desde `Despliegue_RedVital`:

```bash
pruebas/correr-pruebas.sh
```

O solo algunos componentes:

```bash
pruebas/correr-pruebas.sh donacion web
```

| Componente | Qué corre |
|---|---|
| `bds` | `BDs_RedVital/databases/pruebas/correr-local.sh`: migraciones de las tres bases, esquemas, aislamiento y permisos por columna |
| `identidad` | `dotnet test` de Identidad |
| `campanas` | `dotnet test` de Campañas, con PostgreSQL y Kafka reales (Testcontainers) y las migraciones del repo de bases: el consumidor no se cae sin tema de mensajes muertos ni con la base caída |
| `donacion` | `mvn verify` de Donación: unitarias, reglas de arquitectura (ArchUnit) e integración con PostgreSQL y Kafka reales (Testcontainers), incluidos los reintentos sin detener la partición, la base caída y los mensajes muertos |
| `web` | Tipos de TypeScript y compilación de producción del front. El front aún no tiene pruebas automáticas. |
| `gateway` | `APISIX-config/pruebas/correr-local.sh` |
| `contratos` | Valida los contratos AsyncAPI de los eventos de Donación y Campañas |
| `secretos` | `gitleaks` sobre los archivos que entrarían en un commit y sobre el historial de git: ningún secreto versionado |

Al final imprime un resumen con `OK` o `FALLA` por componente. Si algo falla, muestra las últimas 40 líneas de ese componente. Con todo en verde termina con `Todo en verde.`

**Requisitos:**
- Los cinco repos, uno al lado del otro.
- Los puertos `127.0.0.1:9080` y `127.0.0.1:8081` libres, que los usa `gateway`.

### 6.3 QA rechaza la llave de prueba (T-303.3)

En desarrollo, los tokens se pueden firmar con una llave de prueba. QA debe rechazarlos: solo acepta los firmados por su propia llave.

Desde tu PC, en la carpeta que contiene los repos:

1. **Emite un token** con la llave de prueba, en un contenedor del SDK de .NET:
   ```bash
   export TOKEN_PRUEBA=$(docker run --rm -v "$PWD/Servicios_RedVital/services/identity/herramientas/LlaveDePrueba:/fuente:ro" mcr.microsoft.com/dotnet/sdk:10.0 sh -c 'cp -r /fuente /tmp/h && rm -rf /tmp/h/obj /tmp/h/bin && cd /tmp/h && dotnet run -v q -- generar-llave >/dev/null 2>&1 && dotnet run -v q -- emitir-token --perfil operador 2>/dev/null' | tail -1)
   ```
2. **Prueba contra QA:**
   ```bash
   GATEWAY_QA=https://10.43.98.220 bash Tools_RedVital/tools/APISIX-config/pruebas/t-303.3-qa-rechaza-llave-prueba.sh
   ```

**Qué esperar:** `OK — QA rechaza la llave de prueba (401)`.

### 6.4 Kafka caído: `resiliencia_kafka.py`

Comprueba que ningún servicio espera al broker. En la VM de Servicios, desde `~/Downloads/RedVital/Despliegue_RedVital`:

```bash
python3 pruebas/resiliencia_kafka.py --base https://10.43.98.220 --ca redvital-qa-raiz.crt --secretos secretos/qa
```

**Qué hace:**
1. Detiene el contenedor de Kafka durante 60 s (`--segundos-caido` para cambiarlo).
2. Mientras tanto, por el borde, registra una intención anónima, crea un donante y le registra una donación con campaña. Comprueba que cada respuesta llega en menos de 3 s, que los tres servicios y el trabajador de Notificaciones siguen sanos y sin reinicios, y que el evento queda esperando en la bandeja.
3. Enciende Kafka y comprueba que la donación llega sola a Campañas (EV-01).

**Qué esperar:** `18/18 comprobaciones correctas`. El script deja Kafka encendido aunque algo falle.

Necesita una campaña vigente del Banco Aurora. Las de la semilla vencen unos días después de sembrar; si el script dice que no hay ninguna, vuelve a sembrar (sección 10).

### 6.5 Qué correr según lo que cambiaste

| Cambiaste… | Antes de desplegar (tu PC) | Después de desplegar (QA) |
|---|---|---|
| Un servicio | `correr-pruebas.sh <componente> contratos` | 5.1, 5.2 y el recorrido 5.3 del rol afectado |
| Una migración | `correr-pruebas.sh bds` y los servicios que usan esa base | 5.1 y 5.2 |
| El front | `correr-pruebas.sh web` | 5.1 y 5.3 |
| Rutas de APISIX | `correr-pruebas.sh gateway` | 5.2 y 5.5 |
| Eventos o temas de Kafka | `correr-pruebas.sh contratos donacion campanas` | 5.2, 5.4 y 6.4 |
| Firewall o `qa.env` | `firewall.sh … --simular` | 5.5 y 5.6 |
| Antes de cualquier commit | `correr-pruebas.sh secretos` | — |

---

## 7. Consultas útiles a las bases

### 7.1 Cómo entrar a una base

En la VM de Datos, desde `~/Downloads/RedVital/Despliegue_RedVital`, abre una consola SQL de la base que necesites:

| Base | Comando |
|---|---|
| Identidad | `docker compose -f vm-datos/docker-compose.yml --env-file ambientes/qa.env exec -u postgres db-identidad psql -d db_identidad` |
| Campañas | `docker compose -f vm-datos/docker-compose.yml --env-file ambientes/qa.env exec -u postgres db-campana psql -d db_campana` |
| Donación | `docker compose -f vm-datos/docker-compose.yml --env-file ambientes/qa.env exec -u postgres db-donacion psql -d db_donacion` |

`\q` sale. Entras como administrador de la base: **solo lee**. Los datos se cambian a través de la aplicación o con una migración, nunca a mano.

### 7.2 Consultas

| Para qué | Base | Consulta |
|---|---|---|
| Cuentas | Identidad | `select correo, activo from usuario order by correo;` |
| **Auditoría**: últimas denegaciones | Identidad | `select ocurrido_en, rol, operacion, recurso_tipo, correlacion_id from registro_auditoria_ident where resultado = 'denegado' order by ocurrido_en desc limit 10;` |
| **Contador** de donaciones por jornada (EV-01) | Campañas | `select nombre, estado, donaciones_registradas from campania order by nombre;` |
| **Bandeja** de Campañas | Campañas | `select estado, count(*) from evento_salida group by estado;` |
| **Proyección** de jornadas en Donación (EV-03) | Donación | `select nombre from campania_publicada order by nombre;` |
| **Bandeja** de Donación | Donación | `select estado, count(*) from evento_salida group by estado;` |
| Unidades por estado | Donación | `select e.codigo, count(*) from unidad u join estado_unidad e on e.id = u.estado_id group by e.codigo;` |
| Eventos aplicados (consumidor) | Donación o Campañas | `select count(*) from evento_procesado;` |

En las dos bandejas, lo normal es que todo esté `publicado`. Filas `pendiente` que no bajan en un minuto indican que el servicio no llega a Kafka: revisa sus logs.

**Respaldo** de una base, en la VM de Datos:

```bash
docker compose -f vm-datos/docker-compose.yml --env-file ambientes/qa.env exec -T -u postgres db-donacion pg_dump -Fc db_donacion > respaldo-donacion-$(date +%F).dump
```

---

## 8. Secretos

Los secretos se generaron una vez en la VM de Servicios. Cada VM guarda solo los suyos, en `Despliegue_RedVital/secretos/`:

| VM | Guarda |
|---|---|
| Datos | Claves de las bases, certificado TLS de PostgreSQL, huellas de credenciales y semilla de cuentas |
| Servicios | Todo lo de los servicios y Kafka, más la llave de la CA de las bases y `clave_cuentas_qa` (solo aquí) |
| Borde | `gateway-secreto-cliente`: la credencial de APISIX ante Identidad |
| Tools | `kafka_inspeccion` (lectura de Kafka), `grafana_admin` y `kafbat_admin` |

En Servicios también vive `notificaciones_smtp_clave`, la contraseña de aplicación de Gmail. Es el único secreto que no genera el script: lo escribe a mano quien administra la cuenta de correo (8.2).

**Reglas:**
- Nunca los pegues en chats, tickets, documentos ni capturas. Tampoco la contraseña de `estudiante`: revisa la salida antes de pegar algo que incluya un `sudo`.
- Nunca los subas a git. `.gitignore` excluye `secretos/` y los `*.env`, y `correr-pruebas.sh secretos` lo comprueba.
- Si uno se expone, rótalo.

**Rotación:**

| Secreto | Cómo |
|---|---|
| Clave de usuario de una base | En Datos: `ALTER ROLE <usuario> PASSWORD '…'` en una consola (7.1). Actualiza el archivo en Datos y en Servicios y reinicia el servicio. |
| Contraseña de Gmail | En la cuenta de Google: revoca la contraseña de aplicación y crea otra. En Servicios: escríbela en `secretos/qa/notificaciones_smtp_clave` y ejecuta `up -d --force-recreate notification-worker`. |
| Usuario de Kafka | Actualiza el archivo en Servicios, ejecuta `up -d kafka-init` y reinicia el servicio. Si es `kafka_inspeccion`, cópialo también a Tools y ejecuta `scripts/renderizar-tools.sh`. |
| Grafana o Kafbat | En Tools: borra el archivo, ejecuta `scripts/generar-secretos.sh tools` y recrea el contenedor. |
| Clave de las cuentas de QA | Ver 8.1. |
| Llave de firma de Identidad | Pendiente de automatizar. Hoy: borrar la fila anterior de `llave_firma` y reiniciar Identidad. |
| `donacion_clave_hmac` | **No se rota**: se perderían las búsquedas por documento. |

### 8.1 Cambiar la clave de las cuentas de prueba

Hazlo si la clave se expuso (en un chat, una captura o una foto) o cuando alguien deja el equipo.

1. **En la VM de Servicios**, desde `~/Downloads/RedVital/Despliegue_RedVital`, borra la clave y la semilla, y genera las nuevas. Solo se regenera lo que falta; los demás secretos no cambian.
   ```bash
   rm secretos/qa/clave_cuentas_qa secretos/qa/semilla_identidad.sql && scripts/generar-secretos.sh qa
   ```
2. **Lleva la semilla nueva a la VM de Datos.** Desde la misma carpeta de Servicios:
   ```bash
   scp secretos/qa/semilla_identidad.sql estudiante@10.43.99.183:Downloads/RedVital/Despliegue_RedVital/secretos/qa/
   ```
3. **En la VM de Datos**, aplícala. La semilla actualiza la clave de las seis cuentas sin tocar nada más:
   ```bash
   docker compose -f vm-datos/docker-compose.yml --env-file ambientes/qa.env up -d --force-recreate --no-deps semilla-identidad
   ```
   ```bash
   docker compose -f vm-datos/docker-compose.yml --env-file ambientes/qa.env logs semilla-identidad | tail -1
   ```
   Debe decir `Cuentas sintéticas de QA aplicadas`.
4. **Comprueba:** la clave vieja ya no entra y la nueva sí. La nueva se ve en Servicios con `cat secretos/qa/clave_cuentas_qa`. Avisa al equipo.

Las sesiones que ya estaban abiertas siguen vivas hasta que venza su token (15 minutos) o su renovación.

---

### 8.2 Correo de los avisos (Gmail)

Los correos salen de una cuenta de Gmail del proyecto. Se configura una vez; después solo se rota la contraseña.

**1. La cuenta.** En una cuenta de Gmail creada para RedVital (no la personal de nadie):
1. Activa la verificación en dos pasos: «Cuenta de Google» → «Seguridad» → «Verificación en dos pasos».
2. Crea una contraseña de aplicación en `https://myaccount.google.com/apppasswords`, con el nombre `RedVital QA`. Google la muestra una sola vez, en cuatro grupos de letras.

**2. La red.** En la VM de Servicios, comprueba que se puede salir a Gmail:
```bash
nc -zvw5 smtp.gmail.com 587
```
Debe decir `succeeded`. Si se queda esperando, la red del laboratorio bloquea el puerto 587: hay que pedir a TI que lo abra hacia `smtp.gmail.com`.

**3. La configuración, en Servicios.** Desde `~/Downloads/RedVital/Despliegue_RedVital`:
1. Escribe la contraseña en el archivo del secreto, sin mostrarla en pantalla (pégala y presiona `Enter`). Los espacios no importan:
   ```bash
   read -rs CLAVE && printf '%s' "$CLAVE" > secretos/qa/notificaciones_smtp_clave && unset CLAVE
   ```
2. En `ambientes/qa.env`, agrega la cuenta. Por ejemplo, `SMTP_USUARIO=redvital.qa@gmail.com`.
3. Recrea el trabajador:
   ```bash
   docker compose -f vm-servicios/docker-compose.yml --env-file ambientes/qa.env up -d --force-recreate notification-worker
   ```

**4. Comprobar.** Crea una cuenta de donante en la web con un correo tuyo real (5.3). En los logs del trabajador (3.3) debe aparecer `Aviso … (cuenta_creada): entregado`, y el correo debe llegar en uno o dos minutos.

**Qué recibe el donante:** un correo de «RedVital» con el asunto «Tu cuenta de donante en RedVital está lista». Trae su nombre, el botón «Entrar a RedVital» con el enlace a `https://10.43.98.220/ingresar`, lo que puede hacer en su cuenta y el enlace a las jornadas. No lleva el documento, el teléfono ni la contraseña. A los donantes que autorizaron avisos también les llega un correo cuando se publica una jornada en su municipio.

**Nunca se envía a** los dominios reservados `.test`, `.example`, `.invalid` ni `.localhost`. Por eso las cuentas de prueba (`@redvital.test`) y la prueba de extremo a extremo no gastan el cupo diario de Gmail (del orden de 500 correos) ni generan rebotes: el log dice `dominio_reservado`.

## 9. Problemas frecuentes

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| El navegador advierte del certificado | CA no importada, o Caddy creó una nueva porque se borró su volumen | 2.2. Si se recreó, vuelve a bajar la CA y a copiarla a la VM de Servicios. |
| `429` | Límite de tasa | Espera un minuto. |
| El inicio de sesión funciona, pero luego todo da 401 | Relojes desincronizados entre VM, o APISIX no llega a Identidad | `timedatectl` en cada VM (`System clock synchronized: yes`); `nc -zv 10.43.99.174 8080` desde Borde |
| `/api/…` responde 502 o 504 | APISIX no alcanza un servicio | Estado de Servicios (3.1); `nc -zv 10.43.99.174 8083` desde Borde |
| Un servicio en `Restarting` | Error al arrancar | `logs --tail 80 <servicio>` (3.3). Las causas habituales están en las filas siguientes. |
| `password authentication failed` | La base y el servicio tienen secretos de generaciones distintas | Recupera los secretos de la misma generación en las dos VM; si no se puede, sección 10. |
| `certificate verify failed` o `remote certificate is invalid` | La CA de la base no coincide entre Datos y Servicios | Igual que la fila anterior. |
| Donación no arranca: `Schema-validation: missing column` | Falta la migración en Datos | Despliega Datos primero (4.2). |
| `migracion-*` en `Exited (1)`: `checksum mismatch` | Se editó una migración ya aplicada | Revierte ese cambio y crea una migración nueva. |
| Pendientes en la bandeja que no bajan | El servicio no llega a Kafka | Estado de `kafka` y logs del servicio. El evento sale solo cuando Kafka vuelve. |
| Mensajes en un tema `.dlt` | El mensaje mismo no se pudo procesar tras los reintentos, o no cumple el contrato. Una base caída no manda nada ahí: ese evento espera. | Ver el mensaje en Kafbat (cabeceras `kafka_dlt-exception-message` en Donación, `dlt-motivo` en Campañas) y los logs del consumidor. Corregido el defecto, se puede volver a publicar en el tema original: los consumidores descartan lo que ya aplicaron. |
| Logs de Campañas o Donación con «La base no responde; el evento … espera» | La base de ese servicio no responde | Revisa la VM de Datos. El evento se aplica solo cuando la base vuelve. |
| No llega el correo de confirmación; el log del trabajador dice `canal_no_disponible` | Gmail rechaza la credencial («revisa la contraseña de aplicación»), falta `SMTP_USUARIO`, o la red bloquea el puerto 587 | 8.2, pasos 2 y 3. Los avisos esperan en Kafka y salen solos cuando se corrige: no hay que repetir el registro. |
| El log dice `destinatario_sin_contacto` | El donante no tiene correo de contacto, o el aviso exige una autorización que no dio (los de jornadas) | Es normal: no es un fallo. |
| El correo llega a spam | Gmail aún no conoce al remitente | Márcalo como «No es spam». |
| La compilación termina con `Killed` o `exit code 137` | Falta memoria durante la compilación | Repite. Si persiste, compila un servicio a la vez. |
| `no space left on device` | Disco lleno | `docker system df`; `docker image prune` |
| `required variable … is missing` | Falta `--env-file ambientes/qa.env`, o no estás en `Despliegue_RedVital` | Ejecuta desde ahí, con los dos parámetros. |
| `copiar-codigo.sh` pide contraseña o falla | Tu llave SSH no está en esa VM | `ssh-copy-id estudiante@<ip>` (2.1) |
| Un objetivo `DOWN` en Prometheus | Exportador caído, o firewall | 5.6 |
| Lento mientras corre un CI de GitHub | El runner de Borde comparte Docker y CPU | Espera a que termine. |

**Si nada funciona:** apaga y enciende todo en orden (3.2). Luego corre 5.1 y 5.2.

---

## 10. Volver a dejar QA de fábrica

**Borra todos los datos de QA:** cuentas creadas, donaciones, eventos de Kafka y la CA de Caddy. Avisa al equipo antes.

1. **Borrar contenedores y volúmenes,** en el orden **Tools → Borde → Servicios → Datos**, cada uno en su VM:

   | VM | Comando |
   |---|---|
   | Tools | `docker compose -f vm-tools/docker-compose.yml down -v` |
   | Borde | `docker compose -f vm-borde/docker-compose.yml --env-file ambientes/qa.env down -v` |
   | Servicios | `docker compose -f vm-servicios/docker-compose.yml --env-file ambientes/qa.env down -v` |
   | Datos | `docker compose -f vm-datos/docker-compose.yml --env-file ambientes/qa.env down -v` |

2. **Levantar de nuevo,** en el orden **Datos → Servicios → Borde → Tools**:

   | VM | Comando |
   |---|---|
   | Datos | `docker compose -f vm-datos/docker-compose.yml --env-file ambientes/qa.env up -d` |
   | Servicios | `docker compose -f vm-servicios/docker-compose.yml --env-file ambientes/qa.env up -d` |
   | Borde | `docker compose -f vm-borde/docker-compose.yml --env-file ambientes/qa.env up -d` |
   | Tools | `docker compose -f vm-tools/docker-compose.yml up -d` |

   Las bases se crean de nuevo con las mismas claves (los secretos no se tocan), las migraciones y las semillas. Kafka recrea sus temas.

3. **Repartir la CA nueva de Caddy.** En Borde, extráela:
   ```bash
   docker compose -f vm-borde/docker-compose.yml --env-file ambientes/qa.env exec caddy cat /data/caddy/pki/authorities/local/root.crt > redvital-qa-raiz.crt
   ```
   En Servicios, tráela:
   ```bash
   scp estudiante@10.43.98.220:Downloads/RedVital/Despliegue_RedVital/redvital-qa-raiz.crt ~/Downloads/RedVital/Despliegue_RedVital/
   ```
   Luego cada integrante la importa de nuevo en su navegador (2.2).

4. **Verificar:** 5.2 debe dar 46/46.

**Para regenerar también todos los secretos** (por ejemplo, si se expusieron), sigue los pasos 2.3 a 3.4 de [`preparacion-inicial-vm.md`](preparacion-inicial-vm.md) y luego esta sección.
