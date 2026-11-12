---
title: "Proyecto 4 (Proyecto Final): Copy-on-Write y MiniDB"
subtitle: "Sistemas Operativos 2026-2 · Ingeniería de Sistemas · Universidad EAFIT"
lang: es-CO
---

| | |
|---|---|
| **Curso** | Sistemas Operativos 2026-2, Ingeniería de Sistemas, Universidad EAFIT |
| **Docente** | José Luis Montoya Pareja |
| **Modalidad** | Trabajo grupal, máximo tres (3) estudiantes por grupo |
| **Base** | xv6-riscv, commit 06aad25, con la solución de referencia del Proyecto 3 (`base_proyecto3.patch`, entregada por el docente) |
| **Publicación** | Viernes 6 de noviembre de 2026 |
| **Entrega** | Jueves 19 de noviembre de 2026, 23:59:59 |
| **Peso** | 25 % de la nota final del curso |

# Descripción general

En el xv6 original, `fork()` copia toda la memoria del padre en el hijo. Si el proceso usa 50 MB, el kernel copia 50 MB, aunque el hijo solo vaya a leer esos datos o vaya a hacer `exec()` enseguida. En este proyecto implementarán **Copy-on-Write (CoW)**: después de `fork()`, padre e hijo comparten las mismas páginas físicas marcadas como de solo lectura, y una página se copia únicamente cuando alguno de los dos intenta escribir en ella.

Después usarán ese mecanismo para resolver un problema real de gestión de datos con **multiprogramación**: varios procesos que trabajan al mismo tiempo sobre los mismos datos en memoria, cada uno en su propio espacio de direcciones.

El proyecto tiene tres partes que se entregan juntas:

1. **Copy-on-Write en el kernel.** `fork()` comparte las páginas, el manejador de page faults las copia al escribir y un conteo de referencias por página física decide cuándo liberarlas.
2. **Observabilidad.** La llamada al sistema `cowstat()` informa cuánta memoria libre hay, cuántas páginas están compartidas y cuántos fallos y copias CoW ha tenido el proceso.
3. **MiniDB.** Un programa de usuario que administra en memoria las cuentas de una cooperativa: responde consultas con varios procesos trabajadores en paralelo y guarda *snapshots* consistentes en disco sin detener las transferencias. El docente entrega un esqueleto con la consulta paralela ya hecha; el grupo implementa el snapshot.

El proyecto reúne lo visto en el semestre: procesos y pipes (Módulo 1), tablas de páginas Sv39 (Módulo 2), traps y page faults (Módulo 3), spinlocks y planificación Round Robin (Módulo 4). También reutiliza lo construido en el Proyecto 3: la asignación perezosa de `sbrklazy()`, la página `USYSCALL` con `ugetpid()`, `vmprint()` para ver la tabla de páginas y `pgaccess()` para saber qué páginas se usaron.

Antes de empezar, lean la sección siguiente, que explica cómo funciona Copy-on-Write y trae el material de estudio recomendado.

# ¿Qué es Copy-on-Write y cómo funciona?

## La idea

En OSTEP (cap. 5) se ve que `fork()` crea un proceso hijo que es una copia del padre: mismo código, mismos datos, misma pila. En los capítulos de paginación (cap. 18 a 20) se ve que cada proceso tiene **su propia tabla de páginas**, que traduce sus direcciones virtuales a páginas físicas, y que cada entrada (PTE) tiene bits de permisos que el hardware revisa en cada acceso.

Copy-on-Write junta las dos ideas. Para que el hijo "tenga una copia" de la memoria del padre no hace falta duplicar las páginas físicas: basta con que **las dos tablas de páginas apunten a las mismas páginas físicas**. Mientras los dos procesos solo lean, nadie nota la diferencia. El problema aparece cuando uno escribe, porque el otro no debe ver el cambio. Por eso el kernel quita el permiso de escritura en ambas tablas: la primera escritura provoca un **page fault**, y en ese momento, y solo para esa página, el kernel hace la copia.

Es el mismo principio de la asignación perezosa del Proyecto 3 y de la *demand zeroing* que OSTEP describe en el cap. 23: no hacer un trabajo costoso hasta que sea inevitable, y usar el page fault como aviso de que llegó el momento. OSTEP cuenta en ese capítulo que el sistema VAX/VMS ya usaba las dos técnicas; hoy Linux y los demás sistemas Unix usan copy-on-write en `fork()`.

## Paso a paso

Tomemos una página de datos de un proceso, escribible, en la dirección virtual `0x6000`, guardada en la página física P.

| Momento | PTE del padre | PTE del hijo | Refs. a P | Qué hace el kernel |
|-----------|----------|----------|-----|--------------------------------|
| Antes de `fork()` | P, R W U | (no existe) | 1 | Nada |
| Después de `fork()` | P, R U **COW** | P, R U **COW** | 2 | `uvmcopy()` no copia: mapea P en el hijo, quita `PTE_W` y pone `PTE_COW` en los dos, y sube el conteo de P |
| El hijo lee `0x6000` | igual | igual | 2 | Nada: la lectura está permitida y no hay fallo |
| El hijo escribe en `0x6000` | P, R U COW | **Q**, R **W** U | 1 | Page fault de escritura (`scause` = 15). Como P tiene 2 referencias, el kernel reserva Q, copia P en Q, mapea Q en el hijo con `PTE_W` y baja el conteo de P |
| El padre escribe en `0x6000` | P, R **W** U | Q, R W U | 1 | Page fault de escritura. P ya tiene **una** sola referencia: no hace falta copiar, el kernel le devuelve `PTE_W` y le quita `PTE_COW` |
| Los dos terminan | — | — | 0 | `kfree()` baja el conteo de P y de Q; cada página vuelve a la lista libre cuando su conteo llega a 0 |

Tres consecuencias que deben tener presentes al implementar:

- **El conteo de referencias es imprescindible.** Sin él, el primer proceso que termina libera una página que el otro todavía usa. Como `fork()`, `exit()` y los page faults pueden ocurrir a la vez en varias CPUs, el conteo es un dato compartido y se protege con un spinlock (OSTEP, cap. 28).
- **El kernel también escribe en memoria de usuario.** `read()`, `wait()` y otras llamadas escriben en buffers del proceso con `copyout()`, sin pasar por el hardware de traducción del usuario y por lo tanto sin page fault. `copyout()` tiene que detectar las páginas CoW y copiarlas antes de escribir.
- **No todo lo que no tiene `PTE_W` es CoW.** El código del programa, la página de guarda de la pila y `USYSCALL` nunca fueron escribibles. Si se marcaran como CoW, un proceso podría escribir en su propio código después de un `fork()`. El bit `PTE_COW` es justamente lo que distingue "no escribible por ahora" de "no escribible nunca".

## Por qué vale la pena

Muchos programas hacen `fork()` y enseguida `exec()`, que descarta toda la memoria copiada. Otros, como los servidores, crean hijos que casi no modifican los datos del padre. Con CoW, `fork()` cuesta lo mismo para un proceso de 10 KB que para uno de 100 MB: solo se crea la tabla de páginas. Bases de datos como Redis usan este mecanismo para guardar copias consistentes en disco sin detener la operación, que es exactamente lo que hará MiniDB.

## Material de estudio

| Recurso | Qué leer o ver |
|---|---|
| OSTEP, *Operating Systems: Three Easy Pieces*, Arpaci-Dusseau ([pages.cs.wisc.edu/~remzi/OSTEP](https://pages.cs.wisc.edu/~remzi/OSTEP/)) | Cap. 5, *Interlude: Process API* (`fork()`, `wait()`, `exec()`); cap. 18, *Introduction to Paging* (tablas de páginas y PTEs); cap. 23, *Complete Virtual Memory Systems* (sección 23.1, "Other Neat Tricks": *demand zeroing* y copy-on-write en VAX/VMS); cap. 28, *Locks* |
| Libro de xv6, Cox, Kaashoek y Morris, revisión de septiembre de 2025 ([book-riscv-rev5.pdf](https://pdos.csail.mit.edu/6.1810/2025/xv6/book-riscv-rev5.pdf)) | Capítulo de tablas de páginas; capítulo 5, *Page faults*, en especial la sección 5.3 *Real world: Copy-On-Write (COW) fork* |
| Notas de clase MIT 6.1810, *Lecture 8: Page faults* ([l-pgfaults.txt](https://pdos.csail.mit.edu/6.1810/2025/lec/l-pgfaults.txt)) | Sección "copy-on-write fork": compartir páginas, conteo de referencias, asignación perezosa y *zero-fill on demand* |
| Video: *6.S081 Fall 2020 Lecture 8: Page Faults*, Frans Kaashoek, MIT ([youtu.be/KSYO-gTZo0A](https://youtu.be/KSYO-gTZo0A)) | La clase del MIT sobre page faults en xv6, con asignación perezosa y copy-on-write fork. En inglés; YouTube permite activar subtítulos traducidos al español |
| Laboratorio del MIT, *Copy-on-Write Fork for xv6* ([cow.html](https://pdos.csail.mit.edu/6.828/2026/labs/cow.html)) | Pistas para la Parte A. Nuestro proyecto pide además `cowstat()`, la integración con el Proyecto 3 y MiniDB |
| Documentación de Redis, *Redis persistence* ([redis.io](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)) | Sección "Snapshotting / How it works": cómo Redis hace `fork()` y aprovecha copy-on-write para guardar snapshots (el mismo diseño de MiniDB) |
| Arpit Bhayani, *What is copy-on-write and why it matters?* ([artículo](https://edge.arpitbhayani.me/blogs/copy-on-write)) | Explicación corta del concepto y de sus usos fuera de `fork()` |
| Vlastimil Babka, *Patching until the COWs come home (part 1)*, LWN, 22 de marzo de 2021 ([lwn.net/Articles/849638](https://lwn.net/Articles/849638/)) | Lectura opcional y avanzada: un error de seguridad real en el copy-on-write de Linux y cómo se corrigió |


# El problema: la cooperativa y su base de datos en memoria

Una cooperativa de ahorro mantiene en memoria la tabla de sus 8.192 cuentas, para responder rápido. Tiene dos necesidades que chocan entre sí:

- **Reportes de auditoría por sucursal.** Cada noche se calcula, por sucursal, el número de cuentas, el saldo total, el saldo máximo y una firma de auditoría por cuenta. La firma es costosa en CPU, así que el reporte debe repartirse entre varios procesos que corran en paralelo en las CPUs disponibles.
- **Copias de seguridad sin detener la operación.** Cada cierto tiempo se debe guardar en disco una copia (*snapshot*) de la tabla tal como estaba en un instante. Mientras se escribe la copia, la cooperativa debe seguir aplicando transferencias. La copia no puede quedar a medias: no puede tener unas cuentas antes de una transferencia y otras después.

Con `fork()` y Copy-on-Write, las dos necesidades se resuelven con la misma idea. Un `fork()` le entrega al hijo una "foto" de la memoria del padre, sin copiarla. Los trabajadores de la consulta solo leen la tabla, así que nunca se copia ninguna de sus páginas. El hijo que escribe el snapshot ve la tabla congelada en el momento del `fork()`, mientras el padre sigue modificándola; solo se copian las páginas que el padre toca. Así funcionan sistemas reales como Redis (`BGSAVE`) y PostgreSQL al crear procesos.

# Objetivos de aprendizaje

Al terminar el proyecto, cada estudiante será capaz de:

1. Explicar cómo Copy-on-Write retrasa la copia de memoria en `fork()` y por qué eso reduce el tiempo y la memoria que usa un proceso nuevo.
2. Manipular los permisos de las PTEs (`PTE_W` y un bit reservado para el software) para provocar y atender page faults de escritura.
3. Mantener un conteo de referencias por página física protegido con un spinlock, y argumentar por qué sin el lock aparecen condiciones de carrera entre CPUs.
4. Integrar CoW con los mecanismos existentes de xv6: asignación perezosa, `copyout()`, la página `USYSCALL` y la falta de memoria.
5. Analizar y extender un programa multiproceso que reparte trabajo entre procesos, los comunica con pipes y verifica sus resultados.
6. Medir el efecto del paralelismo y de CoW (tiempo en ticks, páginas libres, páginas copiadas) e interpretar los resultados a la luz del planificador Round Robin.

# Competencias

Al finalizar el proyecto, los estudiantes deberán demostrar capacidad para:

- Comprender cómo el sistema operativo virtualiza la memoria y la comparte de forma segura entre procesos.
- Modificar código de kernel respetando sus invariantes, sin romper funcionalidades existentes.
- Aplicar mecanismos de sincronización del kernel para proteger datos compartidos entre CPUs.
- Resolver un problema de gestión de datos con procesos concurrentes y comunicación entre procesos.
- Validar una implementación de bajo nivel con pruebas y mediciones, y diagnosticar sus fallos.
- Documentar y justificar decisiones de diseño de software de bajo nivel en un reporte técnico.

# Requerimientos

Todos los grupos parten de la misma base: xv6-riscv en el commit 06aad25 con la solución de referencia del Proyecto 3, que entrega el docente como `base_proyecto3.patch` (incluye `USYSCALL`, `ugetpid()`, `vmprint()` y `pgaccess()`). Prepárenla así:

```bash
git clone https://github.com/mit-pdos/xv6-riscv.git && cd xv6-riscv
git switch -c proyecto4 06aad25
git apply base_proyecto3.patch
git add -A && git commit -m "Base del Proyecto 4"
```

El commit de base permite ver con `git diff` exactamente lo que el grupo cambió. `cowtest` y `minidb` usan `ugetpid()`, `vmprint()` y `pgaccess()` de esa base.

Para la nueva llamada al sistema, regístrenla en `kernel/syscall.h`, `kernel/syscall.c`, `user/usys.pl` y `user/user.h`, como en los proyectos anteriores. Agreguen `$U/_cowtest\` y `$U/_minidb\` a `UPROGS` en el `Makefile`.

## Parte A. Copy-on-Write en fork() (kernel/kalloc.c, kernel/vm.c, kernel/trap.c, kernel/riscv.h)

1. **Bit CoW.** En `riscv.h` definan `PTE_COW` como el bit 8 de la PTE (`1L << 8`), uno de los dos bits que RISC-V reserva para el software (RSW). El script de pruebas revisa ese bit en la salida de `vmprint()`.
2. **Conteo de referencias.** En `kalloc.c` lleven, para cada página física entre `KERNBASE` y `PHYSTOP`, el número de tablas de páginas (o usos del kernel) que la referencian:
    - `kalloc()` deja la página con 1 referencia.
    - Una función nueva (por ejemplo `krefinc(pa)`) agrega una referencia.
    - `kfree()` quita una referencia y solo devuelve la página a la lista libre cuando llega a 0.
    - El arreglo de conteos se protege con un spinlock propio. Ojo con la inicialización: `freerange()` llama a `kfree()` sobre páginas que nunca pasaron por `kalloc()`.
3. **fork().** `uvmcopy()` ya no copia páginas. Para cada página presente del padre, mapea **la misma página física** en el hijo y le agrega una referencia. Si la página era escribible, le quita `PTE_W` y le pone `PTE_COW`, **tanto en el padre como en el hijo**. Las páginas que ya eran de solo lectura (el código, por ejemplo) se comparten sin `PTE_COW`.
4. **Page fault de escritura.** En `usertrap()`, un fallo de escritura (`scause` = 15) sobre una página con `PTE_COW` se atiende así:
    - Si la página tiene **más de una referencia**, se reserva una página nueva, se copia el contenido, se mapea con `PTE_W` y sin `PTE_COW`, y se quita una referencia a la página vieja.
    - Si la página tiene **una sola referencia** (el otro proceso ya la copió o ya terminó), no se copia: basta con devolverle `PTE_W` y quitarle `PTE_COW`.
    - Factoricen esta lógica en una función de `vm.c` (por ejemplo `cowfault(pagetable, va)`), porque `copyout()` también la necesita.
5. **copyout().** Cuando el kernel escribe en memoria de usuario (por ejemplo `read()` sobre un buffer, o `wait()` al devolver el estado), la página destino puede ser CoW. `copyout()` debe resolver la copia antes de escribir, usando la misma función del punto anterior.
6. **Convivencia con lo que ya existe.**
    - Las páginas perezosas de `sbrklazy()` que aún no tienen memoria física se siguen asignando con `vmfault()`, en el padre y en el hijo. No se comparten porque no existen.
    - La página `USYSCALL` y el trapframe son propios de cada proceso y no se comparten: `ugetpid()` del hijo debe devolver el pid del hijo.
    - Una escritura en una página que no es CoW y no tiene `PTE_W` (el código, la página de guarda de la pila, `USYSCALL`) sigue matando al proceso.
7. **Falta de memoria.** Si no hay memoria para copiar una página, el kernel mata al proceso (`setkilled()`) sin entrar en pánico, y todas sus páginas se recuperan.

## Parte B. cowstat() (kernel/cowstat.h, kernel/sysproc.c, kernel/proc.h)

1. Creen `kernel/cowstat.h` con esta estructura, que `cowtest` y `minidb` usan tal cual:

```c
struct cowstat {
  uint64 freepages;   // páginas físicas libres en todo el sistema
  uint64 sharedpages; // páginas físicas con más de una referencia
  uint64 cowfaults;   // fallos CoW atendidos para el proceso que llama
  uint64 cowcopies;   // de esos fallos, cuántos copiaron la página
};
```

2. Agreguen la llamada al sistema `int cowstat(struct cowstat *st)`, que llena la estructura y la copia al usuario con `copyout()`. Devuelve 0, o -1 si la copia falla. En `user/user.h` declárenla como `int cowstat(struct cowstat *);` (con `struct cowstat;` antes).
3. `cowfaults` y `cowcopies` son contadores de cada proceso (campos nuevos en `struct proc`). Un proceso nuevo empieza con ambos en 0. Cuentan tanto los fallos que llegan por `usertrap()` como los que resuelve `copyout()`.

## Parte C. MiniDB (user/minidb.c)

MiniDB es un programa de usuario que se ejecuta con uno de tres comandos. El docente entrega dos archivos:

- `user/minidb.c`: un **esqueleto** con los comandos `consulta` y `mapa` ya implementados y el comando `snapshot` por hacer (busquen `TODO`). Este es el archivo que el grupo completa y entrega.
- `user/minidb.h`, que **no se modifica**: define `struct cuenta` (32 bytes, 128 cuentas por página), el generador determinista de cuentas y de transferencias, la firma de auditoría, el checksum y la función `reservar_tabla()`, que reserva la tabla de 64 páginas con `sbrklazy()` alineada a página. Como todos los grupos usan el mismo generador, los resultados son los mismos en todas las entregas, y el script los verifica.

En los tres comandos, MiniDB primero reserva la tabla con `reservar_tabla()` y llena las 8.192 cuentas con `generar_cuenta()`. El formato exacto de la salida está en el Anexo B.

### C.1 Consulta paralela: `minidb consulta K` (ya implementada en el esqueleto)

Este comando viene hecho y es el modelo de multiprogramación del proyecto. No tienen que programarlo, pero sí entenderlo línea por línea, usarlo en los experimentos del reporte y explicarlo en la sustentación. Funciona así:

1. Crea `K` procesos trabajadores con `fork()` (1 ≤ K ≤ 8). El trabajador `w` procesa las cuentas `[w·8192/K, (w+1)·8192/K)` y calcula, por sucursal, el número de cuentas, el saldo total, el saldo máximo y la suma de `firma()` de sus cuentas.
2. Cada trabajador envía su `struct resultado` (definida en `minidb.h`) al padre por **su propio pipe**, con su pid obtenido con `ugetpid()`, y termina.
3. El padre crea todos los trabajadores **antes** de leer cualquier resultado, para que trabajen al mismo tiempo. Luego lee los resultados, los combina, espera a los hijos con `wait()` e imprime el reporte.
4. El padre mide la duración con `uptime()` (desde antes del primer `fork()` hasta después del último `wait()`) y la memoria libre con `cowstat()` antes del primer `fork()` y después del último.
5. El padre verifica el reporte recalculando de forma secuencial el número de cuentas, el total y el máximo (sin la firma). Imprime `consulta: OK` solo si todo coincide, todos los trabajadores terminaron con estado 0 y los rangos cubren la tabla completa.

### C.2 Snapshot consistente: `minidb snapshot M` (lo implementa el grupo)

Completen `snapshot()` y `guardar_snapshot()` en el esqueleto. Pueden reutilizar las funciones auxiliares que ya trae (`cargar_tabla()`, `leer_todo()`, `escribir_todo()`, `morir()`).

1. El padre calcula el checksum de la tabla (`checksum_tabla()`) y el saldo total, y limpia los bits de acceso de la tabla con `pgaccess()`.
2. Hace `fork()`. **El hijo** escribe el snapshot en `snap.db`: un `struct snap_encabezado` (con el checksum calculado antes del fork) seguido de las 8.192 cuentas, escritas directamente desde la tabla (sin copiarla a otro buffer). Luego termina.
3. **El padre**, sin esperar al hijo, aplica `M` transferencias generadas con `generar_transferencia()` (semilla inicial 4). Una transferencia se rechaza si el origen y el destino son la misma cuenta o si el origen no tiene saldo suficiente. Si se aplica, resta el monto del origen, lo suma al destino y aumenta `nmov` en ambas cuentas.
4. Al terminar las transferencias, el padre reporta cuántas páginas de la tabla tocó (con `pgaccess()`) y cuántos fallos y copias CoW tuvo durante las transferencias (diferencia de `cowstat()` antes y después).
5. El padre espera al hijo, vuelve a leer `snap.db` y verifica que el encabezado es correcto y que el checksum de las cuentas guardadas es el de **antes** del fork. También verifica que el saldo total de la tabla no cambió con las transferencias. Imprime `snapshot: OK` si todo se cumple.

### C.3 Mapa de páginas: `minidb mapa` (ya implementado en el esqueleto)

Reserva dos páginas con `sbrk()` y escribe en ellas. Hace `fork()`; el hijo imprime su tabla de páginas con `vmprint()`, escribe en la primera de las dos páginas y vuelve a imprimirla. El padre espera al hijo. Esta salida se usa para responder la pregunta 1 del README y en la sustentación.

# Restricciones

- `usertests -q` (versión del curso, la misma del Proyecto 3) y `pgtbltest` deben seguir pasando completos.
- No se pueden modificar las pruebas ni `minidb.h`, ni desactivar ninguna prueba de `usertests`.
- El kernel debe compilar sin advertencias con `make qemu` y no debe tener fugas de memoria: después de que todos los hijos terminan, las páginas libres deben volver al valor inicial.
- MiniDB no puede usar memoria compartida ni llamadas al sistema nuevas: los procesos se comunican solo con pipes, archivos y `wait()`.
- Las pruebas se ejecutan con 3 CPUs (`make qemu CPUS=3`).

# Fuera del alcance

No se requiere implementar:

- Hilos (*threads*) ni memoria compartida entre procesos.
- Archivos mapeados en memoria (`mmap`).
- CoW para las páginas de las tablas de páginas (solo para las páginas de datos del proceso).
- Paginación a disco (*swap*) ni reemplazo de páginas.
- Recuperar la tabla de MiniDB desde `snap.db` al iniciar.

Cualquier funcionalidad adicional será considerada opcional y no reemplaza los requisitos obligatorios.

# Entrega

La entrega se hace en el repositorio del grupo, con el mismo procedimiento de los proyectos anteriores. Se evalúa el último commit realizado antes del jueves 19 de noviembre de 2026 a las 23:59:59.

## Estructura obligatoria del repositorio

No suban el repositorio completo de xv6. Entreguen únicamente los archivos que el grupo modificó o creó sobre la base (06aad25 + `base_proyecto3.patch`), conservando su ruta dentro de xv6. Si un archivo ya venía modificado en la base, entreguen su versión completa:

```
repositorio/
├── README.md
├── REPORTE.pdf
├── Makefile
├── kernel/
│   ├── cowstat.h
│   ├── kalloc.c
│   ├── vm.c
│   ├── trap.c
│   ├── riscv.h
│   ├── memlayout.h
│   ├── proc.h
│   ├── proc.c
│   ├── sysproc.c
│   ├── syscall.c
│   ├── syscall.h
│   └── defs.h
├── user/
│   ├── minidb.c          (el esqueleto, completado)
│   ├── ulib.c
│   ├── user.h
│   └── usys.pl
└── pruebas/
    ├── cowtest.txt
    ├── minidb.txt
    ├── pgtbltest.txt
    └── usertests.txt
```

Incluyan solo los archivos que modificaron. No incluyan los archivos que entrega el docente (`cowtest.c`, `minidb.h`, `pgtbltest.c`, `usertests.c`): al calificar se usan las versiones originales. La carpeta `pruebas/` contiene la salida de las cuatro suites, generada con `ejecutar_pruebas.py` sobre la versión entregada. El `README.md` sigue la estructura del Anexo A.

Antes de la fecha límite, verifiquen que al copiar sus archivos y los del docente sobre una copia limpia de la base (06aad25 + `base_proyecto3.patch`) el kernel compila con `make qemu`.

## Pruebas que debe pasar

- **`cowtest`** (programa del docente, `user/cowtest.c`): 11 pruebas sobre CoW. Verifican que un proceso que usa dos tercios de la memoria puede hacer `fork()`, que padre e hijo no ven las escrituras del otro, que funciona con tres generaciones de procesos, con `copyout()`, con páginas perezosas y con `USYSCALL`, que las páginas de solo lectura siguen protegidas, que `cowstat()` cuenta bien, que no hay fugas, que la falta de memoria no causa pánico y que el conteo de referencias resiste escrituras simultáneas desde varias CPUs.
- **`minidb`**: el script ejecuta `minidb consulta` con 1, 2, 3 y 8 trabajadores, `minidb snapshot 2000`, `minidb snapshot 0` y `minidb mapa`, y compara los resultados con los valores esperados (reporte por sucursal, firma, checksums, transferencias aplicadas y bits de la PTE antes y después de escribir).
- **`pgtbltest`** y **`usertests -q`**, como en el Proyecto 3, para verificar que no se dañó nada.

La guía de pruebas explica cómo instalar los programas, qué verifica cada prueba y cómo leer los resultados. El script `ejecutar_pruebas.py` ejecuta las cuatro suites y genera los archivos de `pruebas/` con fecha, commit y resultado; esos archivos son la evidencia que se entrega. Un pánico del kernel durante cualquiera de las pruebas cuenta como fallo.

# Reporte técnico

Además del README, entreguen `REPORTE.pdf`, de 2 a 3 páginas, con:

1. **Arquitectura de la solución.** Un diagrama del ciclo de vida de una página compartida (`fork()`, escritura del hijo, escritura del padre, `exit()`), con los bits de la PTE y el conteo de referencias en cada paso, y una explicación de qué lock protege el conteo y por qué basta.
2. **Experimentos.** Una tabla con los ticks de `minidb consulta K` para K = 1, 2, 3, 4 y 8, con `CPUS=1` y con `CPUS=3`. Otra tabla con las páginas libres antes y después de los `fork()` de la consulta y con las copias CoW del snapshot, comparadas con lo que costaría lo mismo sin CoW.
3. **Decisiones de diseño, limitaciones y conclusiones.**

# Preguntas para el README

El README debe responder, con sus propias palabras:

1. Con la salida de `minidb mapa`, tomen la PTE de la primera página de la tabla antes y después de que el hijo escriba. Decodifiquen sus bits (V, R, W, X, U, A, D, COW) y expliquen por qué cambió la dirección física en el hijo. ¿Qué pasa con la PTE de esa página en el padre?
2. ¿Qué hace su implementación al hacer `fork()` con las páginas que no tienen `PTE_W` (código, página de guarda) y con `USYSCALL`? ¿Por qué no se marcan como CoW, y qué fallaría en `ugetpid()` si `USYSCALL` se compartiera?
3. Describan una secuencia concreta de eventos en dos CPUs en la que un conteo de referencias sin lock libera una página que otro proceso todavía usa, o pierde una página para siempre.
4. En `minidb snapshot 2000`, ¿por qué `snap.db` tiene el checksum de antes del fork aunque el padre modificó la tabla mientras el hijo escribía? ¿Por qué `copias_cow` es menor que 64? ¿Qué pasaría con `copias_cow` si el padre esperara al hijo antes de aplicar las transferencias?
5. Con la tabla de ticks del reporte, expliquen por qué la consulta se acelera de 1 a 3 trabajadores con `CPUS=3`, por qué no mejora de 3 a 8 y por qué no mejora con `CPUS=1`. Relaciónenlo con el planificador Round Robin de xv6.

# Sustentación y seguimiento

Durante las clases se harán sesiones de seguimiento en las que cada grupo presentará sus avances.

En la sustentación:

- Cada grupo repetirá en vivo, sobre su entrega, la ejecución de `cowtest`, de `minidb` y de `usertests -q`.
- Se pedirá interpretar en vivo la salida de `vmprint()` antes y después de una escritura CoW, y la salida de `cowstat()` en un experimento propuesto por el docente.
- Se harán preguntas sobre el diseño, el reporte técnico y las respuestas del README.
- La sustentación hace parte de la evaluación.

Todos los integrantes deben estar en capacidad de explicar el funcionamiento de la solución entregada, incluidas las partes que no programaron y la consulta paralela que viene en el esqueleto.

## Cómo se califica la sustentación

La sustentación vale 10 puntos, repartidos en dos componentes, y además incluye una revisión de similitud que es condición de validez de la entrega.

**1. Preguntas individuales (7 puntos, nota individual).** Cada integrante responde por separado tres preguntas escogidas al azar por el docente, sobre cualquier parte del proyecto, no solo sobre la que presentó. Cada pregunta se califica como completa (2,33 puntos), parcial (1,17) o sin respuesta (0). El puntaje de este componente es propio de cada estudiante, así que integrantes del mismo grupo pueden obtener notas distintas.

Si un integrante obtiene **menos de 3 de los 7 puntos**, se entiende que no puede explicar la solución entregada y **su nota del proyecto se limita a 60 puntos (3.0)**, sin importar el puntaje del grupo. Si no se presenta a la sustentación sin una excusa válida, su nota del proyecto es 0.0.

**2. Historial del repositorio (3 puntos, nota de grupo).** El grupo trabaja en su repositorio desde el inicio del proyecto y cada integrante hace commits con su propio usuario. Se revisa el historial completo hasta la fecha de entrega:

| Historial | Puntos |
|---|---|
| Commits graduales en al menos 4 días distintos, con mensajes que describen el cambio, y aportes de todos los integrantes | 3 |
| Commits en 2 o 3 días distintos, o con integrantes sin aportes | 1,5 |
| Todo el trabajo en 1 o 2 commits, o todo en el último día | 0 |

Un historial con pocos commits no es por sí solo prueba de una falta, pero en ese caso la sustentación profundizará en la autoría del código. No reescriban el historial (`git push --force`, *squash* o *rebase* de commits ya publicados): un historial reescrito se califica con 0 en este componente.

**3. Revisión de similitud (sin puntos, condición de validez).** Todas las entregas se comparan con una herramienta de detección de similitud de código (MOSS) entre sí y con soluciones publicadas en internet, en particular las del laboratorio Copy-on-Write del MIT. Si se encuentra una similitud alta que el grupo no puede explicar en la sustentación, los criterios afectados de la rúbrica se califican con 0 y el caso se trata como posible fraude académico, según la sección de integridad académica. Usar como referencia el material de estudio del enunciado está permitido. Copiar código de otro grupo o de una solución publicada no lo está.

# Rúbrica de evaluación

El proyecto se califica sobre 100 puntos, que se convierten a la escala del curso de 0.0 a 5.0 con la fórmula nota = puntos × 5 / 100. Por ejemplo, 84 puntos equivalen a una nota de 4.2.

| Criterio | Qué se verifica | Puntos |
|---|---|---|
| Parte A: Copy-on-Write | `fork()` comparte páginas; copia al escribir; no copia con una sola referencia; `copyout()`; convive con páginas perezosas y `USYSCALL`; sin pánico por falta de memoria; sin fugas | 40 |
| Parte B: `cowstat()` | Memoria libre, páginas compartidas y contadores por proceso correctos | 10 |
| Parte C.2: snapshot | Snapshot consistente escrito por el hijo mientras el padre aplica transferencias; verificación del archivo; reporte de páginas tocadas y copias CoW | 20 |
| Regresión y MiniDB completo | `pgtbltest` y `usertests -q` pasan; `minidb consulta` y `minidb mapa` dan los resultados esperados con su kernel | 5 |
| Calidad y organización del código | Código claro, sin código muerto, comentarios donde la decisión no es obvia | 5 |
| Documentación | README con la estructura del Anexo A y respuestas correctas; reporte técnico con el diagrama y los experimentos | 10 |
| Sustentación | Preguntas individuales al azar (7, individual) e historial del repositorio (3, de grupo); ver "Cómo se califica la sustentación" | 10 |
| **Total** | | **100 (5.0)** |

Los puntos de las Partes A, B y C se asignan según las pruebas que pasen y la revisión del código. Un kernel que no compila o que entra en pánico durante las pruebas obtiene como máximo 30 puntos (1.5).

La nota de cada estudiante es: puntos del grupo en los criterios de código y documentación (90) + su puntaje individual de sustentación (hasta 7) + el puntaje del historial (hasta 3), con el límite de 60 puntos descrito en la sustentación cuando corresponda.

# Fechas y políticas

| Hito | Fecha |
|---|---|
| Publicación del enunciado | Viernes 6 de noviembre de 2026 |
| Seguimiento: Parte A funcionando con `cowtest` | Viernes 13 de noviembre de 2026 |
| Festivo (sin clase) | Lunes 16 de noviembre de 2026 |
| Entrega del Proyecto 4 | Jueves 19 de noviembre de 2026, 23:59:59 |

## Organización del trabajo

- Modalidad: trabajo grupal.
- Tamaño de los grupos: máximo tres (3) estudiantes por grupo.
- Peso: 25 % de la nota final del curso.

Plan sugerido para los 13 días:

| Días | Trabajo |
|---|---|
| 6 al 9 de noviembre | Leer la sección de Copy-on-Write y el material de estudio. Conteo de referencias en `kalloc.c`: sin cambiar nada más, `usertests -q` debe seguir pasando |
| 10 al 13 de noviembre | `uvmcopy()`, el page fault de escritura y `copyout()`. Llegar al seguimiento del viernes 13 con `cowtest` casi completo |
| 14 al 17 de noviembre | `cowstat()` y `minidb snapshot`. Correr `minidb consulta` y `minidb mapa` con su kernel |
| 18 y 19 de noviembre | Experimentos, README, reporte y evidencia con `ejecutar_pruebas.py` |

## Política de entrega

Solamente será evaluado el último commit realizado antes de la fecha y hora límite. Los commits posteriores no serán considerados.

Es responsabilidad exclusiva del grupo verificar que el repositorio sea accesible, que los archivos estén disponibles y que el contenido corresponda a la versión que desean evaluar.

## Integridad académica

Todos los estudiantes deberán cumplir las disposiciones de integridad académica establecidas en el Reglamento Académico de Pregrado de la Universidad EAFIT. Las situaciones de fraude académico, copia, suplantación o apropiación indebida de código serán tratadas conforme a la normativa institucional vigente.

## Uso de Inteligencia Artificial Generativa

La Universidad EAFIT promueve el uso ético y responsable de herramientas de inteligencia artificial generativa, en concordancia con los principios de la Alianza 4U. Los estudiantes podrán usarlas como apoyo para comprender conceptos, consultar alternativas de diseño, revisar código y generar ejemplos.

Sin embargo:

- Los estudiantes son responsables del código entregado.
- Deben comprender completamente las soluciones utilizadas.
- Deben poder explicar y justificar cualquier fragmento de código incluido en la entrega.
- El uso de IA no exime la responsabilidad académica sobre la autoría y calidad de la solución.

## Cambios en el alcance

Como indica el syllabus, el alcance puede ajustarse según el ritmo del curso. Cualquier cambio se anunciará en clase.

# Anexo A. Estructura obligatoria del README.md

```markdown
# Proyecto de Sistemas Operativos
## Proyecto 4: Copy-on-Write y MiniDB (xv6)

### Integrantes

- Nombre completo 1
- Nombre completo 2
- Nombre completo 3

### Curso

Sistemas Operativos

### Semestre

2026-2

---

# Descripción

Resumen de la solución de las Partes A, B y C.

---

# Archivos modificados

Para cada archivo (kernel/... y user/...): qué se cambió y por qué.

---

# Integración con xv6

1. Clonar xv6-riscv, hacer `git switch -c proyecto4 06aad25` y
   aplicar `git apply base_proyecto3.patch`.
2. Copiar los archivos entregados en sus rutas.
3. Copiar los archivos del docente: user/cowtest.c, user/minidb.h,
   user/pgtbltest.c y user/usertests.c.
4. Compilar con `make qemu CPUS=3`.
5. Ejecutar `cowtest`, `minidb consulta 3`, `minidb snapshot 2000`,
   `pgtbltest` y `usertests -q`.

---

# Respuestas a las preguntas

## 1. PTE antes y después de una escritura CoW
## 2. Páginas sin PTE_W y USYSCALL en fork()
## 3. Carrera en el conteo de referencias
## 4. Consistencia del snapshot
## 5. Aceleración y planificador

---

# Resultados de las pruebas

Resumen de cowtest, minidb, pgtbltest y usertests (salida completa en pruebas/).

---

# Decisiones de diseño

---

# Limitaciones conocidas

---

# Declaración de uso de IA

Herramientas usadas y para qué. Los integrantes asumen plena
responsabilidad académica sobre el contenido entregado.
```

# Anexo B. Formato de la salida de MiniDB

El script de pruebas lee estas líneas, así que deben imprimirse exactamente con este formato (los números de ejemplo cambian). Pueden imprimir líneas adicionales, siempre que no empiecen igual que estas.

**`minidb consulta K`**

```
consulta: trabajadores=3
trabajador pid=6 cuentas=[0,2730)
trabajador pid=7 cuentas=[2730,5461)
trabajador pid=8 cuentas=[5461,8192)
sucursal 0: cuentas=1024 total=25610224 max=50055
...                                   (una línea por sucursal, de 0 a 7)
sucursal 7: cuentas=1024 total=25680128 max=50051
consulta: firma=0x2DD2F610
consulta: paginas_libres antes=32430 con_trabajadores=32403
consulta: ticks=14
consulta: OK
```

La firma es la suma (módulo 2^32) de las firmas de las ocho sucursales, impresa con `%x`.

**`minidb snapshot M`**

```
snapshot: checksum_inicial=0x6915D295
snapshot: hijo pid=10 escribiendo snap.db
snapshot: transferencias=2000 aplicadas=1978 rechazadas=22
snapshot: paginas_tocadas=30 fallos_cow=30 copias_cow=30
snapshot: archivo bytes=262160 checksum=0x6915D295 coincide=si
snapshot: checksum_actual=0x4AB3E209 total_conservado=si
snapshot: OK
```

**`minidb mapa`**

```
mapa: tabla en 0x6000 (2 páginas)
mapa: hijo antes de escribir
page table 0x...                      (salida de vmprint())
mapa: hijo después de escribir en 0x6000
page table 0x...                      (salida de vmprint())
```
