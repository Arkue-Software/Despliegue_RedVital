#!/usr/bin/env python3
"""RedVital — prueba de extremo a extremo de un ambiente de QA.

Recorre el sistema SOLO por el borde (https://<dominio>/api/...), como lo hace
el navegador: Caddy -> APISIX -> servicio -> base, y Kafka entre los
servicios, que nunca se llaman entre sí. Lee las claves de los archivos de
secretos y nunca las imprime.

    python3 pruebas/extremo_a_extremo.py --base https://localhost \
        --ca /ruta/raiz-caddy.crt --secretos secretos/qa

Requiere el perfil "semillas" (cuentas U3–U7 y campañas de QA).
"""
import argparse
import http.cookiejar
import json
import random
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

# Las claves solo se leen de la carpeta de secretos de este repo, y el ambiente
# solo puede ser uno de los conocidos: ningún argumento llega sin validar a un
# archivo o a docker.
SECRETOS = (Path(__file__).resolve().parent.parent / "secretos").resolve()
PROYECTOS_SERVICIOS = {"qa": "redvital-qa-servicios", "produccion": "redvital-produccion-servicios"}

CAMPANIA_VIGENTE = "d4000000-0000-4000-8000-000000000001"   # Aurora, publicada y vigente
CAMPANIA_BORRADOR = "d4000000-0000-4000-8000-000000000003"  # Aurora, borrador
AURORA = "a1000000-0000-4000-8000-000000000001"              # Medellín (semilla institucional)
CEIBA = "a1000000-0000-4000-8000-000000000002"               # Bello
SABANA = "a1000000-0000-4000-8000-000000000006"              # Bogotá

resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion)))
    marca = "OK  " if condicion else "FALLA"
    print(f"  [{marca}] {nombre}" + (f" — {detalle}" if detalle and not condicion else ""))
    return condicion


class Cliente:
    """Un navegador mínimo: cookies propias, token en memoria, mismo origen."""

    def __init__(self, base, contexto):
        self.base = base.rstrip("/")
        self.cookies = http.cookiejar.CookieJar()
        self.abridor = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=contexto),
            urllib.request.HTTPCookieProcessor(self.cookies),
        )
        self.token = None

    def pedir(self, metodo, ruta, cuerpo=None, idempotente=False, token=None, cabeceras=None, clave=None):
        datos = None if cuerpo is None else json.dumps(cuerpo).encode()
        peticion = urllib.request.Request(self.base + ruta, data=datos, method=metodo)
        peticion.add_header("Accept", "application/json")
        if datos is not None:
            peticion.add_header("Content-Type", "application/json")
        if idempotente or clave:
            peticion.add_header("Idempotency-Key", clave or str(uuid.uuid4()))
        portador = token if token is not None else self.token
        if portador:
            peticion.add_header("Authorization", f"Bearer {portador}")
        for clave, valor in (cabeceras or {}).items():
            peticion.add_header(clave, valor)
        try:
            with self.abridor.open(peticion, timeout=20) as r:
                texto = r.read().decode()
                es_json = "json" in r.headers.get("Content-Type", "")
                return r.status, (json.loads(texto) if texto and es_json else None), dict(r.headers)
        except urllib.error.HTTPError as e:
            texto = e.read().decode()
            try:
                cuerpo_error = json.loads(texto) if texto else None
            except ValueError:
                cuerpo_error = {"texto": texto[:200]}
            return e.code, cuerpo_error, dict(e.headers)

    def entrar(self, correo, clave, cabeceras=None):
        estado, cuerpo, _ = self.pedir("POST", "/api/v1/sesiones", {"correo": correo, "credencial": clave},
                                       cabeceras=cabeceras)
        if estado == 200:
            self.token = cuerpo["token_acceso"]
        return estado


def leer_clave_qa(directorio):
    """La clave de las cuentas de QA, solo si el directorio está dentro de secretos/."""
    ruta = (Path(directorio).resolve() / "clave_cuentas_qa").resolve()
    if not ruta.is_relative_to(SECRETOS):
        sys.exit(f"--secretos debe ser una carpeta dentro de {SECRETOS}")
    return ruta.read_text().strip()


def esperar(condicion, segundos=40):
    limite = time.time() + segundos
    while time.time() < limite:
        valor = condicion()
        if valor:
            return valor
        time.sleep(2)
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="https://localhost")
    p.add_argument("--ca", required=True, help="raíz de la CA del borde (Caddy tls internal)")
    p.add_argument("--secretos", required=True)
    a = p.parse_args()

    contexto = ssl.create_default_context(cafile=a.ca)
    clave_qa = leer_clave_qa(a.secretos)

    print("1. Borde y seguridad")
    anonimo = Cliente(a.base, contexto)
    estado, _, cab = anonimo.pedir("GET", "/")
    comprobar("la web responde por HTTPS", estado == 200)
    comprobar("CSP default-src 'self'", "default-src 'self'" in cab.get("Content-Security-Policy", ""))
    comprobar("HSTS", "max-age" in cab.get("Strict-Transport-Security", ""))
    comprobar("X-Frame-Options DENY", cab.get("X-Frame-Options") == "DENY")
    comprobar("sin cabecera Server", "Server" not in cab)
    estado, _, _ = anonimo.pedir("POST", "/api/internal/v1/sesiones/servicio", {})
    comprobar("/api/internal -> 404", estado == 404, str(estado))
    estado, _, _ = anonimo.pedir("GET", "/api/v1/unidades")
    comprobar("sin token -> 401", estado == 401, str(estado))
    # Token falso a propósito: debe responder 401.
    estado, _, _ = anonimo.pedir("GET", "/api/v1/usuarios/me", token="eyJhbGciOiJSUzI1NiJ9.e30.firma-falsa")  # gitleaks:allow
    comprobar("token alterado -> 401", estado == 401, str(estado))
    estado, _, cab = anonimo.pedir("GET", "/api/v1/campanias")
    comprobar("X-Correlacion-Id en la respuesta", bool(cab.get("X-Correlacion-Id")))

    print("2. Visitante (U1): campañas y registro anónimo")
    estado, campanias, _ = anonimo.pedir("GET", "/api/v1/campanias")
    publicas = {c["id"] for c in (campanias or [])}
    comprobar("listado público de campañas", estado == 200 and CAMPANIA_VIGENTE in publicas, str(estado))
    borrador_publico = CAMPANIA_BORRADOR in publicas
    estado, intencion, _ = anonimo.pedir("POST", "/api/v1/intenciones",
                                          {"grupo_sanguineo": "O+", "municipio_ruta": "/00/05/05001"}, idempotente=True)
    comprobar("registro anónimo -> 201 con código", estado == 201 and len(intencion.get("codigo", "")) == 12, str(estado))
    codigo = (intencion or {}).get("codigo", "X")
    estado, consulta, _ = anonimo.pedir("GET", f"/api/v1/intenciones/{codigo}")
    comprobar("consulta por código: pendiente", estado == 200 and consulta["estado"] == "pendiente", str(estado))
    comprobar("el visitante no ve grupo ni municipio", consulta and consulta.get("grupo_sanguineo") is None)

    print("3. Crear cuenta de donante (U2): cuenta en Identidad, sesión y perfil en Donación")
    documento = str(random.randint(10**9, 10**10 - 1))
    correo = f"donante.{uuid.uuid4().hex[:8]}@redvital.test"
    credencial = "Prueba" + uuid.uuid4().hex[:10] + "7"
    cuenta = {"correo": correo, "credencial": credencial, "nombre": "Valentina Ríos"}
    clave_cuenta = str(uuid.uuid4())
    estado, creada, _ = anonimo.pedir("POST", "/api/v1/cuentas-donante", cuenta, clave=clave_cuenta)
    comprobar("crear la cuenta -> 201", estado == 201 and creada.get("usuario_id"), str(estado))
    estado, reenvio, _ = anonimo.pedir("POST", "/api/v1/cuentas-donante", cuenta, clave=clave_cuenta)
    comprobar("reenviar con la misma clave -> la misma cuenta", estado == 200 and reenvio == creada, str(estado))
    estado, _, _ = anonimo.pedir("POST", "/api/v1/cuentas-donante", cuenta, idempotente=True)
    comprobar("el mismo correo con otra clave -> 409", estado == 409, str(estado))
    perfil_nuevo = {
        "documento": documento, "nombre": "Valentina Ríos", "fecha_nacimiento": "1994-03-12",
        "correo": correo, "telefono": "3001234567", "municipio_ruta": "/00/05/05001",
        "autoriza_tratamiento_datos": True, "autoriza_avisos_campanas": False, "version_aviso": "AV-2026.1",
    }
    estado, _, _ = anonimo.pedir("POST", "/api/v1/donantes", perfil_nuevo, idempotente=True)
    comprobar("el perfil sin sesión -> 401", estado == 401, str(estado))

    donante = Cliente(a.base, contexto)
    comprobar("el donante inicia sesión", donante.entrar(correo, credencial) == 200)
    estado, _, _ = donante.pedir("GET", "/api/v1/donantes/me")
    comprobar("cuenta sin perfil todavía -> 404", estado == 404, str(estado))
    menor = dict(perfil_nuevo, fecha_nacimiento=time.strftime("%Y-%m-%d", time.gmtime(time.time() - 16 * 365.25 * 86400)))
    estado, problema, _ = donante.pedir("POST", "/api/v1/donantes", menor, idempotente=True)
    comprobar("un menor de edad no crea perfil -> 422", estado == 422 and (problema or {}).get("tipo") == "menor-de-edad",
              f"{estado} {problema}")
    estado, _, _ = donante.pedir("POST", "/api/v1/donantes", perfil_nuevo, idempotente=True)
    comprobar("crear el perfil con la sesión -> 201", estado == 201, str(estado))
    estado, _, _ = donante.pedir("POST", "/api/v1/donantes", perfil_nuevo, idempotente=True)
    comprobar("un segundo perfil para la cuenta -> 409", estado == 409, str(estado))
    otra = Cliente(a.base, contexto)
    otro_correo = f"donante.{uuid.uuid4().hex[:8]}@redvital.test"
    otra.pedir("POST", "/api/v1/cuentas-donante", dict(cuenta, correo=otro_correo), idempotente=True)
    otra.entrar(otro_correo, credencial)
    estado, _, _ = otra.pedir("POST", "/api/v1/donantes", dict(perfil_nuevo, correo=otro_correo), idempotente=True)
    comprobar("el mismo documento en otra cuenta -> 409", estado == 409, str(estado))
    estado, yo, _ = donante.pedir("GET", "/api/v1/usuarios/me")
    comprobar("usuarios/me: rol donante", estado == 200 and yo["rol"] == "donante", str(estado))
    estado, perfil, _ = donante.pedir("GET", "/api/v1/donantes/me")
    comprobar("perfil del donante", estado == 200 and perfil["nombre"] == "Valentina Ríos", str(estado))
    estado, eleg, _ = donante.pedir("GET", "/api/v1/donantes/me/elegibilidad")
    comprobar("elegible (sin donaciones)", estado == 200 and eleg["elegible"] is True, str(estado))
    estado, cons, _ = donante.pedir("GET", "/api/v1/donantes/me/consentimientos")
    finalidades = {c["finalidad"]: c["otorgado"] for c in (cons or [])}
    comprobar("dos consentimientos separados", finalidades.get("tratamiento_datos") is True and
              finalidades.get("avisos_campanas") is False, str(finalidades))
    estado, _, _ = donante.pedir("GET", "/api/v1/unidades")
    comprobar("el donante no ve unidades -> 403", estado == 403, str(estado))
    # Renovación y cierre con la otra cuenta: la sesión de Valentina sigue para ver su historial.
    estado, renovado, _ = otra.pedir("POST", "/api/v1/sesiones/renovacion", token="")
    comprobar("renovación con la cookie", estado == 200 and renovado.get("token_acceso"), str(estado))
    if estado == 200:
        otra.token = renovado["token_acceso"]
    estado, _, _ = otra.pedir("DELETE", "/api/v1/sesiones/actual")
    comprobar("cerrar sesión -> 204", estado == 204, str(estado))
    estado, _, _ = otra.pedir("POST", "/api/v1/sesiones/renovacion", token="")
    comprobar("tras cerrar, la renovación se rechaza", estado == 401, str(estado))
    comprobar("credencial incorrecta -> 401", Cliente(a.base, contexto).entrar(correo, "incorrecta123") == 401)

    print("4. Operador (U3): donación, fraccionamiento y ciclo de vida")
    operador = Cliente(a.base, contexto)
    comprobar("el operador inicia sesión", operador.entrar("operador@redvital.test", clave_qa) == 200)
    estado, presente, _ = operador.pedir("POST", "/api/v1/donantes/busqueda", {"documento": documento})
    comprobar("busca al donante por documento", estado == 200 and presente["elegibilidad"]["elegible"], str(estado))
    proyectada = esperar(lambda: operador.pedir("POST", "/api/v1/donaciones", {
        "donante_id": presente["id"], "grupo_sanguineo": "O+", "campania_id": CAMPANIA_VIGENTE,
        "componentes": [{"componente": "globulos_rojos", "volumen_ml": 280},
                        {"componente": "plasma", "volumen_ml": None},
                        {"componente": "plaquetas", "volumen_ml": None}]}, idempotente=True), 5)
    estado, donacion, _ = proyectada
    comprobar("registrar donación -> 201", estado == 201, f"{estado} {donacion}")
    comprobar("campaña confirmada por la proyección (EV-03)", donacion and donacion.get("campania_confirmada") is True)
    unidades = (donacion or {}).get("unidades", [])
    comprobar("una unidad captada por componente", len(unidades) == 3 and all(u["estado"] == "captada" for u in unidades))
    estado, donacion, _ = operador.pedir("POST", f"/api/v1/donaciones/{donacion['id']}/fraccionamiento", idempotente=True)
    comprobar("fraccionamiento -> todas fraccionadas", estado == 200 and all(
        u["estado"] == "fraccionada" for u in donacion["unidades"]), str(estado))
    unidad = donacion["unidades"][0]["id"]
    estado, t, _ = operador.pedir("POST", f"/api/v1/unidades/{unidad}/ingreso-tamizaje")
    comprobar("ingreso a tamizaje", estado == 200 and t["estado_nuevo"] == "en_tamizaje", str(estado))
    estado, _, _ = operador.pedir("POST", f"/api/v1/unidades/{unidad}/tamizaje", {"apta": False, "motivo": "x"})
    comprobar("tamizaje no admite motivo -> 400", estado == 400, str(estado))
    estado, t, _ = operador.pedir("POST", f"/api/v1/unidades/{unidad}/tamizaje", {"apta": False})
    comprobar("tamizaje: no apta", estado == 200 and t["estado_nuevo"] == "no_apta", str(estado))
    estado, _, _ = operador.pedir("POST", f"/api/v1/unidades/{unidad}/reserva")
    comprobar("reservar una no apta -> 409", estado == 409, str(estado))
    estado, _, _ = operador.pedir("POST", f"/api/v1/unidades/{unidad}/disposicion-final",
                                  {"confirmacion": "otra-cosa"}, idempotente=True)
    comprobar("disposición con confirmación errónea -> 422", estado == 422, str(estado))
    estado, t, _ = operador.pedir("POST", f"/api/v1/unidades/{unidad}/disposicion-final",
                                  {"confirmacion": unidad}, idempotente=True)
    comprobar("disposición final -> desechada", estado == 200 and t["estado_nuevo"] == "desechada", str(estado))
    estado, eventos, _ = operador.pedir("GET", f"/api/v1/unidades/{unidad}/eventos")
    comprobar("recorrido de 5 eventos", estado == 200 and len(eventos) == 5, str(len(eventos or [])))

    ceiba = Cliente(a.base, contexto)
    ceiba.entrar("operador.ceiba@redvital.test", clave_qa)
    estado, _, _ = ceiba.pedir("GET", f"/api/v1/unidades/{unidad}")
    comprobar("otro banco no ve la unidad -> 404", estado == 404, str(estado))

    print("5. Kafka: EV-01 (Donación -> Campañas), EV-03 (Campañas -> Donación) y EV-07 (Institucional -> ambos)")
    admin = Cliente(a.base, contexto)
    comprobar("el administrador de banco inicia sesión", admin.entrar("admin.banco@redvital.test", clave_qa) == 200)

    def conteo():
        _, lista, _ = admin.pedir("GET", "/api/v1/campanias")
        c = next((x for x in lista or [] if x["id"] == CAMPANIA_VIGENTE), None)
        return c and c.get("donaciones_registradas", 0) >= 1 and c["donaciones_registradas"]
    comprobar("EV-01 sube donaciones_registradas en Campañas", esperar(conteo))
    _, lista, _ = anonimo.pedir("GET", "/api/v1/campanias")
    vigente = next((c for c in lista or [] if c["id"] == CAMPANIA_VIGENTE), {})
    comprobar("la jornada pública trae el nombre de su banco (EV-07)", "Aurora" in (vigente.get("institucion_nombre") or ""),
              str(vigente.get("institucion_nombre")))
    _, mias, _ = donante.pedir("GET", "/api/v1/donantes/me/donaciones")
    comprobar("el historial del donante nombra el banco y la jornada", bool(mias) and
              "Aurora" in (mias[0].get("institucion_nombre") or "") and bool(mias[0].get("campania_nombre")), str(mias))
    _, propias, _ = admin.pedir("GET", "/api/v1/campanias")
    borrador = next((c for c in propias or [] if c["id"] == CAMPANIA_BORRADOR), None)
    if borrador and borrador["estado"] == "borrador":
        comprobar("el borrador no era público", not borrador_publico)
        estado, publicada, _ = admin.pedir("POST", f"/api/v1/campanias/{CAMPANIA_BORRADOR}/publicacion")
        comprobar("publicar la campaña borrador", estado == 200 and publicada["estado"] == "publicada", str(estado))
    else:
        print("  [--  ] la campaña borrador ya se publicó en una corrida anterior")
        comprobar("la campaña publicada es pública", borrador_publico)
    estado, _, _ = admin.pedir("POST", "/api/v1/donaciones", {
        "grupo_sanguineo": "A+", "componentes": [{"componente": "plasma"}]}, idempotente=True)
    comprobar("el administrador no registra donaciones -> 403", estado == 403, str(estado))
    estado, umbrales, _ = admin.pedir("GET", "/api/v1/inventario/umbrales")
    comprobar("umbrales sembrados de su banco", estado == 200 and len(umbrales) == 4, str(estado))
    # Cambiar un umbral reevalúa las alertas en la misma operación, sin esperar al proceso programado.
    campos = ("componente", "grupo_sanguineo", "minimo_unidades", "dias_previos_vencimiento")
    sembrado = {k: (umbrales or [{}])[0].get(k) for k in campos}

    def escasez_abierta():
        _, abiertas, _ = admin.pedir("GET", "/api/v1/alertas?estado=abierta")
        return any(x["tipo"] == "escasez" and x["componente"] == sembrado["componente"]
                   and x["grupo_sanguineo"] == sembrado["grupo_sanguineo"] for x in abiertas or [])
    habia = escasez_abierta()
    estado, _, _ = admin.pedir("PUT", "/api/v1/inventario/umbrales", {"umbrales": [dict(sembrado, minimo_unidades=10000)]})
    comprobar("subir un umbral abre su alerta de inmediato", estado == 200 and escasez_abierta(), str(estado))
    estado, _, _ = admin.pedir("PUT", "/api/v1/inventario/umbrales", {"umbrales": [sembrado]})
    comprobar("devolverlo deja las alertas como estaban, de inmediato", estado == 200 and escasez_abierta() == habia, str(estado))

    print("6. Institucional: territorios, instituciones, alcance territorial, indicadores y bitácora")
    # Una correlación propia, para encontrar después el mismo recorrido en las cuatro series.
    correlacion = str(uuid.uuid4())
    marca = {"X-Correlacion-Id": correlacion}
    coordinador = Cliente(a.base, contexto)
    comprobar("el coordinador (Antioquia) inicia sesión", coordinador.entrar("coordinador@redvital.test", clave_qa) == 200)
    estado, deps, _ = coordinador.pedir("GET", "/api/v1/territorios?nivel=departamental")
    comprobar("territorios acotados a su jurisdicción", estado == 200 and [t["ruta"] for t in deps["elementos"]] == ["/00/05"],
              str(estado))
    estado, insts, _ = coordinador.pedir("GET", "/api/v1/instituciones?tamano=100")
    ids = {i["id"] for i in (insts or {}).get("elementos", [])}
    comprobar("instituciones de Antioquia, no las de Bogotá", estado == 200 and {AURORA, CEIBA} <= ids and SABANA not in ids,
              str(estado))
    estado, _, _ = coordinador.pedir("GET", "/api/v1/instituciones?territorio_ruta=/00/11")
    comprobar("otra ruta -> 403 fuera de jurisdicción", estado == 403, str(estado))
    estado, _, _ = coordinador.pedir("POST", "/api/v1/territorios", {"codigo_divipola": "05002", "nombre": "Abejorral"})
    comprobar("el coordinador no da de alta territorios -> 403", estado == 403, str(estado))
    # Resolver por id es común a todo perfil con cuenta (DD 15.5), incluido el operador.
    estado, aurora, _ = operador.pedir("GET", f"/api/v1/instituciones/{AURORA}")
    comprobar("el operador resuelve su banco: Medellín", estado == 200 and aurora["territorio_ruta"] == "/00/05/05001", str(estado))
    estado, _, _ = operador.pedir("GET", "/api/v1/instituciones")
    comprobar("el operador no lista instituciones -> 403", estado == 403, str(estado))

    nacional = Cliente(a.base, contexto)
    comprobar("el administrador nacional inicia sesión", nacional.entrar("admin.nacional@redvital.test", clave_qa) == 200)
    estado, municipios, _ = nacional.pedir("GET", "/api/v1/territorios?nivel=municipal&tamano=500")
    medellin = next((t for t in (municipios or {}).get("elementos", []) if t["codigo_divipola"] == "05001"), None)
    comprobar("el nacional ve todos los municipios", estado == 200 and medellin is not None, str(estado))
    estado, _, _ = nacional.pedir("POST", "/api/v1/territorios", {"codigo_divipola": "05002", "nombre": "Abejorral"})
    comprobar("alta de territorio por U6 (201, o 409 si ya existe)", estado in (201, 409), str(estado))
    codigo_inst = "E2E-" + uuid.uuid4().hex[:8].upper()
    estado, nueva, _ = nacional.pedir("POST", "/api/v1/instituciones", {
        "codigo": codigo_inst, "nombre": "Banco E2E (ficticio)", "tipo": "banco_de_sangre",
        "territorio_id": medellin and medellin["id"]}, cabeceras=marca)
    comprobar("alta de institución -> 201 en Medellín", estado == 201 and nueva["territorio_ruta"] == "/00/05/05001", str(estado))
    nueva_id = (nueva or {}).get("id", AURORA)
    estado, cambio, _ = coordinador.pedir("PUT", f"/api/v1/instituciones/{nueva_id}", {"nombre": "Banco E2E renombrado (ficticio)"})
    comprobar("el coordinador la cambia en su jurisdicción", estado == 200 and cambio["nombre"].startswith("Banco E2E renombrado"),
              str(estado))
    estado, _, _ = coordinador.pedir("PUT", f"/api/v1/instituciones/{nueva_id}", {"tipo": "servicio_transfusion"})
    comprobar("el tipo no cambia -> 409", estado == 409, str(estado))
    estado, _, _ = coordinador.pedir("PUT", f"/api/v1/instituciones/{nueva_id}", {"vigente": False})
    comprobar("baja de la institución", estado == 200, str(estado))

    estado, nueva_camp, _ = admin.pedir("POST", "/api/v1/campanias", {
        "nombre": "Jornada E2E", "sede": "Parque de los Deseos, Medellín",
        "inicia_en": time.strftime("%Y-%m-%dT13:00:00Z", time.gmtime(time.time() + 3 * 86400)),
        "termina_en": time.strftime("%Y-%m-%dT22:00:00Z", time.gmtime(time.time() + 3 * 86400)),
        "cupo_total": 30}, cabeceras=marca)
    comprobar("crear campaña: borrador con la ruta de su copia de instituciones", estado == 201 and nueva_camp["estado"] == "borrador"
              and nueva_camp["territorio_ruta"] == "/00/05/05001", f"{estado} {nueva_camp}")
    if estado == 201:
        estado, editada, _ = admin.pedir("PUT", f"/api/v1/campanias/{nueva_camp['id']}", {"cupo_total": 45})
        comprobar("editar el borrador", estado == 200 and editada["cupo_total"] == 45, str(estado))
    estado, _, _ = coordinador.pedir("POST", "/api/v1/campanias", {"nombre": "x"})
    comprobar("el coordinador no crea campañas -> 403", estado == 403, str(estado))

    estado, pagina, _ = coordinador.pedir("GET", "/api/v1/unidades?tamano=100")
    custodias = {u["institucion_custodia_id"] for u in (pagina or {}).get("elementos", [])}
    comprobar("el coordinador lee las unidades de su territorio", estado == 200 and AURORA in custodias, str(estado))
    estado, _, _ = coordinador.pedir("GET", "/api/v1/inventario")
    comprobar("el coordinador lee el inventario de su territorio", estado == 200, str(estado))
    estado, _, _ = coordinador.pedir("POST", f"/api/v1/unidades/{unidad}/ingreso-tamizaje")
    comprobar("el coordinador no opera unidades -> 403", estado == 403, str(estado))
    estado, _, _ = operador.pedir("GET", f"/api/v1/inventario?institucion_id={SABANA}", cabeceras=marca)
    comprobar("el operador pide otro banco -> 403 (auditado en Donación)", estado == 403, str(estado))

    # EV-09: Donación publica la fotografía de M7 cuando cambia, cada minuto.
    def indicadores_al_dia():
        estado, ind, _ = admin.pedir("GET", "/api/v1/indicadores?agrupar_por=mes")
        return estado == 200 and ind.get("actualizado_en") and sum(p["donaciones"] for p in ind["captacion"]) >= 1 and ind
    ind = esperar(indicadores_al_dia, 150)
    estado = 200 if ind else 0
    comprobar("indicadores del banco llegan por Kafka (EV-09)", bool(ind) and ind["cobertura"]
              and ind["filtro"]["institucion_id"] == AURORA and ind["inventario"] is not None, str(ind and ind.get("actualizado_en")))
    comprobar("supresión por agregación declarada en cada periodo",
              estado == 200 and all("suprimido_por_agregacion" in p for p in ind["captacion"]))
    estado, ind, _ = coordinador.pedir("GET", "/api/v1/indicadores?territorio_ruta=/00/05")
    comprobar("indicadores del departamento para el coordinador", estado == 200, str(estado))
    estado, _, _ = coordinador.pedir("GET", "/api/v1/indicadores?territorio_ruta=/00/11")
    comprobar("indicadores de otra ruta -> 403", estado == 403, str(estado))
    estado, _, _ = operador.pedir("GET", "/api/v1/indicadores")
    comprobar("el operador no ve indicadores -> 403", estado == 403, str(estado))

    auditor = Cliente(a.base, contexto)
    comprobar("el auditor inicia sesión", auditor.entrar("auditor@redvital.test", clave_qa, cabeceras=marca) == 200)
    # EV-10: cada serie llega a Institucional por Kafka, en segundos.
    def recorrido():
        _, bitacora, _ = auditor.pedir("GET", f"/api/v1/auditoria?correlacion_id={correlacion}&tamano=50")
        contextos = {r["contexto"] for r in (bitacora or {}).get("registros", [])}
        return contextos == {"identidad", "campanias", "donacion", "institucional"} and bitacora
    bitacora = esperar(recorrido, 60)
    comprobar("un recorrido en las cuatro series (EV-10)", bool(bitacora), str(bitacora and sorted({r["contexto"] for r in bitacora["registros"]})))
    comprobar("cada serie dice hasta cuándo está al día", bool(bitacora) and {x["contexto"] for x in bitacora["series"]}
              == {"identidad", "campanias", "donacion", "institucional"} and all(x["al_dia_hasta"] for x in bitacora["series"]))
    estado, pagina, _ = auditor.pedir("GET", "/api/v1/auditoria?tamano=5")
    comprobar("bitácora paginada con cursor", estado == 200 and len(pagina["registros"]) == 5 and pagina["siguiente"], str(estado))
    if estado == 200 and pagina["siguiente"]:
        estado, otra, _ = auditor.pedir("GET", f"/api/v1/auditoria?tamano=5&antes_de={pagina['siguiente']}")
        comprobar("la página siguiente no repite registros", estado == 200 and not (
            {r["id"] for r in otra["registros"]} & {r["id"] for r in pagina["registros"]}), str(estado))
    estado, _, _ = auditor.pedir("GET", f"/api/v1/unidades/{unidad}/eventos")
    comprobar("el auditor lee la trazabilidad de una unidad", estado == 200, str(estado))
    estado, _, _ = auditor.pedir("POST", "/api/v1/instituciones", {"codigo": "AUD-0001"})
    comprobar("el auditor no escribe nada -> 403", estado == 403, str(estado))
    estado, _, _ = admin.pedir("GET", "/api/v1/auditoria")
    comprobar("la bitácora es solo del auditor -> 403", estado == 403, str(estado))
    estado, _, _ = anonimo.pedir("GET", "/api/internal/v1/auditoria/denegaciones")
    comprobar("/api/internal/v1/auditoria -> 404", estado == 404, str(estado))

    print("7. Límite de tasa del inicio de sesión")
    rafaga = Cliente(a.base, contexto)
    codigos = [rafaga.entrar("nadie@redvital.test", "incorrecta123") for _ in range(12)]
    comprobar("tras 10 intentos por minuto -> 429", 429 in codigos, str(codigos))

    fallas = [n for n, ok in resultados if not ok]
    print(f"\n{len(resultados) - len(fallas)}/{len(resultados)} comprobaciones correctas")
    for n in fallas:
        print(f"  falla: {n}")
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())
