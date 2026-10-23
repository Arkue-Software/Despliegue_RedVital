# Preparación inicial de las VM (se hace una sola vez)

> **QA ya pasó por esto** el 2026-10-08. Este documento queda para montar VM nuevas: producción, o una VM de QA reemplazada.
> Para operar, desplegar y probar QA día a día, usa [`guia-despliegue-qa.md`](guia-despliegue-qa.md).

Esta guía despliega RedVital en las **cuatro VM de QA** del laboratorio. Parte de VM con Ubuntu recién entregadas, tengan o no Docker. Cada paso dice:

- **dónde** se ejecuta;
- **qué** hacer y **para qué** sirve;
- **qué esperar** antes de seguir;
- **qué hacer** si sale distinto.

---

## 0. Antes de empezar

### 0.1 Las máquinas

| Máquina | IP | Qué corre | Qué guarda en `~/Downloads/RedVital` |
|---|---|---|---|
| **VM de Datos** | `10.43.99.183` | Las tres PostgreSQL (Identidad, Campañas, Donación), sus migraciones y los datos de prueba | `Despliegue_RedVital`, `BDs_RedVital` |
| **VM de Servicios** | `10.43.99.174` | Identidad, Campañas, Donación y Kafka. Aquí también se generan los secretos y se corre la prueba automática. | `Despliegue_RedVital`, `Servicios_RedVital` |
| **VM de Borde** | `10.43.98.220` | Caddy (la entrada HTTPS), la aplicación web y APISIX | `Despliegue_RedVital`, `FrontEnd`, `Tools_RedVital` |
| **VM de Tools** | `10.43.100.121` | Prometheus, Grafana y Kafbat (monitoreo) | `Despliegue_RedVital` |
| **Tu PC** | la de tu red | Copia el código a las VM, abre la web y entra a Tools | los cinco repos, en `~/Descargas/RedVital` |

Desde tu PC ya se comprobó que las cuatro VM responden por SSH (puerto 22). Por eso el código se copia desde tu PC (paso 2) y Tools se puede ver desde tu navegador (paso 7.3).

### 0.2 Quién habla con quién

```
 Navegador ──── 443 ────► Borde      10.43.98.220
 Borde ── 8080/8082/8083 ► Servicios 10.43.99.174
 Servicios ─ 5432/5433/5434 (TLS) ─► Datos 10.43.99.183
 Tools 10.43.100.121 ── 9100/9180 (métricas) ─► Datos, Servicios y Borde
                     ── 9091 (APISIX) ────────► Borde
                     ── 8183, 9404, 9094 ─────► Servicios (Donación, Kafka)
```

Son exactamente las conexiones que el firewall deja abiertas en el paso 9. Todo lo demás queda cerrado.

### 0.3 Qué es la «IP privada»

Cada VM tiene varias direcciones. `ip addr` las muestra todas:

| Interfaz | Ejemplo | ¿Se usa? |
|---|---|---|
| `lo` | `127.0.0.1` | No: es la VM hablando consigo misma. |
| **`ens33`** | **`10.43.98.220/20`** | **Sí. Es la IP privada de la VM.** |
| `docker0`, `br-…` | `172.17.0.1`, `172.18.0.1` | No: son redes internas de Docker, invisibles para las otras VM. |

- **«Privada»:** pertenece a la red interna del laboratorio. Las direcciones `10.x.x.x` no salen a internet.
- **Misma red:** las cuatro VM están en `10.43.96.0/20`, que va de `10.43.96.0` a `10.43.111.255`, así que se alcanzan directamente.
- **«Fija»:** si la línea de `ens33` dice `valid_lft forever` y **no** dice `dynamic`, la IP no cambia al reiniciar.

Si una VM mostrara `dynamic`, pide al administrador del laboratorio que reserve esa IP. Si cambia, hay que rehacer el certificado de la base y el firewall.

**Estas IP son las que van en `qa.env`, en el firewall y en el certificado de la base.**

### 0.4 Convenciones

- **Dónde:** cada paso empieza diciendo en qué máquina se ejecuta.
- **Abrir una terminal en una VM:** en la sesión de escritorio remoto de esa VM, `Ctrl+Alt+T`. También puedes entrar desde la terminal de tu PC con `ssh estudiante@10.43.99.183` (o la IP que toque); así copias y pegas más fácil.
- **Carpeta de trabajo:** casi todo se ejecuta desde `~/Downloads/RedVital/Despliegue_RedVital`. Cuando un bloque empieza con `cd`, ejecútalo tal cual.
- **Comandos multilínea:** un bloque de varias líneas se pega completo y se ejecuta con una sola tecla Enter.
- **Ángulos:** esta guía ya trae tus IP. Si algún comando tuviera `<algo>` entre ángulos, reemplázalo entero: bash lee `<` y `>` como redirecciones y da `syntax error`.
- **`sudo`:** pide la contraseña de `estudiante` en esa VM. Mientras la escribes no se ven caracteres; es normal.

### 0.5 Orden general

| Paso | Dónde | Qué |
|---|---|---|
| 1 | Las 4 VM | Preparar: herramientas, red, internet, reloj, **Docker**, recursos |
| 2 | Tu PC → las 4 VM | Copiar el código actualizado |
| 3 | VM de Servicios | Crear `qa.env`, generar los secretos y repartirlos |
| 4 | VM de Datos | Levantar las bases |
| 5 | VM de Servicios | Levantar los servicios y Kafka |
| 6 | VM de Borde | Levantar la entrada, la web y APISIX |
| 7 | VM de Tools | Levantar el monitoreo |
| 8 | VM de Servicios y tu PC | Verificar |
| 9 | Datos, Servicios y Borde | Activar el firewall y verificar de nuevo |

El orden importa: cada capa necesita a la anterior. Los servicios no arrancan sin las bases, y el borde no tiene a quién enviar peticiones sin los servicios.

---

## 1. Preparar las cuatro VM

> **Dónde:** en **cada una** de las cuatro VM. Haz 1.1 a 1.10 completo en una, luego en la siguiente.

### 1.1 Identificar la VM

```bash
hostname; ip -4 -br addr show ens33; lsb_release -ds
```

**Qué esperar:** la IP de esa VM (por ejemplo `ens33 UP 10.43.99.183/20`) y la versión de Ubuntu (por ejemplo `Ubuntu 22.04.5 LTS`).

- **Si la interfaz no se llama `ens33`:** usa `ip -4 -br addr` y busca la que tiene `10.43.x.x`.
- **Si no es Ubuntu:** los pasos sirven igual en Debian, salvo lo indicado en 1.7.2. Otra distribución necesita su propio procedimiento de instalación de Docker.

### 1.2 Herramientas básicas

```bash
sudo apt-get update && sudo apt-get install -y curl ca-certificates openssl rsync netcat-openbsd python3 openssh-server
```

```bash
sudo systemctl enable --now ssh
```

**Para qué sirve cada una:**

| Herramienta | Uso |
|---|---|
| `curl` | Descargar la llave de Docker y probar conexiones. Ubuntu de escritorio no la trae. |
| `openssl` | Generar los secretos y certificados. |
| `rsync` | Recibir el código desde tu PC. |
| `netcat-openbsd` (`nc`) | Probar si un puerto de otra VM responde. |
| `python3` | La prueba automática. |
| `openssh-server` | Copiar archivos entre VM con `scp`. |

Si ya estaban instaladas, `apt` lo dice y no cambia nada.

**Si sale distinto:**

| Mensaje | Qué hacer |
|---|---|
| `estudiante is not in the sudoers file` | Sin `sudo` no se puede instalar Docker ni el firewall. Pide al administrador del laboratorio que dé `sudo` a `estudiante`. |
| `Could not get lock /var/lib/dpkg/lock-frontend` | Ubuntu está instalando actualizaciones solo. Espera 5 minutos y repite. |
| `Temporary failure resolving` o `Could not connect` | La VM no tiene salida a internet. Ver el anexo B. |

### 1.3 Ubicar los repos en `~/Downloads/RedVital`

Todos los comandos de esta guía, y los `scp` entre VM, asumen esa carpeta en las cuatro VM.

**Solo en la VM de Servicios**, donde los repos quedaron en `~/Documents/RedVital/RedVital`, muévelos:

```bash
mkdir -p ~/Downloads/RedVital && mv ~/Documents/RedVital/RedVital/Despliegue_RedVital ~/Documents/RedVital/RedVital/Servicios_RedVital ~/Downloads/RedVital/
```

**En las cuatro VM**, comprueba:

```bash
ls ~/Downloads/RedVital
```

**Qué esperar:** las carpetas de la tabla de 0.1 para esa VM.

En la VM de Tools, si `Despliegue_RedVital` está en otra carpeta, no la muevas: el paso 2 la crea en el lugar correcto. Después borra la copia vieja para no confundirte.

**Si ya existían en `~/Descargas/RedVital`** (Ubuntu en español), usa esa carpeta en todos los comandos. Al copiar el código (paso 2), antepón `DESTINO_VM=Descargas/RedVital` al comando.

### 1.4 Que las VM se alcancen entre sí

El mismo comando en las cuatro:

```bash
for ip in 10.43.99.183 10.43.99.174 10.43.98.220 10.43.100.121; do nc -zvw3 $ip 22; done
```

**Qué esperar:** cuatro líneas `Connection to 10.43.x.x 22 port [tcp/ssh] succeeded!`, incluida la propia VM.

**Si una dice `timed out` o `refused`:**
1. Revisa en esa VM que se haya hecho el paso 1.2 (`openssh-server`).
2. Revisa su `ufw` (paso 1.10).
3. Si sigue igual, la red del laboratorio bloquea el tráfico entre esas VM: pídele al administrador que lo permita dentro de `10.43.96.0/20`.

**Para qué:** la base, los servicios y el borde se hablan por esta red. Si aquí no hay alcance, nada de lo siguiente funciona.

### 1.5 Salida a internet

Cada VM descarga imágenes de Docker; las de Servicios y Borde además descargan dependencias para compilar.

```bash
for u in https://download.docker.com https://registry-1.docker.io/v2/ https://mcr.microsoft.com/v2/ https://ghcr.io/v2/ https://gcr.io/v2/ https://repo.maven.apache.org/maven2/ https://api.nuget.org/v3/index.json https://registry.npmjs.org/; do printf '%-40s %s\n' "$u" "$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$u")"; done
```

**Qué esperar:** un número de tres cifras distinto de `000` en cada línea. `200`, `301`, `401` y `404` están bien: significan que el sitio respondió.

**Si alguno da `000`:** la VM no llega a ese sitio. Si todos dan `000`, la VM no tiene internet o necesita un proxy: ver el anexo B.

### 1.6 Reloj sincronizado

```bash
timedatectl status
```

**Qué esperar:** `System clock synchronized: yes` y `NTP service: active`.

**Si dice `no`:**

```bash
sudo timedatectl set-ntp true
```

Espera un minuto y repite `timedatectl status`.

**Para qué:** los tokens de sesión se aceptan con solo 30 segundos de desfase entre VM. Con relojes distintos, el inicio de sesión funciona pero todo lo demás responde 401.

### 1.7 Docker

Esta guía necesita **Docker Engine 25 o superior**, con los complementos **Compose** (2.24 o superior; las versiones 5.x también sirven) y **Buildx**. Todo instalado desde el repositorio oficial de Docker.

#### 1.7.1 Diagnóstico: ¿en qué caso está esta VM?

```bash
docker --version; docker compose version; docker buildx version; systemctl is-active docker; snap list docker 2>/dev/null; dpkg -l | awk '/^ii/ && $2 ~ /^(docker|containerd|podman-docker|runc)/ {print "paquete:", $2, $3}'
```

Busca tu caso en esta tabla, de arriba abajo, y sigue **solo** la fila que coincida:

| Lo que ves | Caso | Qué hacer |
|---|---|---|
| `docker: command not found` y ninguna línea `paquete:` ni de `snap` | **A. Sin Docker** | 1.7.2 y luego 1.7.5 |
| Una tabla de `snap` con una fila `docker` | **B. Docker de snap** | 1.7.3, luego 1.7.2 y 1.7.5 |
| `paquete: docker.io` | **C. Docker del repositorio de Ubuntu** | 1.7.4, luego 1.7.2 y 1.7.5 |
| `paquete: docker-ce` y `docker compose` o `docker buildx` dicen `is not a docker command`, o la versión de Docker es menor que 25 | **D. Docker oficial incompleto o viejo** | Solo el último comando de 1.7.2, y luego 1.7.5 |
| Todo responde, pero `systemctl is-active docker` dice `inactive` | **E. Instalado pero apagado** | `sudo systemctl enable --now docker` y repite el diagnóstico |
| Todo responde con versiones suficientes y `active` | **F. Listo** | Solo 1.7.5 |

> **VM de Borde (`10.43.98.220`):** ya tiene Docker y un `actions-runner` de GitHub que lo usa. Si está en el caso F, no toques la instalación. Si está en B, C o D, cambiarla reinicia Docker y corta lo que el runner esté ejecutando: avisa antes a quien lo administra.

#### 1.7.2 Instalar Docker desde el repositorio oficial

**Para qué:** el repositorio oficial trae versiones recientes de Docker, Compose y Buildx, que se actualizan con `apt`. Son las que exigen los Dockerfile (compilación con BuildKit y cachés compartidas).

1. **Agregar la llave con la que Docker firma sus paquetes:**

   ```bash
   sudo install -m 0755 -d /etc/apt/keyrings && sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc && sudo chmod a+r /etc/apt/keyrings/docker.asc
   ```

2. **Agregar el repositorio:**

   ```bash
   sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
   Types: deb
   URIs: https://download.docker.com/linux/ubuntu
   Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
   Components: stable
   Signed-By: /etc/apt/keyrings/docker.asc
   EOF
   ```

   `Suites` toma solo el nombre de tu versión de Ubuntu (`jammy` en la 22.04, `noble` en la 24.04).

   **En Debian:** cambia `ubuntu` por `debian` en la URL de este bloque y en la del primero.

3. **Instalar** (en el caso D, este es el único comando necesario):

   ```bash
   sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
   ```

4. **Encender Docker y dejarlo encendido tras cada reinicio:**

   ```bash
   sudo systemctl enable --now docker
   ```

**Si sale distinto:**

| Mensaje | Qué hacer |
|---|---|
| `Conflicting values set for option Signed-By` | Ya había otra fuente de Docker. Bórrala con `sudo rm -f /etc/apt/sources.list.d/docker.list` y repite el paso 3. |
| `NO_PUBKEY` | La llave no se descargó bien. Repite el paso 1. |
| `Unable to locate package docker-ce` | El paso 2 quedó mal escrito. Revísalo con `cat /etc/apt/sources.list.d/docker.sources`. |

#### 1.7.3 Caso B: quitar el Docker de snap

**Por qué:**
- Su servicio no se llama `docker.service`, y el firewall del paso 9 se engancha a ese nombre.
- Se actualiza solo y reinicia los contenedores sin avisar.
- Su confinamiento limita a qué carpetas llega.

Primero mira qué contenedores tiene:

```bash
docker ps -a
```

Si alguno lo usa otra persona, detente y pregunta. Si no:

```bash
sudo snap remove docker
```

`snap` guarda una instantánea de sus datos, así que nada se pierde de inmediato. Sigue con 1.7.2.

#### 1.7.4 Caso C: quitar el Docker del repositorio de Ubuntu

El paquete `docker.io` de Ubuntu choca con el oficial y suele venir sin Compose ni Buildx.

```bash
for pkg in docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc; do sudo apt-get remove -y $pkg; done
```

Es normal que algunos digan `is not installed` o `Unable to locate package`.

`remove` no borra `/var/lib/docker`: las imágenes y volúmenes que hubiera los sigue usando el Docker oficial. Sigue con 1.7.2.

#### 1.7.5 Usar Docker sin `sudo`

```bash
sudo usermod -aG docker $USER
```

**Para que surta efecto**, cierra la sesión del escritorio remoto de esa VM y vuelve a entrar, o ejecuta `sudo reboot`. Si estás por SSH, sal (`exit`) y vuelve a entrar.

Comprueba, ya sin `sudo`:

```bash
docker run --rm hello-world
```

**Qué esperar:** `Hello from Docker!`. Eso prueba a la vez que Docker corre, que tienes permiso y que descarga imágenes.

**Si sale distinto:**

| Mensaje | Qué hacer |
|---|---|
| `permission denied while trying to connect to the Docker daemon socket` | La sesión aún no ve el grupo nuevo. Cierra sesión y vuelve a entrar. Para salir del paso solo en esa terminal: `newgrp docker`. |
| `toomanyrequests: You have reached your pull rate limit` | Docker Hub limita las descargas anónimas, y en el laboratorio muchas máquinas salen por la misma IP. Crea una cuenta gratuita en Docker Hub y ejecuta `docker login` en esa VM (escribes tú tu usuario y clave). |

**Seguridad:** estar en el grupo `docker` equivale a ser administrador de la VM. Agrega solo a `estudiante`.

#### 1.7.6 Verificación final de Docker

```bash
docker version --format 'Docker {{.Server.Version}}'; docker compose version --short; docker buildx version; sudo iptables -L DOCKER-USER -n | head -1
```

**Qué esperar:**
- `Docker 25` o superior;
- Compose `2.24` o superior (o `5.x`);
- una línea `github.com/docker/buildx …`;
- `Chain DOCKER-USER (1 references)`.

**Si `iptables` dice `No chain/target/match by that name`:** Docker no está usando su firewall predeterminado, y el firewall del paso 9 lo necesita. Revisa la configuración:

```bash
cat /etc/docker/daemon.json
```

Quita las líneas `"iptables": false` o `"firewall-backend": "nftables"` si están, y reinicia Docker:

```bash
sudo systemctl restart docker
```

Si `docker info` muestra `rootless`, ese Docker corre sin privilegios y no puede publicar los puertos 80 y 443 ni usar `DOCKER-USER`. Usa el del sistema con `docker context use default`.

### 1.8 Recursos

```bash
nproc; free -h | head -2; swapon --show; df -h ~ | tail -1
```

**Mínimos sugeridos:**

| VM | CPU | RAM | Disco libre | Por qué |
|---|---|---|---|---|
| Datos | 2 | 4 GB | 20 GB | Tres PostgreSQL |
| Servicios | 4 | 8 GB | 30 GB | Kafka, Java, dos .NET; además compila |
| Borde | 2 | 4 GB | 20 GB | Compila la web; comparte CPU con el runner de GitHub |
| Tools | 2 | 4 GB | 20 GB | Prometheus guarda 15 días de métricas |

**Si la RAM está por debajo** y `swapon --show` no muestra nada, agrega 4 GB de memoria de intercambio. Evita que la compilación se caiga por falta de memoria:

```bash
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Si `swapon --show` ya muestra un archivo de intercambio, no hagas esto.

**Si falta disco:**
1. Revisa qué ocupa Docker con `docker system df`.
2. Libera las imágenes sin uso con `docker image prune -a`.
3. En la VM de Borde, el runner volverá a descargar las suyas cuando las necesite; si la VM la comparte alguien más, pregunta antes.

### 1.9 Restos de intentos anteriores y puertos ocupados

```bash
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'; docker volume ls --format '{{.Name}}' | grep -i redvital
```

**Si aparecen contenedores o volúmenes `redvital-qa-…`**, hubo un intento anterior con otros secretos. Elimínalo en esa VM con el anexo C antes de seguir; si no, las bases rechazan las claves nuevas.

```bash
sudo ss -ltnp | grep -E ':(80|443|3000|5432|5433|5434|8080|8082|8083|8183|9090|9091|9094|9100|9180|9404) '
```

**Qué esperar:** nada en los puertos de esa VM:

| VM | Puertos que deben estar libres |
|---|---|
| Datos | 5432, 5433, 5434, 9100, 9180 |
| Servicios | 8080, 8082, 8083, 8183, 9094, 9404, 9100, 9180 |
| Borde | 80, 443, 9091, 9100, 9180 |
| Tools | 3000, 8080 y 9090 en `127.0.0.1` |

**Si algo ocupa uno de esos puertos,** la última columna dice qué programa es.
- Si es un servidor web del sistema (`apache2`, `nginx`) que nadie usa, apágalo, por ejemplo con `sudo systemctl disable --now apache2`.
- Si es un contenedor ajeno, o no sabes qué es, pregunta antes de tocarlo.

**VM de Borde:** los trabajos del `actions-runner` usan el mismo Docker. Los CI de RedVital usan los puertos 9080 y `127.0.0.1:8081`, que no chocan con el borde, pero mientras corren le quitan CPU y memoria a QA.

### 1.10 Firewall del sistema (`ufw`)

```bash
sudo ufw status
```

- **`inactive`:** no hagas nada.
- **`active`:** permite SSH desde la red del laboratorio, para las copias entre VM:

  ```bash
  sudo ufw allow from 10.43.96.0/20 to any port 22 proto tcp
  ```

`ufw` no controla los puertos que publica Docker; esos los filtra el paso 9.

---

## 2. Llevar el código actualizado a las VM

> **Dónde:** en **tu PC**, en `~/Descargas/RedVital/Despliegue_RedVital`.

**Para qué:** las copias que hay en las VM son anteriores a los últimos cambios:
- el firewall con Tools en su propia VM;
- la entrada por IP en Caddy;
- esta guía;
- el script de copia.

Desde ahora, el código se lleva siempre con el mismo comando. Envía solo lo que cambió y borra en la VM los archivos que ya no existen.

### 2.1 Llave SSH (una sola vez)

Así no tienes que escribir la contraseña de cada VM en cada copia.

```bash
ls ~/.ssh/id_ed25519.pub || ssh-keygen -t ed25519
```

Si la crea, presiona Enter en cada pregunta.

```bash
for ip in 10.43.99.183 10.43.99.174 10.43.98.220 10.43.100.121; do ssh-copy-id estudiante@$ip; done
```

En cada VM, la primera vez pregunta `Are you sure you want to continue connecting (yes/no)?`: escribe `yes`. Luego pide la contraseña de `estudiante` en esa VM.

**Comprobar:**

```bash
for ip in 10.43.99.183 10.43.99.174 10.43.98.220 10.43.100.121; do ssh -o BatchMode=yes estudiante@$ip hostname; done
```

**Qué esperar:** el nombre de las cuatro VM, sin que pida contraseña.

**Si dice `Permission denied (publickey)`:** esa VM no acepta contraseñas por SSH. Copia el código por la unidad compartida del escritorio remoto (`thinclient_drives`), sin las carpetas `secretos`, `node_modules`, `target`, `bin` y `obj`.

### 2.2 Copiar

```bash
cd ~/Descargas/RedVital/Despliegue_RedVital
```

```bash
scripts/copiar-codigo.sh datos=10.43.99.183 servicios=10.43.99.174 borde=10.43.98.220 tools=10.43.100.121
```

**Qué hace:** a cada VM le envía solo los repos de su rol, a `~/Downloads/RedVital`, la misma tabla de 0.1.

**Lo que nunca envía ni borra:**
- secretos;
- archivos `*.env`;
- la configuración generada de Tools;
- la CA extraída;
- los respaldos;
- dependencias y compilados (`node_modules`, `target`, `bin`, `obj`, `dist`): cada VM compila dentro de Docker.

**Qué esperar:** `copiado` cuatro veces y `Código copiado a 4 VM.`

**Si falla una VM:**
- `rsync: command not found`: falta el paso 1.2 en esa VM.
- `Connection refused`: falta `openssh-server` en esa VM.

### 2.3 Limpiar lo heredado (solo la primera vez)

> **Dónde:** en las **cuatro VM**.

```bash
cd ~/Downloads/RedVital/Despliegue_RedVital && rm -rf secretos vm-tools/generado ambientes/qa.env ambientes/qa-local.env secretos-*.tar.gz && ls
```

**Para qué:**
- **Secretos viejos:** si las carpetas se copiaron con secretos de la prueba local (hechos para `172.17.0.1`), el generador los reutilizaría.
- **`qa.env` editado a mano:** se escribe completo en 3.1. El que editaste venía de la prueba local: además de las IP tenía `DOMINIO=localhost` e `IP_PUBLICA_BORDE=127.0.0.1`, que dejarían el borde inalcanzable.

**Qué esperar:** `ambientes docs kafka pruebas README.md scripts semillas vm-borde vm-datos vm-servicios vm-tools`, sin `secretos`.

---

## 3. Ambiente y secretos

> **Dónde:** **VM de Servicios** (`10.43.99.174`).

```bash
cd ~/Downloads/RedVital/Despliegue_RedVital
```

**Por qué aquí:**
- Es la VM que usa casi todos los secretos, así que la mayoría no viaja.
- Guarda dos archivos que ninguna otra VM necesita: la llave de la CA de las bases y la clave de las cuentas de prueba.
- La VM de Borde no sirve para esto: tiene el runner de CI, que controla su Docker.
- La de Tools tampoco: monitorea todos los ambientes.

### 3.1 Crear `ambientes/qa.env`

```bash
cat > ambientes/qa.env <<'EOF'
AMBIENTE=qa
ASPNETCORE_AMBIENTE=QA
VERSION=qa
IP_VM_DATOS=10.43.99.183
IP_VM_SERVICIOS=10.43.99.174
IP_VM_BORDE=10.43.98.220
IP_VM_TOOLS=10.43.100.121
DOMINIO=10.43.98.220
IP_PUBLICA_BORDE=0.0.0.0
TLS_MODO=internal
REPOS=../..
SECRETOS_DIR=../secretos/qa
DB_SSL_JDBC=sslmode=verify-ca&sslrootcert=/run/secrets/postgres_ca
DB_SSL_NPGSQL=SSL Mode=VerifyCA;Root Certificate=/run/secrets/postgres_ca;GSS Encryption Mode=Disable
COMPOSE_PROFILES=semillas,metricas
EOF
```

Comprueba:

```bash
grep -E '^(IP_|DOMINIO|COMPOSE)' ambientes/qa.env
```

**Qué significa cada línea:**

| Variable | Valor | Para qué |
|---|---|---|
| `IP_VM_DATOS` | `10.43.99.183` | Dónde publican las bases y a dónde se conectan los servicios. Va dentro del certificado TLS de PostgreSQL. |
| `IP_VM_SERVICIOS` | `10.43.99.174` | Dónde publican los servicios y Kafka, y a dónde envía APISIX las peticiones. |
| `IP_VM_BORDE` | `10.43.98.220` | Dónde publica APISIX sus métricas, y a quién deja entrar el firewall de Servicios. |
| `IP_VM_TOOLS` | `10.43.100.121` | A quién deja leer métricas y Kafka el firewall de las otras tres. |
| `DOMINIO` | `10.43.98.220` | Cómo se entra a RedVital: `https://10.43.98.220`. Al usar la IP, nadie configura DNS ni el archivo hosts. |
| `IP_PUBLICA_BORDE` | `0.0.0.0` | El borde escucha 80 y 443 en todas sus interfaces. |
| `TLS_MODO` | `internal` | Caddy firma el HTTPS con su propia CA de QA. |
| `COMPOSE_PROFILES` | `semillas,metricas` | `semillas`: cuentas, jornadas y umbrales de prueba. `metricas`: los exportadores que lee Prometheus. |
| Las demás | — | No las cambies: ambiente, versión de imágenes, rutas relativas y TLS hacia la base. |

Este archivo no tiene secretos y debe ser **idéntico en las cuatro VM**. Por eso se escribe solo aquí y viaja junto con los secretos (3.3).

### 3.2 Generar los secretos

```bash
scripts/generar-secretos.sh qa
```

**Qué hace:** crea en `secretos/qa/` las claves de las bases, la CA y el certificado de PostgreSQL (con `10.43.99.183` dentro), la llave de firma de Identidad, las credenciales de servicio, los usuarios de Kafka y las cuentas de prueba. El anexo A dice qué es cada uno.

La primera vez descarga la imagen del SDK de .NET (unos 1,3 GB), que se reutiliza después al compilar.

**Qué esperar:** pasos `1/5` a `5/5` y al final `Secretos de qa en …/secretos/qa`. Nunca imprime una clave.

### 3.3 Empaquetar y enviar

Cada VM recibe **solo** los secretos que usa:

```bash
scripts/empaquetar-secretos.sh qa datos && scripts/empaquetar-secretos.sh qa borde && scripts/empaquetar-secretos.sh qa tools
```

Envía cada paquete junto con `qa.env`. Cada `scp` pide la contraseña de `estudiante` en la VM de destino; la primera vez, además, `yes`.

```bash
scp secretos-qa-datos.tar.gz ambientes/qa.env estudiante@10.43.99.183:Downloads/RedVital/Despliegue_RedVital/
```

```bash
scp secretos-qa-borde.tar.gz ambientes/qa.env estudiante@10.43.98.220:Downloads/RedVital/Despliegue_RedVital/
```

```bash
scp secretos-qa-tools.tar.gz ambientes/qa.env estudiante@10.43.100.121:Downloads/RedVital/Despliegue_RedVital/
```

Borra los paquetes de aquí; Servicios ya tiene todo en `secretos/qa/`:

```bash
rm -f secretos-qa-*.tar.gz
```

**Para qué el empaquetado:** si una VM se compromete, solo expone lo suyo. El borde, la más expuesta (público y CI), guarda una sola credencial.

### 3.4 Extraer en cada VM

**En la VM de Datos:**

```bash
cd ~/Downloads/RedVital/Despliegue_RedVital && mv qa.env ambientes/qa.env && tar -xzf secretos-qa-datos.tar.gz && rm secretos-qa-datos.tar.gz && ls -la secretos/qa
```

**En la VM de Borde:**

```bash
cd ~/Downloads/RedVital/Despliegue_RedVital && mv qa.env ambientes/qa.env && tar -xzf secretos-qa-borde.tar.gz && rm secretos-qa-borde.tar.gz && ls -la secretos/qa
```

**En la VM de Tools:**

```bash
cd ~/Downloads/RedVital/Despliegue_RedVital && mv qa.env ambientes/qa.env && tar -xzf secretos-qa-tools.tar.gz && rm secretos-qa-tools.tar.gz && ls -la secretos/qa
```

**Qué esperar:** `secretos/qa` con permisos `drwx------`, y dentro:

| VM | Archivos |
|---|---|
| Datos | 14 |
| Borde | `gateway-secreto-cliente` |
| Tools | `kafka_inspeccion` |

---

## 4. VM de Datos (`10.43.99.183`)

> **Dónde:** VM de Datos.

```bash
cd ~/Downloads/RedVital/Despliegue_RedVital
```

### 4.1 Validar la configuración

```bash
docker compose -f vm-datos/docker-compose.yml --env-file ambientes/qa.env config -q
```

**Qué esperar:** que no imprima nada. Si se queja de una variable, revisa `ambientes/qa.env`; si se queja de un secreto, repite 3.4.

### 4.2 Levantar

```bash
docker compose -f vm-datos/docker-compose.yml --env-file ambientes/qa.env up -d
```

La primera vez descarga PostgreSQL, Flyway y los exportadores de métricas (cerca de 1 GB).

**Qué ocurre, en orden:**

1. **Arrancan las tres PostgreSQL:** Identidad en `10.43.99.183:5432`, Campañas en `:5433` y Donación en `:5434`.
   - `entrypoint-tls.sh` prepara el certificado con los permisos que exige PostgreSQL.
   - `pg_hba.conf` rechaza toda conexión de red sin TLS.
   - La primera vez se crean el usuario propietario y el de servicio de cada base.
2. **`migracion-*`** (Flyway) crea las tablas como propietario y termina.
3. **`credenciales-identidad`** registra las huellas de las credenciales de APISIX y Donación.
4. **`semilla-*`** carga las cuentas de prueba, cinco jornadas y los umbrales del banco de prueba.
5. **`node-exporter` y `cadvisor`** publican las métricas de la VM en 9100 y 9180.

### 4.3 Comprobar

```bash
docker compose -f vm-datos/docker-compose.yml --env-file ambientes/qa.env ps -a
```

**Qué esperar:**
- `db-identidad`, `db-campana` y `db-donacion` en `Up … (healthy)`;
- `migracion-*`, `credenciales-identidad` y `semilla-*` en `Exited (0)`;
- `node-exporter` y `cadvisor` en `Up`.

Si alguno quedó en `Exited (1)`, sus logs dicen por qué:

```bash
docker compose -f vm-datos/docker-compose.yml --env-file ambientes/qa.env logs migracion-donacion | grep -E "Successfully|ERROR"
```

**Qué esperar:** `Successfully applied 7 migrations … now at version v007`. Cambia `migracion-donacion` por el contenedor que falló.

```bash
docker compose -f vm-datos/docker-compose.yml --env-file ambientes/qa.env exec -u postgres db-identidad psql -d db_identidad -c "select correo from usuario order by correo"
```

**Qué esperar:** seis cuentas `@redvital.test`.

---

## 5. VM de Servicios (`10.43.99.174`)

> **Dónde:** VM de Servicios.

```bash
cd ~/Downloads/RedVital/Despliegue_RedVital
```

### 5.1 Alcance hacia las bases

```bash
nc -zv 10.43.99.183 5432; nc -zv 10.43.99.183 5433; nc -zv 10.43.99.183 5434
```

**Qué esperar:** `succeeded` tres veces. Si no, la VM de Datos no está arriba (paso 4).

### 5.2 Validar, compilar y levantar

```bash
docker compose -f vm-servicios/docker-compose.yml --env-file ambientes/qa.env config -q
```

```bash
docker compose -f vm-servicios/docker-compose.yml --env-file ambientes/qa.env up -d --build
```

La primera compilación tarda de 10 a 20 minutos: descarga las imágenes base y las dependencias de Maven y NuGet. Las siguientes reutilizan la caché.

**Qué ocurre, en orden:**

1. **`kafka` arranca.** La primera vez prepara su almacenamiento con el administrador `inicializacion`.
2. **`kafka-init`** crea los usuarios, los temas y los permisos, y termina.
3. **`identity-service`** se conecta por TLS a su base y publica su llave pública en `10.43.99.174:8080`.
4. **`campaign-service` (`:8082`) y `donation-service` (`:8083`)** arrancan. Campañas publica por Kafka las jornadas sembradas, y Donación las recibe.

### 5.3 Comprobar

```bash
docker compose -f vm-servicios/docker-compose.yml --env-file ambientes/qa.env ps -a
```

**Qué esperar:** los tres servicios y `kafka` en `(healthy)`, y `kafka-init` en `Exited (0)`. Un servicio puede tardar hasta 2 minutos en pasar a `healthy`.

```bash
curl -s http://10.43.99.174:8080/.well-known/jwks.json
```

**Qué esperar:** un JSON con `"kid":"identidad_clave_firma"`. Es la llave con que APISIX valida los tokens.

```bash
curl -s http://10.43.99.174:8183/actuator/health/readiness
```

**Qué esperar:** `{"status":"UP"}` (Donación lista).

```bash
curl -s http://10.43.99.174:8082/v1/campanias
```

**Qué esperar:** la lista JSON de jornadas publicadas (Campañas leyendo su base).

**Comprobar que Kafka llevó las jornadas a Donación.** Este comando va **en la VM de Datos**:

```bash
cd ~/Downloads/RedVital/Despliegue_RedVital && docker compose -f vm-datos/docker-compose.yml --env-file ambientes/qa.env exec -u postgres db-donacion psql -d db_donacion -c "select nombre from campania_publicada"
```

**Qué esperar:** cuatro jornadas. Demuestra el camino Campañas → Kafka → Donación.

---

## 6. VM de Borde (`10.43.98.220`)

> **Dónde:** VM de Borde.

```bash
cd ~/Downloads/RedVital/Despliegue_RedVital
```

### 6.1 Alcance hacia los servicios

```bash
nc -zv 10.43.99.174 8080; nc -zv 10.43.99.174 8082; nc -zv 10.43.99.174 8083
```

**Qué esperar:** `succeeded` tres veces.

### 6.2 Validar, compilar y levantar

```bash
docker compose -f vm-borde/docker-compose.yml --env-file ambientes/qa.env config -q
```

```bash
docker compose -f vm-borde/docker-compose.yml --env-file ambientes/qa.env up -d --build
```

**Qué ocurre:**
- **`web`:** compila el front (de 3 a 5 minutos la primera vez) y lo sirve en un puerto interno.
- **`apisix`:** carga sus rutas y resuelve `identity-service`, `campaign-service` y `donation-service` a `10.43.99.174`.
- **`caddy`:** escucha en 80 y 443. La primera vez crea su CA de QA y un certificado para `10.43.98.220`.

### 6.3 Comprobar

```bash
docker compose -f vm-borde/docker-compose.yml --env-file ambientes/qa.env ps
```

**Qué esperar:** `web` en `(healthy)`, y `apisix` y `caddy` en `Up`.

```bash
curl -sk https://10.43.98.220/api/v1/campanias
```

**Qué esperar:** la misma lista de jornadas de 5.3. Es la cadena completa: Caddy → APISIX → Campañas en Servicios → base en Datos.

### 6.4 Extraer la CA de QA

```bash
docker compose -f vm-borde/docker-compose.yml --env-file ambientes/qa.env exec caddy cat /data/caddy/pki/authorities/local/root.crt > redvital-qa-raiz.crt && head -1 redvital-qa-raiz.crt
```

**Qué esperar:** `-----BEGIN CERTIFICATE-----`.

**Llévala a la VM de Servicios**, para la prueba automática. Este comando va **en la VM de Servicios**:

```bash
scp estudiante@10.43.98.220:Downloads/RedVital/Despliegue_RedVital/redvital-qa-raiz.crt ~/Downloads/RedVital/Despliegue_RedVital/
```

**Y a tu PC**, para el navegador. Este comando va **en tu PC**:

```bash
scp estudiante@10.43.98.220:Downloads/RedVital/Despliegue_RedVital/redvital-qa-raiz.crt ~/Descargas/
```

**Para qué:** es la autoridad que firmó el HTTPS de QA. Sin ella, el navegador y la prueba automática rechazan la conexión. Es pública: no es un secreto.

Vive en el volumen `caddy-datos`. Si ese volumen se borra, Caddy crea otra CA y hay que repetir este paso.

---

## 7. VM de Tools (`10.43.100.121`)

> **Dónde:** VM de Tools.

```bash
cd ~/Downloads/RedVital/Despliegue_RedVital
```

### 7.1 Alcance hacia lo que monitorea

```bash
nc -zv 10.43.99.183 9100; nc -zv 10.43.99.174 9094; nc -zv 10.43.99.174 8183; nc -zv 10.43.98.220 9091
```

**Qué esperar:** `succeeded` cuatro veces. Si una falla, esa VM no tiene arriba sus métricas: revisa que su `qa.env` tenga `COMPOSE_PROFILES=semillas,metricas`.

### 7.2 Generar, configurar y levantar

```bash
scripts/generar-secretos.sh tools
```

Crea las claves de los administradores de Grafana y Kafbat en `secretos/tools/`.

```bash
scripts/renderizar-tools.sh
```

Genera, a partir de `ambientes/qa.env`:
- la configuración de Prometheus, con las métricas de Datos, Servicios y Borde;
- la de Kafbat, con el Kafka de Servicios y el usuario de solo lectura `inspeccion`.

**Qué esperar:**
- `ambiente qa: VM datos 10.43.99.183, servicios 10.43.99.174, borde 10.43.98.220`;
- `sin ambientes/produccion.env: se omite`, que es normal porque producción aún no existe.

```bash
docker compose -f vm-tools/docker-compose.yml up -d
```

```bash
docker compose -f vm-tools/docker-compose.yml ps
```

**Qué esperar:** `prometheus`, `grafana` y `kafbat` en `Up`. Los tres escuchan solo en `127.0.0.1` de esta VM: nadie más en la red los ve.

### 7.3 Entrar a Tools

**Opción 1, desde tu PC (recomendada).** Abre un túnel SSH y déjalo corriendo en una terminal:

```bash
ssh -N -L 13000:127.0.0.1:3000 -L 19090:127.0.0.1:9090 -L 18080:127.0.0.1:8080 estudiante@10.43.100.121
```

Se usan los puertos 13000, 19090 y 18080 de tu PC porque 3000, 9090 y 8080 pueden estar ocupados por tu ambiente local.

**Opción 2, desde el escritorio de la VM de Tools.** Abre Firefox ahí y usa los puertos originales: 3000, 9090 y 8080.

| Qué | Opción 1 (tu PC) | Opción 2 (VM de Tools) | Qué esperar |
|---|---|---|---|
| Prometheus | `http://127.0.0.1:19090/targets` | `http://127.0.0.1:9090/targets` | 9 objetivos, todos en `UP` |
| Grafana | `http://127.0.0.1:13000` | `http://127.0.0.1:3000` | Entras con usuario `admin` |
| Kafbat | `http://127.0.0.1:18080` | `http://127.0.0.1:8080` | Entras con usuario `admin`; el clúster `qa` aparece `online` con 6 temas |

Las claves se leen en la VM de Tools: la de Grafana con `cat secretos/tools/grafana_admin` y la de Kafbat con `cat secretos/tools/kafbat_admin`.

**Si un objetivo de Prometheus está `DOWN`:** la columna `Error` dice cuál. Prueba ese puerto con `nc -zv` desde la VM de Tools (7.1).

---

## 8. Verificar el funcionamiento

### 8.1 Prueba automática

> **Dónde:** VM de Servicios. Ahí están la CA (6.4) y la clave de las cuentas de prueba.

```bash
cd ~/Downloads/RedVital/Despliegue_RedVital && python3 pruebas/extremo_a_extremo.py --base https://10.43.98.220 --ca redvital-qa-raiz.crt --secretos secretos/qa
```

**Qué hace:** recorre el sistema solo por `https://10.43.98.220`, igual que un navegador:
- seguridad del borde;
- registro anónimo;
- crear cuenta, iniciar, renovar y cerrar sesión;
- el operador registra y opera una unidad;
- aislamiento entre bancos;
- Kafka en ambos sentidos;
- límite de inicios de sesión.

**Qué esperar:** `46/46 comprobaciones correctas` la primera vez. Desde la segunda salen `45/45`: la campaña borrador ya quedó publicada y la prueba lo indica con `[--  ]`. Si vas a correrla otra vez, espera un minuto: la segunda corrida choca con el límite de 10 inicios de sesión por minuto, que es intencional.

### 8.2 Prueba manual desde tu PC

1. **Confía en la CA de QA**, una sola vez por navegador, con el archivo `~/Descargas/redvital-qa-raiz.crt` de 6.4.
   - **Firefox:** Configuración → Privacidad y seguridad → Certificados → Ver certificados → Autoridades → Importar. Marca «Confiar en esta CA para identificar sitios web».
   - **Brave o Chrome:** Configuración → Privacidad y seguridad → Seguridad → Administrar certificados. En versiones recientes: «Instalados por ti» → «Certificados de confianza» → Importar. En versiones anteriores: pestaña «Autoridades» → Importar, y marca la opción de confiar para identificar sitios web.
2. **Abre `https://10.43.98.220`.**
3. **Haz el recorrido:**
   1. «Quiero donar», en 3 pasos.
   2. «Crear cuenta», en 4 pasos; al terminar debes quedar dentro, en «Mi perfil».
   3. Cerrar sesión: vuelves a la portada.
   4. Entra con `operador@redvital.test`. Registra una donación en «Ciudad Universitaria», fracciónala y lleva una unidad hasta «despachada».
   5. Entra con `admin.banco@redvital.test`. Comprueba que el contador de esa jornada subió, publica «La Alpujarra», configura umbrales y atiende una alerta.

La clave de las cuentas `@redvital.test` se ve en la VM de Servicios con `cat secretos/qa/clave_cuentas_qa`.

---

## 9. Activar el firewall

> **Dónde:** VM de Datos, de Servicios y de Borde, en `~/Downloads/RedVital/Despliegue_RedVital`. Requiere `sudo`. **La VM de Tools no lo necesita:** no publica nada fuera de `127.0.0.1`.

**Por qué después de verificar:** si algo fallara con el firewall puesto, no sabrías si es el firewall o la aplicación.

**Por qué no es opcional:** la red del laboratorio la comparten más equipos. Sin firewall, cualquiera en ella llega a las bases y a los servicios.

### 9.1 Revisar las reglas antes de aplicarlas

| VM | Comando | Qué debe permitir |
|---|---|---|
| Datos | `scripts/firewall.sh qa datos --simular` | 5432–5434 solo desde `10.43.99.174`; 9100 y 9180 solo desde `10.43.100.121` |
| Servicios | `scripts/firewall.sh qa servicios --simular` | 8080, 8082 y 8083 solo desde `10.43.98.220`; 9094, 9404, 8183, 9100 y 9180 solo desde `10.43.100.121` |
| Borde | `scripts/firewall.sh qa borde --simular` | 80 y 443 para todos; 9091, 9100 y 9180 solo desde `10.43.100.121` |

`--simular` solo imprime las reglas, sin aplicar nada. Cada puerto lleva una línea `RETURN` por origen permitido y luego un `DROP` para todos los demás.

### 9.2 Instalar

En la VM de Datos:

```bash
sudo scripts/firewall.sh qa datos --instalar
```

En la VM de Servicios:

```bash
sudo scripts/firewall.sh qa servicios --instalar
```

En la VM de Borde:

```bash
sudo scripts/firewall.sh qa borde --instalar
```

**Qué hace:** aplica las reglas en la cadena `DOCKER-USER` y crea el servicio `redvital-firewall`. Ese servicio las vuelve a aplicar cada vez que arranca Docker, también tras reiniciar la VM.

**Por qué no basta `ufw`:** Docker publica sus puertos con reglas que se evalúan antes que las de `ufw`. `DOCKER-USER` es la cadena que Docker reserva para filtrarlos.

### 9.3 Comprobar que filtra

| Desde | Comando | Qué esperar |
|---|---|---|
| Tu PC | `nc -zvw3 10.43.99.183 5434` | `timed out`: nadie de fuera llega a las bases |
| Tu PC | `nc -zvw3 10.43.99.174 8083` | `timed out`: nadie de fuera llega a los servicios |
| Tu PC | `nc -zvw3 10.43.98.220 443` | `succeeded`: el borde sí es público |
| VM de Servicios | `nc -zv 10.43.99.183 5434` | `succeeded`: los servicios sí llegan a su base |
| VM de Borde | `nc -zvw3 10.43.99.183 5434` | `timed out`: el borde no llega a las bases |
| VM de Tools | `nc -zv 10.43.99.183 9100` | `succeeded`: Tools sí lee las métricas |
| VM de Tools | `nc -zvw3 10.43.99.183 5434` | `timed out`: Tools no llega a las bases |

Después vuelve a correr la prueba automática (8.1): deben seguir saliendo todas correctas (45/45 desde la segunda corrida). Revisa también que Prometheus siga con sus 9 objetivos en `UP`.

### 9.4 Si algo quedó bloqueado por error

Quita las reglas al instante, en la VM afectada:

```bash
sudo iptables -F REDVITAL
```

Para desinstalar el servicio:

```bash
sudo systemctl disable --now redvital-firewall
```

Corrige `ambientes/qa.env` y vuelve a instalar (9.2).

---

## 10. Operación del día a día

Todos los comandos se ejecutan desde `~/Downloads/RedVital/Despliegue_RedVital` de la VM indicada.

**Para llevar un cambio de código:** primero cópialo desde tu PC con el comando de 2.2. Luego recompila en la VM que corresponda:

| Quiero… | Dónde | Comando |
|---|---|---|
| Desplegar un cambio de Donación | VM de Servicios | `docker compose -f vm-servicios/docker-compose.yml --env-file ambientes/qa.env up -d --build donation-service` |
| Desplegar un cambio de Identidad o Campañas | VM de Servicios | El mismo, con `identity-service` o `campaign-service` |
| Aplicar migraciones nuevas | VM de Datos | `docker compose -f vm-datos/docker-compose.yml --env-file ambientes/qa.env up -d`. Hazlo **antes** de desplegar el servicio que las necesita. |
| Desplegar un cambio del front | VM de Borde | `docker compose -f vm-borde/docker-compose.yml --env-file ambientes/qa.env up -d --build web` |
| Cambiar rutas de APISIX | VM de Borde | `docker compose -f vm-borde/docker-compose.yml --env-file ambientes/qa.env restart apisix` |
| Ver los logs de un servicio | VM de Servicios | `docker compose -f vm-servicios/docker-compose.yml --env-file ambientes/qa.env logs -f --tail 100 donation-service` |
| Respaldar la base de Donación | VM de Datos | `docker compose -f vm-datos/docker-compose.yml --env-file ambientes/qa.env exec -T -u postgres db-donacion pg_dump -Fc db_donacion > respaldo-donacion-$(date +%F).dump` |
| Apagar sin perder datos | Cada VM | `docker compose -f vm-<rol>/docker-compose.yml --env-file ambientes/qa.env down`, con `<rol>` = `datos`, `servicios` o `borde`. En Tools: `docker compose -f vm-tools/docker-compose.yml down`. |
| Volver a encender | Cada VM | Lo mismo con `up -d`, en el orden Datos → Servicios → Borde → Tools |

**Tras reiniciar una VM:** los contenedores vuelven solos (`restart: unless-stopped`), y el firewall también. Si la VM de Datos reinicia, los servicios se reconectan solos en uno o dos minutos.

**Rotar secretos:** el anexo A explica cada caso.

---

## 11. Solución de problemas

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `bash: syntax error near unexpected token 'newline'` | Se copió un comando con `<…>` | Reemplaza el texto entre ángulos por el valor real. |
| `docker: command not found` | Docker no está instalado | Paso 1.7. |
| `permission denied … docker.sock` | La sesión no ve el grupo `docker` | 1.7.5: cierra sesión y vuelve a entrar. |
| `unknown shorthand flag: 'f' in -f` o `docker: 'compose' is not a docker command` | Falta el complemento Compose | Caso D de 1.7.1. |
| `requires buildx 0.17 or later` | Buildx viejo o ausente | Caso D de 1.7.1. |
| `toomanyrequests` | Límite de descargas de Docker Hub | `docker login` en esa VM (1.7.5). |
| `required variable … is missing` | No estás en `Despliegue_RedVital`, o falta `--env-file ambientes/qa.env` | Ejecuta desde ahí, con los dos parámetros. |
| `secret … not found` o `no such file` en `secretos/qa` | No se extrajo el paquete de esa VM | Repite 3.4. |
| `migracion-*` o un servicio dice `password authentication failed` | La base se creó con secretos de otra generación | Anexo C en la VM de Datos y vuelve a levantar. |
| Un servicio dice `certificate verify failed` o `remote certificate is invalid` | Datos y Servicios tienen secretos de generaciones distintas | Repite 3.3 y 3.4 para Datos, y luego el anexo C en Datos. |
| Un servicio da timeout hacia `10.43.99.183` | Datos caída o firewall de Datos | `nc -zv 10.43.99.183 5432` desde Servicios; revisa 9.1. |
| La compilación se corta con `Killed` o `exit code: 137` | Falta memoria | Agrega swap (1.8) y repite el `up --build`. |
| `no space left on device` | Disco lleno | 1.8, «Si falta disco». |
| `port is already allocated` o `address already in use` | Otro programa usa ese puerto | 1.9. |
| El inicio de sesión funciona, pero luego todo da 401 | Relojes desincronizados, o APISIX no llega a Identidad | Paso 1.6; `nc -zv 10.43.99.174 8080` desde Borde. |
| `/api/…` responde 502 o 504 | APISIX no alcanza los servicios | `nc -zv 10.43.99.174 8083` desde Borde; firewall de Servicios. |
| El navegador advierte del certificado | CA no importada, o Caddy creó otra CA | 8.2, paso 1; si se borró el volumen de Caddy, repite 6.4. |
| `429` | Límite de tasa: 60 por minuto en lo público, 10 por minuto en el inicio de sesión | Espera un minuto. |
| Un objetivo `DOWN` en Prometheus | Firewall, o falta el perfil `metricas` | `nc -zv` desde Tools (7.1); revisa `COMPOSE_PROFILES` en el `qa.env` de esa VM. |
| Kafbat muestra el clúster `qa` `offline` | Tools no llega a `10.43.99.174:9094`, o falta `secretos/qa/kafka_inspeccion` | `nc -zv 10.43.99.174 9094` desde Tools; repite 3.4 y 7.2. |
| Va lento mientras GitHub Actions corre algo | El runner de Borde comparte Docker y CPU | Espera a que termine el trabajo de CI. |

---

## Anexo A — Los secretos

| Secreto | Para qué | Vive en |
|---|---|---|
| `postgres_superusuario_*` | Superusuario de cada base; solo se usa al crearla | Datos (y Servicios, donde se generó) |
| `*_propietario` | Dueño del esquema: migraciones y semillas | Datos |
| `*_servicio` | Usuario con permisos mínimos con que cada servicio usa su base | Datos y Servicios |
| `postgres_ca` | CA de PostgreSQL de QA: los servicios verifican la base con ella | Datos y Servicios |
| `postgres_tls_cert`, `postgres_tls_key` | Certificado y llave del servidor PostgreSQL (lleva `10.43.99.183`) | Datos |
| `postgres_ca_llave` | Llave privada de la CA: solo sirve para emitir otro certificado | Solo Servicios |
| `identidad_clave_firma` | Llave RSA con que Identidad firma los tokens | Servicios |
| `gateway-secreto-cliente` | Credencial de APISIX ante Identidad (reporte de denegaciones) | Borde |
| `donacion_secreto_identidad` | Credencial de Donación ante Identidad (alta de cuentas de donante) | Servicios |
| `credenciales_servicio.sql` | Huellas, no claves, de esas dos credenciales | Datos |
| `donacion_clave_hmac` | Resume los documentos de identidad. **No se rota**: se perderían las búsquedas. | Servicios |
| `kafka_*` | Administrador de Kafka y un usuario SCRAM por servicio | Servicios (`kafka_inspeccion` también en Tools) |
| `semilla_identidad.sql` | Cuentas de prueba, con la clave cifrada | Datos |
| `clave_cuentas_qa` | La clave en claro de esas cuentas, para quienes prueban | Solo Servicios |
| `grafana_admin`, `kafbat_admin` | Acceso a Grafana y Kafbat | Tools (`secretos/tools/`) |

Ningún secreto está en git: `.gitignore` excluye `secretos/`, los `*.env` y los paquetes, y `copiar-codigo.sh` no los envía.

**Rotación:**
- **Clave de una base:** en Datos, `ALTER ROLE` con `exec -u postgres … psql`. Luego actualiza el archivo en Datos y Servicios y reinicia el servicio.
- **Usuario de Kafka:** actualiza el archivo en Servicios, ejecuta `up -d kafka-init` y reinicia el servicio. Si es `kafka_inspeccion`, cópialo también a Tools y repite 7.2.
- **Credenciales ante Identidad:**
  1. En Servicios, borra el secreto y `credenciales_servicio.sql`, y ejecuta `scripts/generar-secretos.sh qa`.
  2. Lleva el SQL a Datos y ejecuta ahí `up -d credenciales-identidad`.
  3. Lleva el secreto a Borde o déjalo en Servicios, según de quién sea, y reinicia.
- **Llave de firma de Identidad:** pendiente de automatizar. Hoy hay que borrar la fila anterior de `llave_firma` y reiniciar Identidad.

---

## Anexo B — VM sin internet o detrás de un proxy

### B.1 Si el laboratorio usa un proxy

Pregunta al administrador la dirección del proxy y repite 1.5 con esa configuración. Docker necesita el proxy en dos lugares.

**1. El servicio de Docker**, para descargar imágenes:

```bash
sudo mkdir -p /etc/systemd/system/docker.service.d
```

```bash
sudo tee /etc/systemd/system/docker.service.d/proxy.conf > /dev/null <<'EOF'
[Service]
Environment="HTTP_PROXY=http://PROXY:PUERTO"
Environment="HTTPS_PROXY=http://PROXY:PUERTO"
Environment="NO_PROXY=localhost,127.0.0.1,10.43.0.0/16"
EOF
```

En este bloque, reemplaza `PROXY:PUERTO` por los datos reales.

```bash
sudo systemctl daemon-reload && sudo systemctl restart docker
```

**2. Las compilaciones**, que descargan dependencias de Maven, NuGet y npm. Docker toma el proxy de `~/.docker/config.json` y se lo pasa a cada compilación:

```bash
mkdir -p ~/.docker && cat > ~/.docker/config.json <<'EOF'
{
  "proxies": {
    "default": {
      "httpProxy": "http://PROXY:PUERTO",
      "httpsProxy": "http://PROXY:PUERTO",
      "noProxy": "localhost,127.0.0.1,10.43.0.0/16,identity-service,campaign-service,donation-service,kafka"
    }
  }
}
EOF
```

- Reemplaza `PROXY:PUERTO` por los datos reales.
- Si el archivo ya existía (por ejemplo, tras un `docker login`), no lo sobrescribas: agrégale solo la clave `proxies`.
- `noProxy` evita que el tráfico entre VM (`10.43.0.0/16`) y entre contenedores (los nombres de los servicios) pase por el proxy.

### B.2 Si no hay ninguna salida a internet

Se construye en tu PC, que sí tiene internet, y se llevan las imágenes.

**En tu PC**, desde `~/Descargas/RedVital/Despliegue_RedVital`, para cada rol (`datos`, `servicios`, `borde`):

1. **Construir y descargar las imágenes:**

   ```bash
   docker compose -f vm-servicios/docker-compose.yml --env-file ambientes/qa.env --profile metricas build
   ```

   ```bash
   docker compose -f vm-servicios/docker-compose.yml --env-file ambientes/qa.env --profile metricas pull --ignore-buildable
   ```

2. **Guardarlas en un archivo:**

   ```bash
   docker save $(docker compose -f vm-servicios/docker-compose.yml --env-file ambientes/qa.env --profile metricas config --images) mcr.microsoft.com/dotnet/sdk:10.0 | gzip > imagenes-servicios.tar.gz
   ```

   La imagen del SDK de .NET solo hace falta en Servicios, para generar los secretos.

3. **Llevar el archivo** a la VM con `scp`.

**En la VM:**

1. Cárgalas con `docker load -i imagenes-servicios.tar.gz`.
2. En los pasos 4 a 6, usa `up -d --no-build --pull never` en lugar de `up -d --build`.

**Para Tools:** son las tres imágenes de `vm-tools/docker-compose.yml`. Usa `-f vm-tools/docker-compose.yml`, sin `--env-file`.

Docker también necesita instalarse sin internet: descarga en tu PC los `.deb` de `https://download.docker.com/linux/ubuntu/dists/` (los de la versión de Ubuntu de la VM) e instálalos con `sudo apt-get install ./*.deb`.

---

## Anexo C — Empezar de cero en una VM

Úsalo cuando una VM tenga restos de un intento anterior, o para dejar QA de fábrica.

**Borra los datos de esa VM:** bases, Kafka o la CA de Caddy, según el rol.

| VM | Comando |
|---|---|
| Datos | `docker compose -f vm-datos/docker-compose.yml --env-file ambientes/qa.env down -v` |
| Servicios | `docker compose -f vm-servicios/docker-compose.yml --env-file ambientes/qa.env down -v` |
| Borde | `docker compose -f vm-borde/docker-compose.yml --env-file ambientes/qa.env down -v` |
| Tools | `docker compose -f vm-tools/docker-compose.yml down -v` |

Si `qa.env` ya no existe, el comando falla. Borra a mano los volúmenes que mostró el paso 1.9:

```bash
docker volume ls --format '{{.Name}}' | grep -i redvital
```

**Para rehacer todo QA:**
1. Ejecuta `down -v` en el orden Tools → Borde → Servicios → Datos.
2. En Servicios, borra `secretos/` y repite desde el paso 3.
3. Levanta en el orden Datos → Servicios → Borde → Tools.
4. Reparte la CA nueva (6.4).
