"""Generate freestanding adaptations of LLVM's Vectorizer scalar-loop tests.

Only generated files contain upstream code. See llvm_notes.md for the catalog,
semantics of harness substitutions, and the runtime/build contract.
"""
from __future__ import annotations

import pathlib
import re


# These are harness facilities, not substitute benchmark kernels. The generated
# translation units do not include any hosted C++ headers or require libstdc++.
_SUPPORT = r'''
#ifndef AV_LLVM_SUPPORT_H
#define AV_LLVM_SUPPORT_H
#include "av_runtime.h"
#include <stdint.h>
#include <stddef.h>
// C math macros would rename std::isnan to std::__builtin_isnan recursively.
#ifdef isnan
#undef isnan
#endif
#ifdef signbit
#undef signbit
#endif

#if defined(__GNUC__) && !defined(__clang__)
#define AV_LLVM_SCALAR __attribute__((optimize("no-tree-vectorize,no-tree-slp-vectorize")))
#else
#define AV_LLVM_SCALAR
#endif
inline void *operator new(size_t, void *p) noexcept { return p; }

namespace av_llvm {
static unsigned checksum = 2166136261u;
static unsigned observations = 0;
struct sampled_positions {
  unsigned values[12]; unsigned count = 0;
  const unsigned *begin() const { return values; }
  const unsigned *end() const { return values + count; }
};
static sampled_positions positions(unsigned n, unsigned stride) {
  sampled_positions p;
  unsigned candidates[] = {0u, 1u, 2u, 3u, 7u, 15u, 16u, n / 2, n - 1, n};
  for (unsigned v : candidates) {
    unsigned x = v / stride * stride;
    bool found = false;
    for (unsigned i=0; i<p.count; ++i) if(p.values[i] == x) found=true;
    if (!found) p.values[p.count++] = x;
  }
  return p;
}
static void observe_word(unsigned x) {
  checksum = (checksum ^ x) * 16777619u;
  ++observations;
}
template<class T> constexpr bool floating = false;
template<> constexpr bool floating<float> = true;
template<> constexpr bool floating<double> = true;
template<class T> void observe(T x) {
  if constexpr (floating<T>) {
    // Ignore NaN payloads and the zero sign, as the upstream check does.
    if (__builtin_isnan(x)) { observe_word(0x7fc00000u); return; }
    if (x == 0) x = 0;
  }
  const unsigned char *p = reinterpret_cast<const unsigned char *>(&x);
  unsigned h = 0;
  for (size_t i = 0; i < sizeof(T); ++i) h = h * 257u + p[i];
  observe_word(h);
}
template<class T> T *allocate(size_t n) {
  T *p = static_cast<T *>(malloc(sizeof(T) * n));
  if (!p) abort();
  return p;
}
}

// Deliberately limited implementations of the upstream harness's interfaces.
// No headers from the standard C++ library are used or shadowed on disk.
namespace std {
template<class T> class initializer_list {
  const T *p; size_t n;
  constexpr initializer_list(const T *a, size_t b): p(a), n(b) {}
public:
  constexpr initializer_list(): p(nullptr), n(0) {}
  constexpr const T *begin() const { return p; }
  constexpr const T *end() const { return p+n; }
  constexpr size_t size() const { return n; }
};
template<class T> constexpr T &&forward(T &x) { return static_cast<T &&>(x); }
template<class T> constexpr T &&forward(T &&x) { return static_cast<T &&>(x); }
template<class A, class B> struct pair { A first; B second; };
template<class A, class B> auto make_pair(A a, B b) { return pair<A,B>{a,b}; }
template<class T, size_t N> struct array {
  T values[N];
  T *begin() { return values; } T *end() { return values+N; }
  const T *begin() const { return values; }
  const T *end() const { return values+N; }
};
template<class T, class... U> array(T, U...) -> array<T, 1+sizeof...(U)>;

template<class T> class unique_ptr;
template<class T> class unique_ptr<T[]> {
  T *p;
public:
  explicit unique_ptr(T *v): p(v) {}
  unique_ptr(const unique_ptr &) = delete;
  ~unique_ptr() { free(p); }
  T &operator[](size_t i) const { return p[i]; }
};

template<class Signature> class function;
template<class R, class... A> class function<R(A...)> {
  alignas(16) unsigned char data[128];
  R (*invoke)(const void *, A...);
public:
  function(const function &) = default;
  function &operator=(const function &) = default;
  template<class F> function(const F &f) {
    static_assert(sizeof(F) <= sizeof(data), "LLVM callback storage");
    static_assert(__is_trivially_copyable(F), "LLVM callbacks must be trivial");
    new (data) F(f);
    invoke = [](const void *p, A... args) -> R {
      return (*static_cast<const F *>(p))(args...);
    };
  }
  R operator()(A... args) const {
    if constexpr (__is_same(R, void)) { invoke(data, args...); }
    else {
      R r = invoke(data, args...);
      if constexpr (__is_same(R, float) || __is_same(R, double) ||
                    __is_same(R, int) || __is_same(R, unsigned) ||
                    __is_same(R, long) || __is_same(R, unsigned long) ||
                    __is_same(R, short) || __is_same(R, unsigned short) ||
                    __is_same(R, signed char) || __is_same(R, unsigned char) ||
                    __is_same(R, long long) || __is_same(R, unsigned long long))
        av_llvm::observe(r);
      return r;
    }
  }
};

template<class T> class vector {
  T *p = nullptr; size_t n = 0, capacity = 0;
public:
  vector() = default;
  vector(initializer_list<T> xs) { for (auto x : xs) push_back(x); }
  vector(const vector &x) { for (auto v : x) push_back(v); }
  vector(vector &&x): p(x.p), n(x.n), capacity(x.capacity) { x.p=nullptr; x.n=0; }
  ~vector() { free(p); }
  void push_back(T x) {
    if (n == capacity) {
      size_t c = capacity ? capacity * 2 : 8;
      T *q = av_llvm::allocate<T>(c);
      for (size_t i=0; i<n; ++i) q[i]=p[i];
      free(p); p=q; capacity=c;
    }
    p[n++] = x;
  }
  size_t size() const { return n; }
  T &operator[](size_t i) { return p[i]; }
  const T &operator[](size_t i) const { return p[i]; }
  const T *begin() const { return p; } const T *end() const { return p+n; }
};

template<class T> struct numeric_limits {
  static constexpr bool sign = T(-1) < T(0);
  static constexpr T max() {
    if constexpr (sign) return T((1ULL << (sizeof(T)*8-1))-1);
    else return T(~0ULL);
  }
  static constexpr T min() { if constexpr (sign) return T(-max()-1); else return 0; }
  static constexpr T lowest() { return min(); }
};
template<> struct numeric_limits<float> {
  static constexpr float min() { return __FLT_MIN__; }
  static constexpr float max() { return __FLT_MAX__; }
  static constexpr float lowest() { return -__FLT_MAX__; }
  static constexpr float denorm_min() { return __FLT_DENORM_MIN__; }
  static constexpr float quiet_NaN() { return __builtin_nanf(""); }
  static constexpr float infinity() { return __builtin_inff(); }
};
template<> struct numeric_limits<double> {
  static constexpr double min() { return __DBL_MIN__; }
  static constexpr double max() { return __DBL_MAX__; }
  static constexpr double lowest() { return -__DBL_MAX__; }
  static constexpr double denorm_min() { return __DBL_DENORM_MIN__; }
  static constexpr double quiet_NaN() { return __builtin_nan(""); }
  static constexpr double infinity() { return __builtin_inf(); }
};
template<class T> constexpr bool is_floating_point_v = av_llvm::floating<T>;

// Fixed seed LCG replaces hosted <random>; boundary cases remain upstream's.
struct mt19937 {
  unsigned state;
  mt19937(unsigned s=5489u): state(s) {}
  unsigned operator()() { state = state*1664525u+1013904223u; return state; }
};
template<class T> struct uniform_int_distribution {
  uniform_int_distribution(T, T) {}
  T operator()(mt19937 &g) { return T(g()); }
};
template<class T> struct uniform_real_distribution {
  uniform_real_distribution(T, T) {}
  T operator()(mt19937 &g) { return T(int(g()%20001u)-10000)/T(32); }
};

template<class T> struct greater { bool operator()(T a,T b) const { return a>b; } };
template<class T> struct less { bool operator()(T a,T b) const { return a<b; } };
template<class T, class C> void sort(T *a,T *b,C cmp) {
  // Shell sort is used only to prepare test inputs.
  size_t n=b-a;
  for (size_t gap=n/2; gap; gap/=2)
    for (size_t i=gap; i<n; ++i) {
      T v=a[i]; size_t j=i;
      while (j>=gap && cmp(v,a[j-gap])) { a[j]=a[j-gap]; j-=gap; }
      a[j]=v;
    }
}
template<class T> void sort(T *a,T *b) { sort(a,b,less<T>()); }
template<class T> void reverse(T *a,T *b) {
  while (a<b) { --b; if(a>=b) break; T t=*a; *a++=*b; *b=t; }
}
template<class T, class U> void fill_n(T *p,size_t n,U x) { for(size_t i=0;i<n;++i) p[i]=x; }
template<class T> bool equal(const T *a,const T *b,const T *c) {
  bool ok=true;
  for(;a!=b;++a,++c) { av_llvm::observe(*c); if(*a!=*c) ok=false; }
  return ok;
}
template<class T> T reduce(T *a,T *b) { T s=0; for(;a!=b;++a) s+=*a; return s; }
template<class T> T max(T a,T b) { return a>b?a:b; }
inline long abs(long x) { return x<0?-x:x; }
inline bool isnan(float x) { return __builtin_isnan(x); }
inline bool isnan(double x) { return __builtin_isnan(x); }
inline bool signbit(float x) { return __builtin_signbit(x); }
inline float fmin(float a,float b) { return __builtin_fminf(a,b); }
inline double fmin(double a,double b) { return __builtin_fmin(a,b); }
inline float fmax(float a,float b) { return __builtin_fmaxf(a,b); }
inline double fmax(double a,double b) { return __builtin_fmax(a,b); }

struct ostream {
  ostream &operator<<(const char *s) { printf("%s", s); return *this; }
  ostream &operator<<(float x) { av_llvm::observe(x); printf("<float>"); return *this; }
  ostream &operator<<(double x) { av_llvm::observe(x); printf("<double>"); return *this; }
  template<class T> ostream &operator<<(T x) { printf("%ld", (long)x); return *this; }
};
static ostream cout, cerr;
}
static void av_llvm_exit(int) { abort(); }
#define exit av_llvm_exit
#endif
'''

_CANDIDATES = (
    "gcc-loops.cpp", "any-of.cpp", "argmin-argmax-int.cpp",
    "argmin-argmax-fp.cpp", "early-exit.cpp", "find-last.cpp",
    "fmax-reduction.cpp", "recurrences.cpp", "runtime-checks.cpp",
    "VPlanNativePath/outer-loop-vect.cpp",
)


def _strip_hosted(text: str) -> str:
    return re.sub(r'^\s*#include\s*<[^>]+>\s*$', '', text, flags=re.M)


def _adapt(text: str) -> str:
    text = _strip_hosted(text)
    # GCC ignores clang loop pragmas: isolate the scalar correctness oracle.
    text = re.sub(r'(auto ScalarFn[\s\\]*=[\s\\]*\[\][\s\\]*(?:\((?:[^()]|\([^()]*\))*\)|Args))',
                  r'\1 AV_LLVM_SCALAR', text)
    # All upstream new[] uses allocate numeric arrays; keep RAII free ownership.
    text = re.sub(r'new\s+(\w+)\[([^\]]+)\]', r'av_llvm::allocate<\1>(\2)', text)
    # Names are diagnostic metadata; no dynamic strings are needed.
    text = re.sub(r'\(std::string\(P.Name\) \+ " [^"]+"\)\.c_str\(\)', 'P.Name', text)
    # Preserve the optimization barrier for GCC, which ignores optnone.
    text = text.replace('__attribute__((optnone))',
                        '__attribute__((noinline))')
    return text


def _gcc_loops(text: str) -> str:
    text = _strip_hosted(text)
    start = text.index('class Timer {')
    end = text.index('// Warmup and then measure.', start)
    text = text[:start] + text[end:]
    start = text.index('#define BENCH(')
    end = text.index('int main(', start)
    text = text[:start] + r'''
#define BENCH(NAME, RUN_LINE, ITER, DIGEST_LINE) { \
  RUN_LINE; \
  av_begin(NAME); \
  for (int i=0, count=(ITER)>0?(ITER):1; i<count; ++i) RUN_LINE; \
  av_end(NAME); \
  unsigned r = DIGEST_LINE; \
  av_llvm::observe(r); \
  printf("%s checksum=%u\n", NAME, r); \
}
''' + text[end:]
    text = text.replace('int main(int argc,char* argv[])', 'int llvm_test_main()')
    text = text.replace('  bool print_times = argc > 1;', '')
    text = text.replace('  std::vector<unsigned> results;', '')
    text = re.sub(r'#ifdef SMALL_PROBLEM_SIZE.*?#endif', 'const int Mi = 1;', text, flags=re.S)
    start = text.index('  std::cout<<std::hex;')
    end = text.index('  return 0;', start)
    text = text[:start] + text[end:]
    # Some upstream digest expressions observe unchanged arguments, not outputs.
    text = text.replace(
        'example4b(N-10, ia, ib), Mi*2, digest_memory(&ia[0], &ia[N])',
        'example4b(N-10, ia, ib), Mi*2, digest_memory(&a[0], &a[N-10])')
    text = text.replace(
        'example4c(N, ia, ib), Mi*2, digest_memory(&ib[0], &ib[N])',
        'example4c(N, ia, ib), Mi*2, digest_memory(&b[0], &b[N])')
    # Upstream hashes usa (the unchanged input), and uses literal 0 for example24.
    text = text.replace('digest_memory(&usa[0], &usa[256])',
                        'digest_memory(&ua[0], &ua[256])')
    text = text.replace('example24(2,4), Mi*2, 0',
                        'example24(2,4), Mi*2, digest_memory(&ic[0], &ic[N])')
    return text


def prepare(source: pathlib.Path, output: pathlib.Path) -> list[dict]:
    """Read a Vectorizer directory and return independent executable records.

    ``source`` may also be an llvm-test-suite checkout or a directory containing
    that checkout. ``output`` is the caller's ignored generated-source directory.
    Missing expected sources fail loudly rather than silently reducing coverage.
    """
    source = pathlib.Path(source).resolve()
    for candidate in (source, source / 'SingleSource/UnitTests/Vectorizer',
                      source / 'llvm-test-suite/SingleSource/UnitTests/Vectorizer'):
        if (candidate / 'gcc-loops.cpp').is_file():
            source = candidate
            break
    else:
        raise FileNotFoundError(f'No LLVM Vectorizer source directory under {source}')
    output = pathlib.Path(output).resolve() / 'llvm'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'av_llvm_support.h').write_text(_SUPPORT)
    for header in ('common.h', 'argmin-argmax-common.h'):
        text = _adapt((source / header).read_text())
        (output / header).write_text(text)
    records = []
    for relative in _CANDIDATES:
        text = (source / relative).read_text()
        if relative == 'gcc-loops.cpp':
            text = _gcc_loops(text)
        else:
            text = _adapt(text)
            text = text.replace('#include "../common.h"', '#include "common.h"')
            text, count = re.subn(r'int main\((?:void)?\)', 'int llvm_test_main()', text)
            if count != 1:
                raise ValueError(f'Expected exactly one main in {relative}')
            if relative == 'runtime-checks.cpp':
                text = text.replace(
                    'for (int i = -100; i <= 100; i++)',
                    'for (int i : {-100, -99, -33, -17, -16, -7, -2, -1, 0, 1, 2, 7, 16, 17, 33, 99, 100})')
                text = text.replace(
                    'for (int i = -(2 * (InnerTC + 1)); i <= (2 * (InnerTC + 1)); i++)',
                    'for (int i : {-(2 * (InnerTC + 1)), -InnerTC - 1, -InnerTC, -1, 0, 1, InnerTC, InnerTC + 1, 2 * (InnerTC + 1)})')
                text = text.replace(
                    'for (int Offset = -MaxOffset; Offset <= MaxOffset; Offset++)',
                    'for (int Offset : {-MaxOffset, -MaxOffset + 1, -int(Reach), -int(Reach) + 1, -100, -17, -16, -7, -2, -1, 0, 1, 2, 7, 16, 17, 100, int(Reach) - 1, int(Reach), MaxOffset - 1, MaxOffset})')
            if relative == 'early-exit.cpp':
                text = text.replace(
                    'for (unsigned IdxToFind = 0; IdxToFind < N; ++IdxToFind)',
                    'for (unsigned IdxToFind : {0u, 1u, 2u, 3u, 7u, 15u, 16u, 31u, 32u, 63u, 64u, 127u, 255u, N - 2u, N - 1u})')
                for index in (1, 2, 3):
                    text = text.replace(
                        f'for (unsigned Exit{index}Idx = 0; Exit{index}Idx <= TripCount; Exit{index}Idx += Stride{index})',
                        f'for (unsigned Exit{index}Idx : av_llvm::positions(TripCount, Stride{index}))')
            if relative == 'find-last.cpp':
                # Decrementing upstream kernels start at I=TC and read A[TC].
                # Keep kernel/trip counts; provide a deterministic guard element.
                text = text.replace('allocate<Ty>(N)', 'allocate<Ty>(N + 1)')
                text = text.replace('init_data(Src1, N);', 'init_data(Src1, N + 1);')
                text = text.replace('init_data(Src2, N);', 'init_data(Src2, N + 1);')
                text = text.replace('allocate<Ty>(SmallN)', 'allocate<Ty>(SmallN + 1)')
                text = text.replace('I != SmallN;', 'I != SmallN + 1;')
            if relative == 'fmax-reduction.cpp':
                # Two original strict reductions use a hardcoded 1025 bound.
                text = text.replace('unsigned N = 1024;', 'unsigned N = 1025;')
                # Sample harness positions, not kernel trip counts: exhaustive
                # hosted sweeps execute billions of iterations under simulation.
                text = text.replace(
                    'for (unsigned Idx = 0; Idx != N; ++Idx)',
                    'for (unsigned Idx : {0u, 1u, 3u, 15u, 16u, 31u, 32u, 63u, 64u, 511u, 512u, N - 1})')
                text = text.replace(
                    'for (unsigned Idx = 0; Idx != 64; ++Idx)',
                    'for (unsigned Idx : {0u, 1u, 15u, 31u, 63u})')
            if relative.endswith('outer-loop-vect.cpp'):
                # Original hosted main relies on main's implicit return 0.
                i = text.rfind('}')
                text = text[:i] + 'return 0;\n' + text[i:]
        name = relative.removesuffix('.cpp').replace('/', '-')
        wrapper = f'''
int main() {{
  av_begin("llvm/{name}");
  int result = llvm_test_main();
  av_end("llvm/{name}");
  if (result != 0) abort();
  printf("llvm/{name} checksum=%u observations=%u PASS\\n",
         av_llvm::checksum, av_llvm::observations);
  return 0;
}}
'''
        target = output / f'{name}.cpp'
        target.write_text('#include "av_llvm_support.h"\n' + text + wrapper)
        functions = (re.findall(r'BENCH\("[^"]+",\s*(example\w+)\(', text)
                     if relative == 'gcc-loops.cpp' else ['VectorFn'])
        records.append({'suite': 'llvm', 'name': name,
                        'sources': [str(target)], 'include_dirs': [str(output)],
                        'defines': [], 'language': 'c++', 'cflags': ['-fwrapv'],
                        'kernel_sources': [target.name],
                        'kernel_functions': list(dict.fromkeys(functions))})
    return records
