#!/usr/bin/env python3
"""RedVital — prueba de extremo a extremo de un ambiente de QA.

Recorre el sistema SOLO por el borde (https://<dominio>/api/...), como lo hace
el navegador: Caddy -> APISIX -> servicio -> base, y Kafka entre Campañas y
Donación. Lee las claves de los archivos de secretos y nunca las imprime.

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

CAMPANIA_VIGENTE = "d4000000-0000-4000-8000-000000000001"   # Aurora, publicada y vigente
CAMPANIA_BORRADOR = "d4000000-0000-4000-8000-000000000003"  # Aurora, borrador

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

    def pedir(self, metodo, ruta, cuerpo=None, idempotente=False, token=None, cabeceras=None):
        datos = None if cuerpo is None else json.dumps(cuerpo).encode()
        peticion = urllib.request.Request(self.base + ruta, data=datos, method=metodo)
        peticion.add_header("Accept", "application/json")
        if datos is not None:
            peticion.add_header("Content-Type", "application/json")
        if idempotente:
            peticion.add_header("Idempotency-Key", str(uuid.uuid4()))
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

    def entrar(self, correo, clave):
        estado, cuerpo, _ = self.pedir("POST", "/api/v1/sesiones", {"correo": correo, "credencial": clave})
        if estado == 200:
            self.token = cuerpo["token_acceso"]
        return estado


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
    clave_qa = open(f"{a.secretos}/clave_cuentas_qa").read().strip()

    print("1. Borde y seguridad")
    anonimo = Cliente(a.base, contexto)
    estado, _, cab = anonimo.pedir("GET", "/")
    comprobar("la web responde por HTTPS", estado == 200)
    comprobar("CSP default-src 'self'", "default-src 'self'" in cab.get("Content-Security-Policy", ""))
    comprobar("HSTS", "max-age" in cab.get("Strict-Transport-Security", ""))
    comprobar("X-Frame-Options DENY", cab.get("X-Frame-Options") == "DENY")
    comprobar("sin cabecera Server", "Server" not in cab)
    estado, _, _ = anonimo.pedir("POST", "/api/internal/v1/cuentas-donante", {})
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

    print("3. Crear cuenta de donante (U2), sesión y cierre")
    documento = str(random.randint(10**9, 10**10 - 1))
    correo = f"donante.{uuid.uuid4().hex[:8]}@redvital.test"
    credencial = "Prueba" + uuid.uuid4().hex[:10] + "7"
    registro = {
        "documento": documento, "nombre": "Valentina Ríos", "fecha_nacimiento": "1994-03-12",
        "correo_acceso": correo, "credencial": credencial, "correo": correo, "telefono": "3001234567",
        "municipio_ruta": "/00/05/05001", "autoriza_tratamiento_datos": True, "autoriza_avisos_campanas": False,
        "version_aviso": "AV-2026.1",
    }
    estado, _, _ = anonimo.pedir("POST", "/api/v1/donantes", registro, idempotente=True)
    comprobar("crear cuenta -> 201", estado == 201, str(estado))
    estado, _, _ = anonimo.pedir("POST", "/api/v1/donantes", registro, idempotente=True)
    comprobar("documento repetido -> 409", estado == 409, str(estado))

    donante = Cliente(a.base, contexto)
    comprobar("el donante inicia sesión", donante.entrar(correo, credencial) == 200)
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
    estado, renovado, _ = donante.pedir("POST", "/api/v1/sesiones/renovacion", token="")
    comprobar("renovación con la cookie", estado == 200 and renovado.get("token_acceso"), str(estado))
    if estado == 200:
        donante.token = renovado["token_acceso"]
    estado, _, _ = donante.pedir("DELETE", "/api/v1/sesiones/actual")
    comprobar("cerrar sesión -> 204", estado == 204, str(estado))
    estado, _, _ = donante.pedir("POST", "/api/v1/sesiones/renovacion", token="")
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

    print("5. Kafka: EV-01 (Donación -> Campañas) y EV-03 (Campañas -> Donación)")
    admin = Cliente(a.base, contexto)
    comprobar("el administrador de banco inicia sesión", admin.entrar("admin.banco@redvital.test", clave_qa) == 200)

    def conteo():
        _, lista, _ = admin.pedir("GET", "/api/v1/campanias")
        c = next((x for x in lista or [] if x["id"] == CAMPANIA_VIGENTE), None)
        return c and c.get("donaciones_registradas", 0) >= 1 and c["donaciones_registradas"]
    comprobar("EV-01 sube donaciones_registradas en Campañas", esperar(conteo))
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

    print("6. Límite de tasa del inicio de sesión")
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
