/* dest-side working set cap (memcpy dest, memset dest, ...) */
#define MAX_DEST (1024*1024*4)
/* heap: dest + src (+ extra buffer for trans8x8/hist) */
#define MAX_MEM (MAX_DEST * 3 + 4096)
/* the byte count for the next run */
#define NEXT(c) (c + c/7 + 3)

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
#define MIN_REPEATS 5
/* maximum number of repeats, executed until more than STOP_CYCLES has elapsed */
#define MAX_REPEATS 10
#define VALIDATE 0
#endif

/* stop repeats early afer this many cycles have elapsed */
#define STOP_CYCLES (1024*1024*500)

#ifndef REF_IMPL
#define REF_IMPL scalar
#endif

/* custom scaling factors for benchmarks, these are used to make sure each
 * benchmark approximately takes the same amount of time. */

#define SCALE_mandelbrot(N) ((N)/10)
#define SCALE_mergelines(N) ((N)/10)

/* benchmark specific configurations */
#define mandelbrot_ITER 100
