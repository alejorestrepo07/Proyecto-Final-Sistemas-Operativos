// Physical memory allocator, for user processes,
// kernel stacks, page-table pages,
// and pipe buffers. Allocates whole 4096-byte pages.

#include "types.h"
#include "param.h"
#include "memlayout.h"
#include "spinlock.h"
#include "riscv.h"
#include "defs.h"

void freerange(void *pa_start, void *pa_end);

extern char end[]; // first address after kernel.
                   // defined by kernel.ld.

struct run {
  struct run *next;
};

struct {
  struct spinlock lock;
  struct run *freelist;
} kmem;

// Conteo de referencias por pagina fisica entre KERNBASE y PHYSTOP.
// Un spinlock propio protege el arreglo. Nunca se toma junto con
// kmem.lock (primero se suelta uno y despues se toma el otro).
#define NPAGES ((PHYSTOP - KERNBASE) / PGSIZE)
#define PAGEIDX(pa) (((uint64)(pa) - KERNBASE) / PGSIZE)

struct {
  struct spinlock lock;
  uint8 cnt[NPAGES];
} refs;

void
kinit()
{
  initlock(&kmem.lock, "kmem");
  initlock(&refs.lock, "refs");
  freerange(end, (void *)PHYSTOP);
}

void
freerange(void *pa_start, void *pa_end)
{
  char *p;
  p = (char *)PGROUNDUP((uint64)pa_start);
  for (; p + PGSIZE <= (char *)pa_end; p += PGSIZE) {
    // estas paginas nunca pasaron por kalloc(): se les da la
    // referencia que kfree() va a quitar.
    refs.cnt[PAGEIDX(p)] = 1;
    kfree(p);
  }
}

// Agrega una referencia a la pagina fisica pa (que ya esta en uso).
void
krefinc(void *pa)
{
  if (((uint64)pa % PGSIZE) != 0 || (char *)pa < end || (uint64)pa >= PHYSTOP)
    panic("krefinc");

  acquire(&refs.lock);
  if (refs.cnt[PAGEIDX(pa)] < 1)
    panic("krefinc: pagina libre");
  refs.cnt[PAGEIDX(pa)]++;
  release(&refs.lock);
}

// Numero de referencias de la pagina fisica pa.
int
krefcnt(void *pa)
{
  int n;

  acquire(&refs.lock);
  n = refs.cnt[PAGEIDX(pa)];
  release(&refs.lock);
  return n;
}

// Numero de paginas en la lista libre.
uint64
kfreepages(void)
{
  struct run *r;
  uint64 n = 0;

  acquire(&kmem.lock);
  for (r = kmem.freelist; r; r = r->next)
    n++;
  release(&kmem.lock);
  return n;
}

// Numero de paginas fisicas con mas de una referencia.
uint64
ksharedpages(void)
{
  uint64 n = 0;

  acquire(&refs.lock);
  for (int i = 0; i < NPAGES; i++)
    if (refs.cnt[i] > 1)
      n++;
  release(&refs.lock);
  return n;
}

// Quita una referencia a la pagina de memoria fisica pa. Solo cuando
// llega a 0 la devuelve a la lista libre.  pa normalmente viene de
// kalloc(); la excepcion es freerange(), que le fija 1 antes de llamar.
void
kfree(void *pa)
{
  struct run *r;

  if (((uint64)pa % PGSIZE) != 0 || (char *)pa < end || (uint64)pa >= PHYSTOP)
    panic("kfree");

  acquire(&refs.lock);
  if (refs.cnt[PAGEIDX(pa)] < 1)
    panic("kfree: sin referencias");
  int quedan = --refs.cnt[PAGEIDX(pa)];
  release(&refs.lock);
  if (quedan > 0)
    return; // otro proceso todavia la usa

  // Fill with junk to catch dangling refs.
  memset(pa, 1, PGSIZE);

  r = (struct run *)pa;

  acquire(&kmem.lock);
  r->next = kmem.freelist;
  kmem.freelist = r;
  release(&kmem.lock);
}

// Allocate one 4096-byte page of physical memory.
// Returns a pointer that the kernel can use.
// Returns 0 if the memory cannot be allocated.
void *
kalloc(void)
{
  struct run *r;

  acquire(&kmem.lock);
  r = kmem.freelist;
  if (r)
    kmem.freelist = r->next;
  release(&kmem.lock);

  if (r) {
    memset((char *)r, 5, PGSIZE); // fill with junk
    acquire(&refs.lock);
    refs.cnt[PAGEIDX(r)] = 1;
    release(&refs.lock);
  }
  return (void *)r;
}
