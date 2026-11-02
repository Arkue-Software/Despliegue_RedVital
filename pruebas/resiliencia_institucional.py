#!/usr/bin/env python3
"""RedVital — prueba de resiliencia del Servicio Institucional en QA.

Ningún servicio llama a otro: Institucional publica las instituciones (EV-07)
y compone indicadores y bitácora con lo que recibe por Kafka (EV-09 y EV-10);
Campañas y Donación guardan su propia copia de las instituciones. Esta prueba
comprueba que la caída de uno no detiene a los demás y que, al volver, todos
se ponen al día solos:

  - Con Donación detenida, los indicadores siguen respondiendo con la última
    fotografía recibida, y la bitácora y M6 siguen atendiendo.
  - Con Campañas detenida, un cambio de nombre de una institución espera en
    Kafka y Campañas lo aplica al volver.
  - Con Institucional detenido, se crean campañas (con la copia local de
    Campañas), se registran donaciones y Donación resuelve el alcance
    territorial con su copia. Los registros de auditoría que se generan
    mientras tanto llegan a la bitácora cuando Institucional vuelve.
  - Todas las respuestas llegan en menos de 4 s, y los servicios siguen sanos.

Se corre en la VM de servicios, porque detiene y arranca contenedores con
docker; las peticiones van por el borde:

    python3 pruebas/resiliencia_institucional.py --base https://<ip-borde> \\
        --ca ~/raiz-caddy.crt --secretos secretos/qa

Requiere el perfil "semillas". Deja los servicios encendidos y el nombre del
banco como estaba al terminar, aunque algo falle.
"""
import argparse
import ssl
import subprocess
import sys
import time
import uuid

from extremo_a_extremo import AURORA, Cliente, comprobar, esperar, resultados

PLAZO = 4.0


def docker(*argumentos):
    return subprocess.run(["docker", *argumentos], capture_output=True, text=True, check=True).stdout.strip()


def sano(contenedor):
    return docker("inspect", "-f", "{{.State.Health.Status}}", contenedor) == "healthy"


def cronometrado(funcion):
    inicio = time.monotonic()
    resultado = funcion()
    return resultado, time.monotonic() - inicio


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="https://localhost")
    p.add_argument("--ca", required=True, help="raíz de la CA del borde (Caddy tls internal)")
    p.add_argument("--secretos", required=True)
    p.add_argument("--ambiente", default="qa")
    a = p.parse_args()

    contexto = ssl.create_default_context(cafile=a.ca)
    clave_qa = open(f"{a.secretos}/clave_cuentas_qa").read().strip()
    proyecto = f"redvital-{a.ambiente}-servicios"
    donacion = f"{proyecto}-donation-service-1"
    institucional = f"{proyecto}-institutional-service-1"
    campanias = f"{proyecto}-campaign-service-1"
    anonimo = Cliente(a.base, contexto)

    print("1. Sesiones")
    operador, admin, auditor, nacional = (Cliente(a.base, contexto) for _ in range(4))
    comprobar("el operador inicia sesión", operador.entrar("operador@redvital.test", clave_qa) == 200)
    comprobar("el administrador de banco inicia sesión", admin.entrar("admin.banco@redvital.test", clave_qa) == 200)
    comprobar("el auditor inicia sesión", auditor.entrar("auditor@redvital.test", clave_qa) == 200)
    comprobar("el administrador nacional inicia sesión", nacional.entrar("admin.nacional@redvital.test", clave_qa) == 200)
    estado, antes, _ = admin.pedir("GET", "/api/v1/indicadores")
    comprobar("con todo arriba, indicadores -> 200", estado == 200, str(estado))
    estado, aurora, _ = nacional.pedir("GET", f"/api/v1/instituciones/{AURORA}")
    nombre_original = (aurora or {}).get("nombre", "Banco de Sangre Aurora (ficticio)")

    try:
        print("2. Donación detenida")
        docker("stop", donacion)
        (estado, ind, _), segundos = cronometrado(lambda: admin.pedir("GET", "/api/v1/indicadores"))
        comprobar("indicadores -> 200 con la última fotografía recibida", estado == 200 and ind
                  and ind.get("actualizado_en") == (antes or {}).get("actualizado_en"), f"{estado}")
        comprobar(f"sin esperar ({segundos:.1f} s < {PLAZO} s)", segundos < PLAZO)
        (estado, _, _), segundos = cronometrado(lambda: auditor.pedir("GET", "/api/v1/auditoria?tamano=10"))
        comprobar("la bitácora sigue atendiendo", estado == 200, str(estado))
        comprobar(f"la bitácora no espera ({segundos:.1f} s < {PLAZO} s)", segundos < PLAZO)
        estado, _, _ = nacional.pedir("GET", "/api/v1/instituciones?tamano=5")
        comprobar("M6 sigue atendiendo sin Donación", estado == 200, str(estado))
        comprobar("Institucional sigue sano", sano(institucional))
    finally:
        docker("start", donacion)
    comprobar("Donación vuelve a estar sana", esperar(lambda: sano(donacion), 120))

    nuevo_nombre = f"{nombre_original} · {uuid.uuid4().hex[:4]}"
    try:
        print("3. Campañas detenida: el cambio de una institución espera en Kafka")
        docker("stop", campanias)
        (estado, _, _), segundos = cronometrado(lambda: nacional.pedir("PUT", f"/api/v1/instituciones/{AURORA}",
                                                                       {"nombre": nuevo_nombre}))
        comprobar("Institucional cambia el nombre sin esperar a Campañas", estado == 200, str(estado))
        comprobar(f"sin esperar ({segundos:.1f} s < {PLAZO} s)", segundos < PLAZO)
    finally:
        docker("start", campanias)
    comprobar("Campañas vuelve a estar sana", esperar(lambda: sano(campanias), 120))

    def nombre_en_campanias():
        _, lista, _ = anonimo.pedir("GET", "/api/v1/campanias")
        return any(c["institucion_id"] == AURORA and c.get("institucion_nombre") == nuevo_nombre for c in lista or [])
    comprobar("al volver, Campañas aplica el cambio (EV-07)", esperar(nombre_en_campanias, 60))
    estado, _, _ = nacional.pedir("PUT", f"/api/v1/instituciones/{AURORA}", {"nombre": nombre_original})
    comprobar("el nombre vuelve a ser el original", estado == 200, str(estado))

    correlacion = str(uuid.uuid4())
    marca = {"X-Correlacion-Id": correlacion}
    try:
        print("4. Institucional detenido")
        docker("stop", institucional)
        (estado, camp, _), segundos = cronometrado(lambda: admin.pedir("POST", "/api/v1/campanias", {
            "nombre": "Jornada sin Institucional", "sede": "Parque Berrío, Medellín",
            "inicia_en": time.strftime("%Y-%m-%dT13:00:00Z", time.gmtime(time.time() + 2 * 86400)),
            "termina_en": time.strftime("%Y-%m-%dT22:00:00Z", time.gmtime(time.time() + 2 * 86400))}, cabeceras=marca))
        comprobar("crear campaña -> 201 con la copia local de instituciones", estado == 201
                  and (camp or {}).get("territorio_ruta") == "/00/05/05001", str(estado))
        comprobar(f"Campañas no espera ({segundos:.1f} s < {PLAZO} s)", segundos < PLAZO)
        (estado, _, _), segundos = cronometrado(lambda: operador.pedir("GET", "/api/v1/unidades", cabeceras=marca))
        comprobar("la consulta del operador -> 200", estado == 200, str(estado))
        comprobar(f"sin esperar ({segundos:.1f} s < {PLAZO} s)", segundos < PLAZO)
        (estado, _, _), segundos = cronometrado(lambda: nacional.pedir("GET", "/api/v1/unidades", cabeceras=marca))
        comprobar("la consulta territorial -> 200 con la copia de Donación", estado == 200, str(estado))
        comprobar(f"Donación no espera ({segundos:.1f} s < {PLAZO} s)", segundos < PLAZO)
        (estado, _, _), segundos = cronometrado(lambda: operador.pedir("POST", "/api/v1/donaciones", {
            "grupo_sanguineo": "O+", "componentes": [{"componente": "plasma"}]}, idempotente=True, cabeceras=marca))
        comprobar("registrar una donación -> 201", estado == 201, str(estado))
        comprobar(f"sin esperar ({segundos:.1f} s < {PLAZO} s)", segundos < PLAZO)
        estado, _, _ = auditor.pedir("GET", "/api/v1/auditoria")
        comprobar("la bitácora por el borde -> 502/503 declarado", estado in (502, 503), str(estado))
        comprobar("Donación sigue sana", sano(donacion))
        comprobar("Campañas sigue sana", sano(campanias))
    finally:
        docker("start", institucional)
    comprobar("Institucional vuelve a estar sano", esperar(lambda: sano(institucional), 120))

    def recorrido():
        estado, bitacora, _ = auditor.pedir("GET", f"/api/v1/auditoria?correlacion_id={correlacion}&tamano=50")
        return estado == 200 and {r["contexto"] for r in bitacora["registros"]} >= {"campanias", "donacion"}
    comprobar("al volver, la bitácora recibe lo que pasó mientras tanto (EV-10)", esperar(recorrido, 90))
    estado, _, _ = admin.pedir("GET", "/api/v1/indicadores")
    comprobar("todo de vuelta: indicadores -> 200", estado == 200, str(estado))

    fallas = [n for n, ok in resultados if not ok]
    print(f"\n{len(resultados) - len(fallas)}/{len(resultados)} comprobaciones correctas")
    for n in fallas:
        print(f"  falla: {n}")
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())
