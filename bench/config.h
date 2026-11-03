/* dest-side working set cap (memcpy dest, memset dest, ...) */
#ifndef MAX_DEST
#define MAX_DEST (1024*1024*4)
#endif
/* heap: dest + src (+ extra buffer for trans8x8/hist) */
#define MAX_MEM (MAX_DEST * 3 + 4096)
/* Benchmark size progression; bench.h supplies previous/current values. */
#if defined(NEXT_ALGO_ORIGIN)
#define NEXT(previous, current) ((current) + (current) / 7 + 3)
#elif defined(NEXT_ALGO_FIBONACCI)
#define NEXT(previous, current) ((previous) + (current))
#else
#error "NEXT_ALGO must be ORIGIN or FIBONACCI"
#endif

/* memcpy: initialize the destination before each benchmark case. */
#ifndef MEMCPY_INIT
#define MEMCPY_INIT 1
#endif

/* CHECK=1: one-shot scalar reference compare. CHECK=0: timing. */
#ifndef CHECK
#define CHECK 0
#endif

#if CHECK
#define MIN_REPEATS 1
#define MAX_REPEATS 1
#define VALIDATE 1
#else
/* minimum number of repeats */
#define MIN_REPEATS 2
/* maximum number of repeats, executed until more than STOP_CYCLES has elapsed */
#define MAX_REPEATS 3
#define VALIDATE 0
#endif

/* stop repeats early afer this many cycles have elapsed */
#define STOP_CYCLES (1024*1024*1)

#ifndef REF_IMPL
#define REF_IMPL scalar
#endif

/* custom scaling factors for benchmarks, these are used to make sure each
 * benchmark approximately takes the same amount of time. */

#define SCALE_mandelbrot(N) ((N)/10)
#define SCALE_mergelines(N) ((N)/10)

/* benchmark specific configurations */
#define mandelbrot_ITER 100
