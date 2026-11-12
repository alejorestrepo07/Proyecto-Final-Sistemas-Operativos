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

Este README cubre la **Parte A: Copy-on-Write en `fork()`**. Las Partes B (`cowstat()`) y C (MiniDB) se documentarán cuando estén terminadas.

Después de `fork()`, padre e hijo comparten las mismas páginas físicas, marcadas como de solo lectura con el bit `PTE_COW`. La página se copia solo cuando alguno de los dos escribe en ella. Un conteo de referencias por página física decide cuándo devolverla a la lista libre.

---

# Archivos modificados

| Archivo | Cambio |
|---|---|
| `kernel/riscv.h` | Define `PTE_COW` como `1L << 8`, un bit RSW reservado para software. |
| `kernel/kalloc.c` | Arreglo de conteos de referencias (una entrada por página entre `KERNBASE` y `PHYSTOP`) con su propio spinlock. `kalloc()` deja 1 referencia, `krefinc()` suma una, `krefcnt()` la consulta y `kfree()` resta una y libera solo al llegar a 0. `freerange()` fija 1 antes de llamar a `kfree()`, porque esas páginas nunca pasaron por `kalloc()`. |
| `kernel/vm.c` | `uvmcopy()` ya no copia: mapea la misma página en el hijo, suma una referencia y, si era escribible, quita `PTE_W` y pone `PTE_COW` en padre e hijo (con `sfence_vma()` para el TLB del padre). Función nueva `cowfault()`: con más de una referencia copia la página; con una sola, solo devuelve `PTE_W`. `copyout()` llama a `cowfault()` antes de escribir en una página CoW. |
| `kernel/trap.c` | `usertrap()` atiende los fallos de escritura (`scause` = 15) con `cowfault()`. Si no hay memoria para copiar, el proceso se mata por la ruta normal, sin pánico. |
| `kernel/defs.h` | Prototipos de `krefinc`, `krefcnt` y `cowfault`. |

---

# Integración con xv6

1. Clonar xv6-riscv, hacer `git switch -c proyecto4 06aad25` y aplicar `git apply base_proyecto3.patch`.
2. Copiar los archivos entregados en sus rutas.
3. Copiar los archivos del docente: `user/cowtest.c`, `user/minidb.h`, `user/pgtbltest.c` y `user/usertests.c`.
4. Compilar con `make qemu CPUS=3`.
5. Ejecutar `cowtest`, `pgtbltest` y `usertests -q`.

`cowtest` necesita `cowstat()` (Parte B) para compilar.

---

# Respuestas a las preguntas (relacionadas con la Parte A)

## 2. Páginas sin PTE_W y USYSCALL en fork()

`uvmcopy()` solo marca como CoW las páginas que tenían `PTE_W`. El código y la página de guarda de la pila no son escribibles, así que se comparten tal cual, sin `PTE_COW`. Si se marcaran como CoW, un proceso podría escribir en su propio código después de un `fork()`: el fallo de escritura se resolvería como CoW en lugar de matar al proceso.

`USYSCALL` y el trapframe no se copian con `uvmcopy()`: son propios de cada proceso y los crea `allocproc()`. Si `USYSCALL` se compartiera, el hijo leería el pid del padre y `ugetpid()` devolvería un valor incorrecto.

## 3. Carrera en el conteo de referencias

Sin lock, dos CPUs hacen `kfree()` de la misma página a la vez, con conteo 2:

1. La CPU 0 lee 2 y la CPU 1 lee 2.
2. Las dos escriben 1.
3. El conteo queda en 1 aunque ya nadie usa la página, y esta se pierde para siempre (fuga).

Con `krefinc()` y `kfree()` pasa lo mismo al revés: dos incrementos simultáneos pueden dejar el conteo en 2 en lugar de 3, y cuando el primer proceso termine liberará una página que el otro todavía usa. Por eso todos los accesos al arreglo van bajo `refs.lock`.

---

# Resultados de las pruebas

Probado con `make qemu CPUS=3`, sin advertencias de compilación (con un `cowstat()` provisional solo para poder compilar `cowtest`):

| Suite | Resultado |
|---|---|
| `cowtest` | 11 de 11 |
| `pgtbltest` | 11 de 11 |
| `usertests -q` | ALL TESTS PASSED |

---

# Decisiones de diseño

- **Lock propio para el conteo**, distinto de `kmem.lock`. Nunca se toman al mismo tiempo, así que no hay riesgo de deadlock.
- **`cowfault()` en `vm.c`**, compartida por `usertrap()` y `copyout()`.
- **Una sola referencia:** no se copia, solo se devuelve `PTE_W`.
- **Conteo de 8 bits** (`uint8`): alcanza porque xv6 tiene `NPROC` = 64 procesos.

---

# Limitaciones conocidas

- Si dos procesos fallan a la vez sobre una página con 2 referencias, ambos pueden copiarla. Es correcto, porque la página vieja se libera al llegar a 0, pero hace una copia de más.
- Faltan las Partes B y C, y las respuestas 1, 4 y 5.

---

# Declaración de uso de IA

Se usó Claude Code como apoyo para implementar y probar la Parte A. Los integrantes asumen plena responsabilidad académica sobre el contenido entregado.
