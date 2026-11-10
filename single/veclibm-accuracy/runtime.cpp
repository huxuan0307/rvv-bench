// Bare-metal result reporting for the deliberately restricted GTest adapter.
#include "av_runtime.h"
#include <stdarg.h>
extern "C" {
int vprintf_(const char *, va_list);
int accuracy_note(const char *format, ...) {
  // A whole call containing a long-double conversion is suppressed before
  // consuming varargs; all ordinary diagnostics (especially special bits) stay.
  for (const char *p = format; *p; ++p) {
    if (*p != '%') continue;
    ++p;
    if (*p == '%') continue;
    for (; *p; ++p) {
      if (*p == 'L') return 0;
      if ((*p >= 'a' && *p <= 'z' && *p != 'l' && *p != 'h' && *p != 'j' &&
           *p != 't' && *p != 'z') || *p == 'X' || *p == 'E' || *p == 'F' ||
          *p == 'G' || *p == 'A' || *p == '%') break;
    }
    if (!*p) break;
  }
  va_list args;
  va_start(args, format);
  int result = vprintf_(format, args);
  va_end(args);
  return result;
}
unsigned long accuracy_checks;
unsigned long accuracy_failures;
void __assert_fail(const char *expr, const char *file, unsigned int line,
                   const char *function) {
  printf_("ERROR VECLIBM_C_ASSERT expr=%s file=%s line=%u function=%s\n",
          expr, file, line, function);
  abort();
}
void accuracy_check(int ok, const char *op, const char *lhs, const char *rhs,
    const char *file, int line, double a, double b,
    unsigned long long bits_a, unsigned long long bits_b) {
  ++accuracy_checks;
  if (!ok) ++accuracy_failures;
  // Keep original numerical/bitwise comparisons in the calling translation unit.
  // Only presentation is narrowed to double; the comparison is never narrowed.
  if (op[0] == 'L') {
    printf_("VECLIBM_ASSERT op=%s pass=%d lhs=%.17g rhs=%.17g file=%s line=%d expr=%s,%s\n",
      op, ok, a, b, file, line, lhs, rhs);
  } else if (!ok) {
    printf_("VECLIBM_ASSERT op=%s pass=%d lhs_bits=%016llx rhs_bits=%016llx file=%s line=%d expr=%s,%s\n",
      op, ok, bits_a, bits_b, file, line, lhs, rhs);
  }
}
}
