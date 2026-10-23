#!/usr/bin/env python3
"""RedVital — prueba de resiliencia de Kafka en un ambiente de QA.

Comprueba que ningún servicio espera al broker (ADR-018, Herramientas V4.0
entradas 78 y 85): con Kafka detenido, el sistema sigue atendiendo por el
borde sin demoras, los servicios siguen sanos, y al volver el broker los
eventos que esperaban en la bandeja de salida se entregan.

Se corre en la VM de servicios, porque detiene y arranca el contenedor de
Kafka con docker; las peticiones van por el borde, como las del navegador:

    python3 pruebas/resiliencia_kafka.py --base https://<ip-borde> \\
        --ca ~/raiz-caddy.crt --secretos secretos/qa

Requiere el perfil "semillas" y una campaña vigente del Banco Aurora. Deja
Kafka encendido al terminar, aunque algo falle.
"""
import argparse
import random
import ssl
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

from extremo_a_extremo import Cliente, comprobar, esperar, resultados

SERVICIOS = ("identity-service", "campaign-service", "donation-service", "notification-worker")
# Un servicio que esperara al broker tardaría al menos max.block.ms (5 s).
PLAZO_SIN_ESPERA = 3.0


def docker(*argumentos):
    return subprocess.run(["docker", *argumentos], capture_output=True, text=True, check=True).stdout.strip()


def estado_contenedor(nombre):
    salud, reinicios = docker("inspect", "-f", "{{.State.Health.Status}} {{.RestartCount}}", nombre).split()
    return salud, int(reinicios)


def cronometrado(funcion):
    inicio = time.monotonic()
    resultado = funcion()
    return resultado, time.monotonic() - inicio


def campania_vigente(admin):
    _, lista, _ = admin.pedir("GET", "/api/v1/campanias")
    ahora = datetime.now(timezone.utc)
    for c in lista or []:
        inicia = datetime.fromisoformat(c["inicia_en"].replace("Z", "+00:00"))
        termina = datetime.fromisoformat(c["termina_en"].replace("Z", "+00:00"))
        if c["estado"] == "publicada" and inicia <= ahora <= termina:
            return c
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="https://localhost")
    p.add_argument("--ca", required=True, help="raíz de la CA del borde (Caddy tls internal)")
    p.add_argument("--secretos", required=True)
    p.add_argument("--ambiente", default="qa")
    p.add_argument("--segundos-caido", type=int, default=60, help="cuánto tiempo queda Kafka detenido")
    a = p.parse_args()

    contexto = ssl.create_default_context(cafile=a.ca)
    clave_qa = open(f"{a.secretos}/clave_cuentas_qa").read().strip()
    proyecto = f"redvital-{a.ambiente}-servicios"
    kafka = f"{proyecto}-kafka-1"
    contenedores = {s: f"{proyecto}-{s}-1" for s in SERVICIOS}

    print("1. Antes de detener Kafka")
    operador, admin, anonimo = Cliente(a.base, contexto), Cliente(a.base, contexto), Cliente(a.base, contexto)
    comprobar("el operador inicia sesión", operador.entrar("operador@redvital.test", clave_qa) == 200)
    comprobar("el administrador de banco inicia sesión", admin.entrar("admin.banco@redvital.test", clave_qa) == 200)
    campania = campania_vigente(admin)
    if not campania:
        print("  No hay una campaña vigente del Banco Aurora: vuelve a sembrar QA (guía, sección 10).")
        return 2
    antes = campania.get("donaciones_registradas", 0)
    reinicios_antes = {s: estado_contenedor(c)[1] for s, c in contenedores.items()}
    print(f"  campaña {campania['nombre']}: {antes} donaciones registradas")

    try:
        print(f"2. Kafka detenido ({a.segundos_caido} s): el sistema sigue atendiendo sin esperar al broker")
        docker("stop", kafka)
        detenido_en = time.monotonic()

        (estado, intencion, _), duracion = cronometrado(lambda: anonimo.pedir(
            "POST", "/api/v1/intenciones", {"grupo_sanguineo": "A+", "municipio_ruta": None}, idempotente=True))
        comprobar(f"registro anónimo -> 201 en {duracion:.2f} s", estado == 201 and duracion < PLAZO_SIN_ESPERA,
                  f"{estado} {intencion}")

        documento = str(random.randint(10**9, 10**10 - 1))
        correo = f"resiliencia.{uuid.uuid4().hex[:8]}@redvital.test"
        (estado, _, _), duracion = cronometrado(lambda: anonimo.pedir("POST", "/api/v1/donantes", {
            "documento": documento, "nombre": "Prueba Resiliencia", "fecha_nacimiento": "1990-05-20",
            "correo_acceso": correo, "credencial": "Prueba" + uuid.uuid4().hex[:10] + "7", "correo": None,
            "telefono": "3001234567", "municipio_ruta": "/00/05/05001", "autoriza_tratamiento_datos": True,
            "autoriza_avisos_campanas": False, "version_aviso": "AV-2026.1"}, idempotente=True))
        comprobar(f"crear cuenta de donante -> 201 en {duracion:.2f} s", estado == 201 and duracion < PLAZO_SIN_ESPERA,
                  str(estado))

        _, presente, _ = operador.pedir("POST", "/api/v1/donantes/busqueda", {"documento": documento})
        (estado, donacion, _), duracion = cronometrado(lambda: operador.pedir("POST", "/api/v1/donaciones", {
            "donante_id": (presente or {}).get("id"), "grupo_sanguineo": "A+", "campania_id": campania["id"],
            "componentes": [{"componente": "plasma"}]}, idempotente=True))
        comprobar(f"registrar donación con campaña -> 201 en {duracion:.2f} s",
                  estado == 201 and duracion < PLAZO_SIN_ESPERA, f"{estado} {donacion}")
        comprobar("la campaña se confirma con la proyección local, sin preguntarle a Campañas",
                  donacion and donacion.get("campania_confirmada") is True)

        (estado, _, _), duracion = cronometrado(lambda: admin.pedir("GET", "/api/v1/campanias"))
        comprobar(f"Campañas responde -> 200 en {duracion:.2f} s", estado == 200 and duracion < PLAZO_SIN_ESPERA,
                  str(estado))

        time.sleep(max(0, a.segundos_caido - (time.monotonic() - detenido_en)))
        for servicio, contenedor in contenedores.items():
            salud, reinicios = estado_contenedor(contenedor)
            comprobar(f"{servicio} sigue sano y sin reinicios", salud == "healthy" and reinicios == reinicios_antes[servicio],
                      f"{salud}, {reinicios} reinicios")
        actual = (campania_vigente(admin) or {}).get("donaciones_registradas")
        comprobar("el evento espera en la bandeja: el conteo no ha cambiado", actual == antes, str(actual))
    finally:
        print("3. Kafka vuelve")
        docker("start", kafka)

    listo = esperar(lambda: estado_contenedor(kafka)[0] == "healthy", 90)
    comprobar("Kafka arranca sano", listo)

    def conteo():
        c = next((x for x in admin.pedir("GET", "/api/v1/campanias")[1] or [] if x["id"] == campania["id"]), {})
        return c.get("donaciones_registradas", 0) >= antes + 1
    comprobar("la donación pendiente llega a Campañas (EV-01) sin intervención", esperar(conteo, 120))
    for servicio, contenedor in contenedores.items():
        salud, reinicios = estado_contenedor(contenedor)
        comprobar(f"{servicio} sano al final", salud == "healthy" and reinicios == reinicios_antes[servicio],
                  f"{salud}, {reinicios} reinicios")

    fallas = [n for n, ok in resultados if not ok]
    print(f"\n{len(resultados) - len(fallas)}/{len(resultados)} comprobaciones correctas")
    for n in fallas:
        print(f"  falla: {n}")
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())
