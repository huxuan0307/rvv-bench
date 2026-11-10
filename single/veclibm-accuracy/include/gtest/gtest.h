// Restricted compatibility adapter for the veclibm source tests, NOT GoogleTest.
// Supported API is deliberately only TEST, EXPECT_LT and EXPECT_EQ.
#ifndef VECLIBM_ACCURACY_GTEST_H
#define VECLIBM_ACCURACY_GTEST_H
#include <stddef.h>
#include <stdint.h>
#include <assert.h>
#include "av_runtime.h"
extern "C" void accuracy_check(int ok, const char *op, const char *lhs,
    const char *rhs, const char *file, int line, double a, double b,
    unsigned long long bits_a, unsigned long long bits_b);
// Upstream diagnostic printf uses %L (binary128), unsupported by AM klib's
// formatter. Suppress only those diagnostic calls; assertions below log their
// actual operands with the runtime's supported double/integer formats.
extern "C" int accuracy_note(const char *format, ...);
#undef printf
#define printf accuracy_note
#define TEST(suite, name) void accuracy_test_##suite##_##name(void)
#define EXPECT_LT(a, b) do { \
    auto accuracy_a = (a); auto accuracy_b = (b); \
    accuracy_check(accuracy_a < accuracy_b, "LT", #a, #b, __FILE__, __LINE__, \
      (double)accuracy_a, (double)accuracy_b, 0, 0); \
  } while (0)
#define EXPECT_EQ(a, b) do { \
    auto accuracy_a = (a); auto accuracy_b = (b); \
    accuracy_check(accuracy_a == accuracy_b, "EQ", #a, #b, __FILE__, __LINE__, \
      0, 0, (unsigned long long)accuracy_a, (unsigned long long)accuracy_b); \
  } while (0)
#endif
