#include "av_runtime.h"
#include <stdarg.h>
extern void _halt(int);
extern void __real__trm_init(void);
extern int vprintf_(const char *, va_list);
extern unsigned char _heap_start[];
extern unsigned char _pmem_end[];
static uintptr_t heap_next;
static uint64_t start_tick;
volatile double av_sink;
/* This wrapper executes before benchmark code can use vector registers. */
void __wrap__trm_init(void) {
    __asm__ volatile("la tp, __tls_base" ::: "memory");
    uintptr_t status;
    __asm__ volatile("csrr %0, mstatus" : "=r"(status));
    status |= (3UL << 9) | (3UL << 13);
    __asm__ volatile("csrw mstatus, %0; csrwi vcsr, 0; csrwi fcsr, 0" :: "r"(status));
    __real__trm_init();
}
uint64_t av_ticks(void) {
    uint64_t value;
    __asm__ volatile("rdcycle %0" : "=r"(value) :: "memory");
    return value;
}
void av_begin(const char *name) { (void)name; start_tick = av_ticks(); }
void av_end(const char *name) {
    uint64_t elapsed = av_ticks() - start_tick;
    printf_("AV case=%s cycles=%llu\n", name, (unsigned long long)elapsed);
}
void av_observe(double value) { av_sink = value; printf_("CHECKSUM %.9g\n", value); }
void *malloc(size_t size) {
    if (!heap_next) heap_next = (uintptr_t)_heap_start;
    if (size > (uintptr_t)_pmem_end - heap_next - 64) { printf_("ERROR heap exhausted\n"); _halt(1); }
    uintptr_t p = (heap_next + 63) & ~(uintptr_t)63;
    heap_next = p + size;
    return (void *)p;
}
void free(void *p) { (void)p; }
void *calloc(size_t n, size_t size) {
    if (size && n > SIZE_MAX / size) abort();
    void *p = malloc(n * size); memset(p, 0, n * size); return p;
}
int posix_memalign(void **p, size_t alignment, size_t size) {
    if (alignment < sizeof(void *) || (alignment & (alignment - 1))) return 22;
    if (size > SIZE_MAX - alignment) return 12;
    uintptr_t raw = (uintptr_t)malloc(size + alignment);
    *p = (void *)((raw + alignment - 1) & ~(uintptr_t)(alignment - 1)); return 0;
}
void abort(void) { printf_("ERROR abort\n"); _halt(1); for (;;) {} }
void exit(int code) { _halt(code); for (;;) {} }
__thread int errno;
int *__errno_location(void) { return &errno; }
int gettimeofday(struct timeval *restrict tv, void *restrict tz) {
    (void)tz; tv->tv_sec = 0; tv->tv_usec = (long)av_ticks(); return 0;
}
FILE *stderr;
FILE *stdout;
int fprintf(FILE *stream, const char *format, ...) {
    (void)stream; va_list args; va_start(args, format);
    int n = vprintf_(format, args); va_end(args); return n;
}
int puts(const char *s) { return printf_("%s\n", s); }
int putchar(int c) { extern void _putc(char); _putc((char)c); return c; }
