/* Shared AM/TLS runtime, with glibc internal errno name for its real math
 * helper objects. No libc startup or approximate numerical stub is used. */
#include "../../autovec/av_runtime.c"
extern __thread int __libc_errno __attribute__((alias("errno")));
