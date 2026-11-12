---
title: "Proyecto 4: guía de pruebas"
subtitle: "Copy-on-Write y MiniDB · Sistemas Operativos 2026-2 · Universidad EAFIT"
lang: es-CO
---

Esta guía explica cómo instalar y ejecutar las cuatro suites de pruebas del Proyecto 4 y cómo generar la evidencia que se entrega en la carpeta `pruebas/`. Un proyecto aprobado debe pasar **todas** las pruebas de `cowtest`, la revisión de resultados de `minidb`, `pgtbltest` y `usertests -q`.

| Suite | Qué es | Duración aproximada |
|---|---|---|
| `cowtest` | 11 pruebas de Copy-on-Write y `cowstat()`, escritas para el curso | 10 a 60 segundos |
| `minidb` | Siete ejecuciones de su programa MiniDB, con revisión automática de resultados | 20 a 60 segundos |
| `pgtbltest` | Las pruebas del Proyecto 3 (regresión) | 20 segundos |
| `usertests -q` | Suite oficial de xv6 con los ajustes del Proyecto 3 (regresión) | 1 a 10 minutos |

# 1. Preparar la base

Todos los grupos parten de la misma base: xv6-riscv en el commit **06aad25** con la solución de referencia del Proyecto 3 (`base_proyecto3.patch`, incluida en este paquete). Trae `USYSCALL`, `ugetpid()`, `vmprint()` y `pgaccess()`.

```bash
git clone https://github.com/mit-pdos/xv6-riscv.git
cd xv6-riscv
git switch -c proyecto4 06aad25
git apply base_proyecto3.patch
git add -A && git commit -m "Base del Proyecto 4"
```

Antes de empezar, instalen los archivos de la sección 2 y verifiquen que `pgtbltest` y `usertests -q` pasan sobre la base sin cambios.

# 2. Instalar los archivos del docente

1. Copien en `user/` los archivos del paquete: `cowtest.c`, `minidb.c` (el esqueleto), `minidb.h`, `pgtbltest.c` y `usertests.c` (estos dos últimos son los mismos del Proyecto 3).
2. En el `Makefile`, agreguen los programas a la lista `UPROGS`:

```makefile
	$U/_sync\
	$U/_pgtbltest\
	$U/_cowtest\
	$U/_minidb\
```

3. `cowtest` incluye `kernel/cowstat.h` y usa `cowstat()`. **No compila hasta que creen `kernel/cowstat.h`** con la estructura del enunciado y declaren en `user/user.h`:

```c
struct cowstat;
int cowstat(struct cowstat *);
```

Mientras avanzan, pueden dejar `cowtest` o `minidb` fuera de `UPROGS`.

4. `minidb.c` es un esqueleto: `minidb consulta` y `minidb mapa` ya funcionan (cuando su kernel tenga `cowstat()`), y `minidb snapshot` imprime `snapshot: FALLO (sin implementar)` hasta que completen los `TODO`. `minidb.h` no se modifica.

Ojo: la pila de usuario de xv6 es de una sola página (4096 bytes). No declaren arreglos grandes como variables locales; usen variables globales o `sbrk()`.

# 3. Ejecutar las pruebas a mano

Dentro de xv6 (después de `make qemu`):

| Comando | Qué hace |
|---|---|
| `cowtest` | Ejecuta todas las pruebas de CoW |
| `cowtest -l` | Lista los nombres de las pruebas |
| `cowtest contadores` | Ejecuta solo una prueba (útil para depurar) |
| `minidb consulta 3` | Consulta con 3 trabajadores |
| `minidb snapshot 2000` | Snapshot mientras se aplican 2000 transferencias |
| `minidb mapa` | Tabla de páginas del hijo antes y después de escribir |
| `pgtbltest` | Pruebas del Proyecto 3 |
| `usertests -q` | Suite rápida de xv6 con verificación de fugas |

Para salir de QEMU: `Ctrl-a` y luego `x`. Para el experimento del reporte con una sola CPU: `make qemu CPUS=1`.

# 4. Generar la evidencia para la entrega

El script `ejecutar_pruebas.py` compila xv6, arranca QEMU con 3 CPUs, ejecuta las pruebas y guarda la salida completa con un encabezado (fecha, commit, duración y resultado). Para `minidb`, además compara los resultados con los valores esperados. Cópienlo en la carpeta raíz de xv6 y ejecuten:

```bash
python3 ejecutar_pruebas.py              # las cuatro suites
python3 ejecutar_pruebas.py cowtest      # solo cowtest
python3 ejecutar_pruebas.py minidb       # solo minidb
python3 ejecutar_pruebas.py pgtbltest    # solo pgtbltest
python3 ejecutar_pruebas.py usertests    # solo usertests
```

El script crea `pruebas/cowtest.txt`, `pruebas/minidb.txt`, `pruebas/pgtbltest.txt` y `pruebas/usertests.txt`. Esos cuatro archivos son los que se suben al repositorio del grupo. No los editen a mano: el docente vuelve a ejecutar las pruebas sobre el código entregado y compara los resultados.

Si no tienen Python 3, pueden guardar la salida con `make qemu CPUS=3 2>&1 | tee pruebas/cowtest.txt`, ejecutar los comandos dentro de xv6 y salir con `Ctrl-a x`. En ese caso deben revisar los resultados de MiniDB a mano con la sección 6.

# 5. Qué verifica cada prueba de cowtest

| Prueba | Qué verifica | Parte |
|---|---|---|
| `fork_grande` | Un proceso que usa dos tercios de la memoria libre puede hacer `fork()`. Sin CoW no hay memoria para copiarlo | A |
| `aislamiento` | Padre e hijo ven los mismos datos después del fork, y cada uno ve solo sus propias escrituras | A |
| `tres_niveles` | Abuelo, padre y nieto escriben a la vez en las mismas páginas sin verse entre sí; la memoria vuelve al valor inicial | A |
| `copyout_cow` | `read()` de un pipe escribe en páginas CoW del hijo (a través de `copyout()`) sin cambiar las del padre | A |
| `perezosa_cow` | CoW convive con `sbrklazy()`: las páginas no tocadas siguen siendo perezosas en ambos procesos | A |
| `ugetpid_hijo` | `ugetpid()` devuelve el pid correcto en hijos y nietos: `USYSCALL` no se comparte | A |
| `solo_lectura` | Escribir en el código o en `USYSCALL` después de `fork()` sigue matando al proceso | A |
| `contadores` | `cowstat()`: un hijo empieza en 0, cuenta un fallo y una copia por página escrita, y el padre no copia una página con una sola referencia | A y B |
| `sin_fugas` | 60 ciclos de `fork()`, escrituras y `exit()` no pierden páginas | A |
| `sin_memoria` | Si las copias agotan la memoria, el kernel mata al proceso sin pánico y la memoria se recupera | A |
| `concurrente` | Cuatro hijos escriben a la vez desde varias CPUs, 15 rondas: el conteo de referencias está bien protegido | A |

Algunas pruebas provocan escrituras inválidas o agotan la memoria a propósito, así que es normal ver mensajes como `usertrap(): unexpected scause 0xf ...` siempre que la prueba termine en OK.

# 6. Qué revisa el script en MiniDB

El script ejecuta, en este orden: `minidb consulta 1`, `minidb consulta 2`, `minidb consulta 3`, `minidb consulta 8`, `minidb snapshot 2000`, `minidb snapshot 0` y `minidb mapa`.

| Comando | Qué se revisa |
|---|---|
| `consulta K` | Termina con `consulta: OK`; hay K líneas `trabajador` con pids distintos y rangos que cubren las 8192 cuentas sin huecos; las ocho líneas `sucursal` y la firma `0x2DD2F610` coinciden con los valores esperados; existe la línea `consulta: ticks=N` |
| `snapshot 2000` (lo implementa el grupo) | Termina con `snapshot: OK`; `checksum_inicial=0x6915D295`; `aplicadas=1978 rechazadas=22`; `snap.db` tiene 262160 bytes y el checksum de antes del fork; `checksum_actual=0x4AB3E209`; el saldo total se conserva; `paginas_tocadas` está entre 8 y 63; `copias_cow` está entre 1 y `paginas_tocadas` + 2 |
| `snapshot 0` | La tabla no cambia y casi no se copian páginas |
| `mapa` | En la salida de `vmprint()`, la PTE de la primera página de la tabla tiene `PTE_COW` (bit 8) y no `PTE_W` antes de escribir, y `PTE_W` sin `PTE_COW` después, con otra dirección física |

Al final, el script informa la aceleración de la consulta con 3 trabajadores frente a 1. Con la solución de referencia es de 2,5 a 3 veces. Si es menor que 1,5 el script muestra una advertencia, pero no la cuenta como fallo, porque depende de la máquina: revisen que el padre cree todos los trabajadores antes de leer los resultados y que su computador tenga al menos 3 núcleos libres.

Los valores esperados son los mismos para todos los grupos porque los datos salen del generador de `minidb.h`. Si una línea `sucursal` no coincide, revisen que usan `generar_cuenta()` sin modificarla y que los totales se acumulan en `uint64`.

# 7. Cómo leer los resultados

- `prueba X: OK`: la prueba pasó.
- `prueba X: FALLO: <motivo>`: la prueba detectó un error. El motivo dice qué se esperaba.
- `prueba X: FALLO: el kernel mató al proceso de la prueba`: hubo un page fault no atendido u otra excepción. Miren el mensaje `usertrap()` justo antes: `scause 0xf` es un fallo de escritura, `stval` es la dirección.
- `prueba X: FALLO: wait() no pudo devolver el estado del hijo`: `copyout()` no está resolviendo las páginas CoW (la pila del padre es CoW después del fork).
- `panic: ...`: el kernel entró en pánico. El script se detiene y marca la suite como fallida.
- Si QEMU se queda quieto sin imprimir nada, suele ser un *deadlock*: por ejemplo, llamar a `kfree()` o `kalloc()` mientras se tiene tomado el lock del conteo de referencias.

# 8. Consejos de depuración

- Implementen primero el conteo de referencias sin cambiar `uvmcopy()`. Con eso, `usertests -q` debe seguir pasando; si no pasa, el error está en `kalloc.c`.
- Usen `vmprint()` dentro del hijo para ver si las PTEs quedaron sin `PTE_W` y con `PTE_COW`.
- `cowstat()` es su mejor herramienta: si `freepages` no vuelve al valor inicial después de que los hijos terminan, hay una fuga (o un decremento de más, que tarde o temprano causa un pánico).
- `make qemu-gdb` y `gdb-multiarch` permiten poner un breakpoint en `cowfault` o en `usertrap`.
