# Proyecto de Sistemas Operativos
## Proyecto 4: Copy-on-Write y MiniDB (xv6)

### Integrantes

- Tomás Ramírez
- Alejandro Restrepo

### Curso

Sistemas Operativos

### Semestre

2026-2

---

# Descripción

**Parte A, Copy-on-Write.** Después de `fork()`, padre e hijo comparten las mismas páginas físicas. Las páginas que eran escribibles quedan sin `PTE_W` y con el bit `PTE_COW` en los dos procesos. Cuando uno de los dos escribe, el page fault (o `copyout()`) llama a `cowfault()`, que copia la página si todavía la comparte otro proceso, o solo le devuelve `PTE_W` si ya es el único dueño. Un conteo de referencias por página física, protegido con su propio spinlock, decide cuándo una página vuelve a la lista libre.

**Parte B, `cowstat()`.** Una llamada al sistema que devuelve las páginas libres del sistema, las páginas físicas con más de una referencia y, para el proceso que llama, cuántos fallos CoW se atendieron y cuántos de ellos copiaron la página.

**Parte C, MiniDB.** Completamos `minidb snapshot M`. El padre calcula el checksum de la tabla y hace `fork()`. El hijo escribe `snap.db` directamente desde la tabla mientras el padre, sin esperarlo, aplica M transferencias. Como el hijo ve la memoria tal como estaba en el `fork()`, el archivo queda consistente, y solo se copian las páginas que el padre modifica. Al final el padre relee el archivo y verifica el encabezado, el checksum y que el saldo total se conservó.

---

# Archivos modificados

| Archivo | Qué se cambió y por qué |
|---|---|
| `kernel/riscv.h` | Define `PTE_COW` como `1L << 8`, uno de los bits RSW que RISC-V reserva para el software. Marca las páginas que no son escribibles "por ahora" y que se copian al escribir. |
| `kernel/kalloc.c` | Arreglo `refs.cnt` con un contador por página entre `KERNBASE` y `PHYSTOP`, protegido con `refs.lock`. `kalloc()` deja la página con 1 referencia, `krefinc()` suma una, `krefcnt()` la consulta y `kfree()` resta una y solo libera al llegar a 0. `freerange()` fija 1 antes de llamar a `kfree()`, porque esas páginas nunca pasaron por `kalloc()`. Para la Parte B, `kfreepages()` cuenta la lista libre y `ksharedpages()` cuenta las páginas con más de una referencia. |
| `kernel/vm.c` | `uvmcopy()` ya no copia: mapea la misma página física en el hijo, suma una referencia y, si era escribible, le quita `PTE_W` y le pone `PTE_COW` en padre e hijo (con `sfence_vma()` porque cambió la tabla del padre). Función nueva `cowfault()`: con más de una referencia copia la página; con una sola, solo devuelve `PTE_W`. También suma los contadores de la Parte B. `copyout()` llama a `cowfault()` antes de escribir en una página CoW. |
| `kernel/trap.c` | `usertrap()` atiende los fallos de escritura (`scause` = 15) con `cowfault()`. Si no es una página CoW, sigue el camino de antes (páginas perezosas con `vmfault()` o matar al proceso). |
| `kernel/defs.h` | Prototipos de `krefinc`, `krefcnt`, `kfreepages`, `ksharedpages` y `cowfault`. |
| `kernel/cowstat.h` | Nuevo. La `struct cowstat` del enunciado. |
| `kernel/proc.h` | Campos `cowfaults` y `cowcopies` en `struct proc`. |
| `kernel/proc.c` | `allocproc()` pone los dos contadores en 0, así todo proceso nuevo (también el hijo de un `fork()`) empieza desde cero. |
| `kernel/sysproc.c` | `sys_cowstat()`: llena la estructura y la copia al usuario con `copyout()`. Devuelve 0, o -1 si la copia falla. |
| `kernel/syscall.h`, `kernel/syscall.c` | Número `SYS_cowstat` (25) y su entrada en la tabla de llamadas. |
| `user/user.h`, `user/usys.pl` | Declaración `int cowstat(struct cowstat *);` (con `struct cowstat;` antes) y el stub de usuario. |
| `user/minidb.c` | `guardar_snapshot()` (lo que hace el hijo) y `snapshot()` (lo que hace el padre), más la función auxiliar `saldo_total()` y la constante `NPAGINAS`. |
| `Makefile` | `_pgtbltest`, `_cowtest` y `_minidb` en `UPROGS`. |
| `kernel/memlayout.h`, `user/ulib.c` | Sin cambios respecto a la base. Se incluyen porque `base_proyecto3.patch` ya los modificaba. |

---

# Integración con xv6

1. Clonar xv6-riscv, hacer `git switch -c proyecto4 06aad25` y aplicar `git apply base_proyecto3.patch`.
2. Copiar los archivos entregados en sus rutas.
3. Copiar los archivos del docente: `user/cowtest.c`, `user/minidb.h`, `user/pgtbltest.c` y `user/usertests.c`.
4. Compilar con `make qemu CPUS=3`.
5. Ejecutar `cowtest`, `minidb consulta 3`, `minidb snapshot 2000`, `pgtbltest` y `usertests -q`.

---

# Respuestas a las preguntas

## 1. PTE antes y después de una escritura CoW

Salida de `minidb mapa` (en `pruebas/minidb.txt`). La tabla quedó en `0x5000` y miramos la PTE de esa primera página en el hijo:

| | PTE | Bits bajos | Dirección física |
|---|---|---|---|
| Antes de escribir | `0x21fb61d3` | `0x1d3` | `0x87ed8000` |
| Después de escribir | `0x21fca0d7` | `0x0d7` | `0x87f28000` |

| Bit | V (0) | R (1) | W (2) | X (3) | U (4) | A (6) | D (7) | COW (8) |
|---|---|---|---|---|---|---|---|---|
| Antes | 1 | 1 | 0 | 0 | 1 | 1 | 1 | 1 |
| Después | 1 | 1 | 1 | 0 | 1 | 1 | 1 | 0 |

Antes de escribir, la página es válida, legible y de usuario, pero no tiene `W` y sí tiene `COW`: es la página del padre, compartida por el `fork()`. Los bits `A` y `D` vienen del padre, que escribió en la tabla antes del `fork()`, y `uvmcopy()` copia los flags tal cual.

Cuando el hijo escribe en `0x5000`, la CPU genera un page fault de escritura (`scause` = 15) porque falta `W`. `usertrap()` llama a `cowfault()`, que ve que la página tiene 2 referencias (padre e hijo). Entonces reserva una página nueva, copia el contenido, mapea la nueva en el hijo con `W` y sin `COW`, y le quita una referencia a la vieja. Por eso cambia la dirección física: la del hijo ahora es su propia copia, `0x87f28000`. La segunda página (`0x6000`), en la que el hijo no escribió, sigue igual: `0x1d3`, compartida.

En el padre la PTE no cambia en ese momento: sigue apuntando a `0x87ed8000`, sin `W` y con `COW`. Ahora esa página tiene una sola referencia, así que cuando el padre escriba también tendrá un fallo CoW, pero `cowfault()` no copia nada: solo le devuelve `W` y le quita `COW`.

## 2. Páginas sin PTE_W y USYSCALL en fork()

`uvmcopy()` solo marca como CoW las páginas que tenían `PTE_W`. Las demás se comparten con los mismos permisos. El código (en `mapa`, `0x0` y `0x1000` con `R`, `X` y `U`, sin `W`) se comparte como está y queda de solo lectura para siempre. Si se marcara como CoW, una escritura en el código se resolvería como un fallo CoW y el proceso podría modificar su propio código, en lugar de morir.

La página de guarda de la pila es un caso particular en esta versión de xv6: `exec()` la crea con `PTE_W` y `uvmclear()` solo le quita `PTE_U`. Por eso `uvmcopy()` sí le pone `PTE_COW` (en `mapa`, la página `0x3000` tiene flags `0x103`: `V`, `R` y `COW`, sin `U`). Sigue protegida porque `cowfault()` rechaza las páginas sin `PTE_U`. Además, un acceso del usuario a una página sin `U` falla en el hardware sin importar `W`, así que el fallo termina en el camino de "matar al proceso". `copyout()` tampoco puede escribir en ella, porque `walkaddr()` exige `PTE_U`.

`USYSCALL` y el trapframe no pasan por `uvmcopy()`, porque están en direcciones altas, fuera de `[0, sz)`. Los crea `allocproc()` para cada proceso, con su propia página física. `USYSCALL` guarda el pid del proceso. Si se compartiera con el padre, `ugetpid()` en el hijo leería la página del padre y devolvería el pid del padre.

## 3. Carrera en el conteo de referencias

Sin lock, un padre y su hijo que comparten una página (conteo 2) terminan al mismo tiempo en dos CPUs, y los dos hacen `kfree()` de esa página:

1. La CPU 0 lee 2 y la CPU 1 lee 2.
2. Las dos escriben 1.
3. El conteo queda en 1 aunque ya nadie usa la página. Nunca llega a 0, así que la página se pierde para siempre (fuga).

Con `krefinc()` y `kfree()` puede pasar algo peor. Un padre P y su hijo C comparten la página A, con conteo 2. En la CPU 0, P hace `fork()` y `uvmcopy()` llama a `krefinc(A)`. Al mismo tiempo, en la CPU 1, C escribe en A, y `cowfault()` la copia y llama a `kfree(A)`. Las dos CPUs leen 2. La CPU 0 escribe 3 y después la CPU 1 escribe 1. Ahora A la usan dos procesos (P y su nuevo hijo), pero el conteo dice 1. Cuando uno de los dos termine, `kfree()` lo baja a 0 y devuelve A a la lista libre aunque el otro la sigue usando. El siguiente `kalloc()` se la da a otro proceso y los dos escriben en la misma memoria. Si las escrituras ocurren en el orden contrario, el conteo queda en 3 y la página nunca se libera.

Esto solo puede pasar con procesos distintos, porque cada proceso de xv6 tiene un solo hilo y no puede estar en dos CPUs a la vez. Por eso todos los accesos al arreglo van bajo `refs.lock`.

## 4. Consistencia del snapshot

`minidb snapshot 2000` imprimió `checksum_inicial=0x6915D295`, `paginas_tocadas=30 fallos_cow=30 copias_cow=30` y `archivo ... checksum=0x6915D295 coincide=si`.

**Por qué `snap.db` tiene el checksum de antes del fork.** Después del `fork()`, la tabla del hijo apunta a las mismas páginas físicas que tenía el padre en ese instante, y todas quedaron sin `W`. Cada vez que el padre escribe en una página de la tabla, el fallo CoW le da al padre una copia nueva y lo que modifica es esa copia. La página vieja, la que ve el hijo, no cambia. El hijo solo lee, así que escribe en el archivo exactamente la tabla del momento del `fork()`, sin importar cuántas transferencias haga el padre mientras tanto.

**Por qué `copias_cow` es menor que 64.** Solo se copian las páginas que el padre escribe. Las transferencias no se reparten por toda la tabla: según `generar_transferencia()`, 255 de cada 256 ocurren entre las 1.024 cuentas más activas, que caben en las primeras 8 páginas (128 cuentas por página). Solo unas pocas transferencias tocan cuentas del resto de la tabla. Por eso el padre toca 30 páginas y las otras 34 se quedan compartidas. Además, cada página se copia una sola vez: después de la primera copia es propia del padre y las siguientes escrituras no generan fallos.

**Si el padre esperara al hijo antes de las transferencias.** Al terminar el hijo, `kfree()` le quita su referencia a cada página, y todas quedan con 1. Las PTE del padre siguen sin `W` y con `COW`, así que habría igual unos 30 fallos CoW, pero `cowfault()` vería una sola referencia y solo devolvería `W`: `copias_cow` sería 0. A cambio, el padre no podría aplicar transferencias mientras se guarda el snapshot, que es justo lo que se quiere evitar.

## 5. Aceleración y planificador

Ticks de `minidb consulta K` (mínimo de 13 corridas; 1 tick ≈ 0,1 s):

| K | CPUS=3 | CPUS=1 |
|---|---|---|
| 1 | 26 | 35 |
| 2 | 16 | 30 |
| 3 | 12 | 48 |
| 4 | 12 | 29 |
| 8 | 13 | 30 |

El trabajo de la consulta es casi todo CPU (la firma hace 10.000 rondas por cuenta) y se reparte en partes iguales entre los K trabajadores. Los trabajadores no se esperan entre sí: cada uno calcula, escribe su resultado en su pipe y termina.

**De 1 a 3 trabajadores con `CPUS=3`.** Cada CPU de xv6 corre su propio `scheduler()`, que recorre la tabla de procesos y ejecuta cualquier proceso `RUNNABLE`. Con 3 trabajadores, cada CPU toma uno y los tres calculan al mismo tiempo, cada uno con un tercio de las cuentas. El tiempo baja de 26 a 12 ticks. No llega a ser 3 veces más rápido porque hay costos fijos (los `fork()`, los pipes, el `wait()`) y porque QEMU emula las 3 CPUs con hilos del computador anfitrión, que también corría otros programas.

**De 3 a 8 no mejora.** Solo hay 3 CPUs. Con 8 trabajadores, Round Robin los turna: cada uno corre un quantum (un tick de reloj), la interrupción del timer llama a `yield()` y la CPU pasa al siguiente proceso `RUNNABLE`. El trabajo total es el mismo y se sigue haciendo de a 3 en paralelo, así que el tiempo queda igual (12-13 ticks). Más trabajadores solo agregan cambios de contexto y más procesos que crear.

**Con `CPUS=1` no mejora con ningún K.** Hay un solo procesador. Con varios trabajadores, Round Robin los alterna cada tick, pero en cada instante solo uno avanza, así que la suma del trabajo es la misma que con un trabajador. Por eso todos los K quedan alrededor de 30 ticks. El 48 de K = 3 es ruido de la medición: QEMU compartía el computador con otros programas y ninguna de las corridas de ese caso tuvo poca interferencia.

---

# Resultados de las pruebas

Todas las suites pasan con `make qemu CPUS=3`, sobre una copia limpia de la base con los archivos entregados y los del docente. La salida completa está en `pruebas/`.

| Suite | Resultado |
|---|---|
| `cowtest` | 11 de 11 |
| `minidb` (consulta 1, 2, 3 y 8, snapshot 2000 y 0, mapa) | Revisión de resultados OK |
| `pgtbltest` | 11 de 11 |
| `usertests -q` | ALL TESTS PASSED |

El kernel compila sin advertencias del compilador (el `Makefile` usa `-Werror`).

---

# Decisiones de diseño

- **Lock propio para el conteo de referencias**, distinto de `kmem.lock`. Nunca se toman al mismo tiempo (en `kfree()` se suelta `refs.lock` antes de tomar `kmem.lock`), así que no hay riesgo de deadlock.
- **Una sola función `cowfault()` en `vm.c`**, compartida por `usertrap()` y `copyout()`. Así la lógica de la copia, y los contadores de la Parte B, están en un solo lugar.
- **Con una sola referencia no se copia**: solo se devuelve `PTE_W` y se quita `PTE_COW`.
- **Conteo de 8 bits** (`uint8`): una página la referencia a lo sumo una vez cada proceso, y xv6 tiene `NPROC` = 64.
- **Contadores de `cowstat()` sin lock**: `cowfaults` y `cowcopies` solo los modifica el propio proceso dentro de `cowfault()` y solo los lee el propio proceso en `sys_cowstat()`. Se cuentan en el proceso actual (`myproc()`), porque `usertrap()` y `copyout()` siempre llegan con la tabla de páginas de ese proceso. Solo se cuentan los fallos que se resolvieron.
- **`sharedpages` y `freepages` se calculan al llamar a `cowstat()`** recorriendo el arreglo y la lista libre, en lugar de mantener contadores globales en cada `kalloc()` y `kfree()`. Es más lento, pero solo se usa para medir y no complica el camino rápido.
- **En el snapshot**, el hijo escribe directamente desde la tabla, sin copiarla, para no tocar sus páginas. El padre toma la primera medición de `cowstat()` después de imprimir el pid del hijo. Para entonces ya escribió en su pila y esa página ya se copió, así que su fallo CoW no se cuenta como parte de las transferencias (por eso `fallos_cow` coincide con `paginas_tocadas`). Para releer `snap.db` usamos un buffer reservado con `sbrk()`, porque la pila de xv6 es de una sola página y el archivo ocupa 262.160 bytes.

---

# Limitaciones conocidas

- Si dos procesos fallan a la vez sobre una página con 2 referencias, los dos pueden copiarla. Es correcto, porque la página vieja se libera cuando su conteo llega a 0, pero hace una copia de más.
- La página de guarda de la pila queda marcada con `PTE_COW` después de un `fork()` (ver la pregunta 2). No es un problema de seguridad, porque `cowfault()` exige `PTE_U`, pero suma una referencia y aparece en `sharedpages`.
- Si no hay memoria para copiar una página en un page fault, el proceso muere por la ruta normal y el kernel imprime el mensaje genérico `usertrap(): unexpected scause`. Si pasa dentro de `copyout()`, la llamada al sistema devuelve -1, igual que cualquier otro error de `copyout()`.
- Los tiempos de los experimentos varían mucho entre corridas, porque QEMU compartía el computador con otros programas. Por eso reportamos el mínimo de 13 corridas.

---

# Declaración de uso de IA

Usamos Claude Code como apoyo para implementar y probar las Partes A, B y C, correr las pruebas y los experimentos, y redactar este README. Revisamos el código y las respuestas, y los integrantes asumimos plena responsabilidad académica sobre el contenido entregado.
