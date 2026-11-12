#!/usr/bin/env python3
# ejecutar_pruebas.py: corre las pruebas del Proyecto 4 en xv6 y guarda la
# evidencia en la carpeta pruebas/.
# Proyecto 4, Sistemas Operativos 2026-2, Universidad EAFIT.
#
# Uso, desde la carpeta raíz de xv6 (donde está el Makefile):
#   python3 ejecutar_pruebas.py              corre todas las suites
#   python3 ejecutar_pruebas.py cowtest      corre solo cowtest
#   python3 ejecutar_pruebas.py minidb       corre solo las pruebas de MiniDB
#   python3 ejecutar_pruebas.py pgtbltest    corre solo pgtbltest (Proyecto 3)
#   python3 ejecutar_pruebas.py usertests    corre solo usertests -q
#
# El script compila xv6 (make qemu CPUS=3), arranca QEMU, escribe los
# comandos en la consola de xv6, espera a que terminen y guarda toda la
# salida en pruebas/<suite>.txt, con un encabezado que indica la fecha, el
# commit y el resultado. Para minidb, además revisa los resultados de las
# consultas, del snapshot y del mapa de páginas.

import os
import re
import select
import signal
import subprocess
import sys
import time

CPUS = 3

PRUEBAS = {
    # nombre: (comandos en xv6, segundos máximos, texto que indica éxito)
    "cowtest": (["cowtest"], 900, "cowtest: TODAS LAS PRUEBAS PASARON"),
    "minidb": (["minidb consulta 1", "minidb consulta 2", "minidb consulta 3",
                "minidb consulta 8", "minidb snapshot 2000", "minidb snapshot 0",
                "minidb mapa"], 900, None),
    "pgtbltest": (["pgtbltest"], 600, "pgtbltest: TODAS LAS PRUEBAS PASARON"),
    "usertests": (["usertests -q"], 1800, "ALL TESTS PASSED"),
}

# Resultados esperados: todos los grupos usan el mismo generador (minidb.h).
SUCURSALES = [
    (1024, 25610224, 50055), (1024, 25750064, 50068), (1024, 25677072, 50050),
    (1024, 25624464, 50071), (1024, 25671856, 50084), (1024, 25685600, 50097),
    (1024, 25582992, 50021), (1024, 25680128, 50051),
]
FIRMA = 0x2DD2F610
CHECKSUM_INICIAL = 0x6915D295
CHECKSUM_2000 = 0x4AB3E209
APLICADAS_2000, RECHAZADAS_2000 = 1978, 22
NCUENTAS = 8192
PTE_W, PTE_COW = 1 << 2, 1 << 8


def git(*args):
    try:
        return subprocess.run(["git", *args], capture_output=True,
                              text=True).stdout.strip()
    except OSError:
        return ""


def bloque(salida, comando):
    """Devuelve la salida de un comando (desde su eco hasta el siguiente prompt)."""
    i = salida.find("$ " + comando + "\n")
    if i < 0:
        return None
    resto = salida[i + len(comando) + 3:]
    j = resto.find("\n$ ")
    return resto if j < 0 else resto[:j + 1]


def revisar_consulta(texto, k, errores, ticks):
    pref = "consulta %d: " % k
    if texto is None:
        errores.append(pref + "no se encontró la salida")
        return
    lineas = [l.rstrip("\r") for l in texto.splitlines()]
    if "consulta: OK" not in lineas:
        errores.append(pref + "el programa no imprimió 'consulta: OK'")
    trab = [re.match(r"^trabajador pid=(\d+) cuentas=\[(\d+),(\d+)\)$", l) for l in lineas]
    trab = [m for m in trab if m]
    if len(trab) != k:
        errores.append(pref + "se esperaban %d líneas 'trabajador', hay %d" % (k, len(trab)))
    else:
        pids = {m.group(1) for m in trab}
        if len(pids) != k:
            errores.append(pref + "los trabajadores deben ser procesos distintos (pids repetidos)")
        rangos = sorted((int(m.group(2)), int(m.group(3))) for m in trab)
        fin = 0
        for a, b in rangos:
            if a != fin or b <= a:
                errores.append(pref + "los rangos de cuentas no cubren la tabla sin huecos ni solapes")
                break
            fin = b
        if fin != NCUENTAS:
            errores.append(pref + "los rangos no cubren las %d cuentas" % NCUENTAS)
    for s, (n, total, mx) in enumerate(SUCURSALES):
        esperado = "sucursal %d: cuentas=%d total=%d max=%d" % (s, n, total, mx)
        if esperado not in lineas:
            errores.append(pref + "resultado incorrecto para la sucursal %d (se esperaba '%s')" % (s, esperado))
    if ("consulta: firma=0x%X" % FIRMA).lower() not in [l.lower() for l in lineas]:
        errores.append(pref + "la firma de auditoría no es 0x%X" % FIRMA)
    m = next((re.match(r"^consulta: ticks=(\d+)$", l) for l in lineas if l.startswith("consulta: ticks=")), None)
    if not m:
        errores.append(pref + "falta la línea 'consulta: ticks=N'")
    else:
        ticks[k] = int(m.group(1))


def campos(lineas, prefijo):
    """Lee los pares clave=valor de la línea que empieza con prefijo."""
    for l in lineas:
        if l.startswith(prefijo):
            return dict(re.findall(r"(\w+)=(\S+)", l[len(prefijo):]))
    return None


def revisar_snapshot(texto, m, errores):
    pref = "snapshot %d: " % m
    if texto is None:
        errores.append(pref + "no se encontró la salida")
        return
    lineas = [l.rstrip("\r") for l in texto.splitlines()]
    if "snapshot: OK" not in lineas:
        errores.append(pref + "el programa no imprimió 'snapshot: OK'")
    c0 = next((l.split("=")[1] for l in lineas if l.startswith("snapshot: checksum_inicial=")), "")
    if c0.lower() != "0x%x" % CHECKSUM_INICIAL:
        errores.append(pref + "checksum_inicial debe ser 0x%X" % CHECKSUM_INICIAL)
    tr = campos(lineas, "snapshot: transferencias=")
    cw = campos(lineas, "snapshot: paginas_tocadas=")
    ar = campos(lineas, "snapshot: archivo ")
    ac = campos(lineas, "snapshot: checksum_actual=")
    if None in (tr, cw, ar, ac):
        errores.append(pref + "faltan líneas obligatorias de la salida")
        return
    if ar.get("coincide") != "si" or ar.get("checksum", "").lower() != "0x%x" % CHECKSUM_INICIAL:
        errores.append(pref + "snap.db no contiene la tabla tal como estaba en el fork()")
    if ar.get("bytes") != str(16 + NCUENTAS * 32):
        errores.append(pref + "snap.db debe tener %d bytes" % (16 + NCUENTAS * 32))
    if ac.get("total_conservado") != "si":
        errores.append(pref + "las transferencias no conservaron el saldo total")
    actual = next((l.split("=")[1].split()[0] for l in lineas if l.startswith("snapshot: checksum_actual=")), "")
    tocadas = int(next((l.split("=")[1].split()[0] for l in lineas if l.startswith("snapshot: paginas_tocadas=")), "-1"))
    fallos, copias = int(cw.get("fallos_cow", -1)), int(cw.get("copias_cow", -1))
    if m == 2000:
        if tr.get("aplicadas") != str(APLICADAS_2000) or tr.get("rechazadas") != str(RECHAZADAS_2000):
            errores.append(pref + "se esperaban aplicadas=%d rechazadas=%d" % (APLICADAS_2000, RECHAZADAS_2000))
        if actual.lower() != "0x%x" % CHECKSUM_2000:
            errores.append(pref + "checksum_actual debe ser 0x%X" % CHECKSUM_2000)
        if not 8 <= tocadas < 64:
            errores.append(pref + "paginas_tocadas=%d fuera de lo esperado (entre 8 y 63)" % tocadas)
        if copias < 1 or copias > tocadas + 2 or fallos < copias:
            errores.append(pref + "copias_cow debe estar entre 1 y paginas_tocadas+2, y fallos_cow >= copias_cow")
    else:
        if actual.lower() != "0x%x" % CHECKSUM_INICIAL:
            errores.append(pref + "sin transferencias la tabla no debe cambiar")
        if copias > 2:
            errores.append(pref + "sin transferencias no debería copiarse casi ninguna página")


LINEA = re.compile(r"^ \.\. \.\. \.\.(0x[0-9a-f]{16}): pte (0x[0-9a-f]{16}) pa (0x[0-9a-f]{16})$")


def revisar_mapa(texto, errores):
    pref = "mapa: "
    if texto is None:
        errores.append(pref + "no se encontró la salida")
        return
    lineas = [l.rstrip("\r") for l in texto.splitlines()]
    m = next((re.match(r"^mapa: tabla en (0x[0-9a-f]+)", l) for l in lineas if l.startswith("mapa: tabla en")), None)
    try:
        i0 = next(i for i, l in enumerate(lineas) if l.startswith("mapa: hijo antes"))
        i1 = next(i for i, l in enumerate(lineas) if l.startswith("mapa: hijo después"))
    except StopIteration:
        m = None
    if not m:
        errores.append(pref + "faltan las líneas 'mapa: tabla en ...', 'mapa: hijo antes ...' o 'mapa: hijo después ...'")
        return
    va = int(m.group(1), 16)

    def hoja(desde, hasta):
        for l in lineas[desde:hasta]:
            x = LINEA.match(l)
            if x and int(x.group(1), 16) == va:
                return int(x.group(2), 16), int(x.group(3), 16)
        return None

    antes, despues = hoja(i0, i1), hoja(i1, len(lineas))
    if not antes or not despues:
        errores.append(pref + "vmprint() no muestra la página 0x%x antes y después de escribir" % va)
        return
    if antes[0] & PTE_W or not antes[0] & PTE_COW:
        errores.append(pref + "antes de escribir, la página debe estar sin PTE_W y con PTE_COW (bit 8)")
    if not despues[0] & PTE_W or despues[0] & PTE_COW:
        errores.append(pref + "después de escribir, la página debe tener PTE_W y no PTE_COW")
    if antes[1] == despues[1]:
        errores.append(pref + "después de escribir, la página del hijo debe estar en otra dirección física")


def revisar_minidb(salida):
    errores, avisos, ticks = [], [], {}
    for k in (1, 2, 3, 8):
        revisar_consulta(bloque(salida, "minidb consulta %d" % k), k, errores, ticks)
    for m in (2000, 0):
        revisar_snapshot(bloque(salida, "minidb snapshot %d" % m), m, errores)
    revisar_mapa(bloque(salida, "minidb mapa"), errores)
    if 1 in ticks and 3 in ticks and ticks[1] > 0:
        acel = ticks[1] / max(ticks[3], 1)
        avisos.append("aceleración con 3 trabajadores: %.2fx (%d ticks con 1, %d con 3)" % (acel, ticks[1], ticks[3]))
        if acel < 1.5:
            avisos.append("ADVERTENCIA: la consulta con 3 trabajadores no es más rápida; "
                          "revisen que los trabajadores corran en paralelo (o que la máquina tenga 3 núcleos libres)")
    return errores, avisos


def correr_en_xv6(comandos, limite):
    """Arranca xv6, ejecuta los comandos uno tras otro y devuelve (salida, motivo_fin)."""
    qemu = subprocess.Popen(["make", "qemu", "CPUS=%d" % CPUS], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True)
    salida = b""
    siguiente = 0
    fin_comando = 0
    inicio = time.time()
    motivo = "tiempo agotado"
    try:
        while time.time() - inicio < limite:
            listo, _, _ = select.select([qemu.stdout], [], [], 0.5)
            if listo:
                bloque_ = os.read(qemu.stdout.fileno(), 4096)
                if not bloque_:
                    motivo = "QEMU terminó inesperadamente"
                    break
                salida += bloque_
                sys.stdout.buffer.write(bloque_)
                sys.stdout.flush()
            if b"panic" in salida[fin_comando:]:
                time.sleep(1)
                motivo = "el kernel entró en pánico"
                break
            if salida.endswith(b"$ ") and (siguiente == 0 or len(salida) > fin_comando + 2):
                if siguiente == len(comandos):
                    motivo = "terminó"
                    break
                comando = comandos[siguiente]
                qemu.stdin.write((comando + "\n").encode())
                qemu.stdin.flush()
                siguiente += 1
                fin_comando = len(salida) + len(comando) + 1
    finally:
        try:
            os.killpg(qemu.pid, signal.SIGKILL)
        except OSError:
            pass
    return salida.decode("utf-8", errors="replace"), motivo


def main():
    if not os.path.exists("Makefile") or not os.path.isdir("kernel"):
        sys.exit("Ejecute este script desde la carpeta raíz de xv6.")
    nombres = sys.argv[1:] or list(PRUEBAS)
    for n in nombres:
        if n not in PRUEBAS:
            sys.exit("Prueba desconocida: %s (use %s)" % (n, ", ".join(PRUEBAS)))

    os.makedirs("pruebas", exist_ok=True)
    todo_ok = True
    for n in nombres:
        comandos, limite, exito = PRUEBAS[n]
        print("\n=== Ejecutando %s (puede tardar varios minutos) ===\n" % ", ".join(comandos))
        inicio = time.time()
        salida, motivo = correr_en_xv6(comandos, limite)
        duracion = time.time() - inicio
        paso = motivo == "terminó" and (exito is None or exito in salida)
        if re.search(r"^prueba \S+: FALLO", salida, re.M):
            paso = False
        extra = ""
        if n == "minidb":
            errores, avisos = revisar_minidb(salida)
            if errores:
                paso = False
                extra = "\nRevisión de los resultados de MiniDB: FALLÓ\n" + "".join("  - %s\n" % e for e in errores)
            else:
                extra = "\nRevisión de los resultados de MiniDB: OK\n"
            extra += "".join("  %s\n" % a for a in avisos)
            print(extra)
        todo_ok = todo_ok and paso

        encabezado = [
            "Proyecto 4 - Copy-on-Write y MiniDB - evidencia de pruebas",
            "Prueba:     %s" % ", ".join(comandos),
            "Fecha:      %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
            "Commit:     %s" % (git("rev-parse", "--short", "HEAD") or "(sin git)"),
            "Máquina:    %s (%d CPUs en QEMU)" % (os.uname().nodename, CPUS),
            "Duración:   %.0f s" % duracion,
            "Fin:        %s" % motivo,
            "Resultado:  %s" % ("PASÓ" if paso else "FALLÓ"),
            "-" * 60,
            "",
        ]
        archivo = os.path.join("pruebas", "%s.txt" % n)
        with open(archivo, "w", encoding="utf-8") as f:
            f.write("\n".join(encabezado) + salida + extra)
        print("\n=== %s: %s. Salida guardada en %s ===" %
              (n, "PASÓ" if paso else "FALLÓ", archivo))

    sys.exit(0 if todo_ok else 1)


if __name__ == "__main__":
    main()
