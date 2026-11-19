/* Reuse AM/TLS/stdio support and satisfy glibc's internal errno alias.
 * Some real libm helpers (frexp/ldexp/scalbn) reside in libc.a. Without this
 * alias libc's errno.o duplicates the runtime's TLS errno. No hosted startup
 * or approximate math replacement is needed. */
#include "../../autovec/av_runtime.c"
extern __thread int __libc_errno __attribute__ ((alias ("errno")));
