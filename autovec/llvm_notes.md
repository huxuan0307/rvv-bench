# LLVM Vectorizer bare-metal integration

`prepare_llvm.prepare(source: pathlib.Path, output: pathlib.Path) -> list[dict]`
reads the external LLVM test-suite tree and writes adapted translation units and
harness headers beneath `output/llvm`. The caller must supply an ignored output
directory. No upstream source is vendored into tracked files. Returned sources
and include directories are absolute paths; each of the ten independent records
uses suite `llvm`, language `c++`, empty defines, and `cflags: ['-fwrapv']`.
`source` can name the Vectorizer directory, the llvm-test-suite checkout, or its
containing directory. Missing expected candidates cause an error.

The source inspected was
`/nfs/home/huxuan/repos/compiler-experiment/benchmarks/llvm-test-suite/SingleSource/UnitTests/Vectorizer`.
These are scalar C++ loops, even where upstream names a function `VectorFn`:
there are no explicit vector types or architecture intrinsics in these sources.

## Complete source candidate catalog

| Candidate, relative to Vectorizer | Decision and adaptation |
| --- | --- |
| `gcc-loops.cpp` | Included, priority case. Retains all original kernel definitions and the 17 active upstream BENCH calls. Removes hosted Timer/gettimeofday, vector result storage, iostream and numeric dependencies. Reports per-example output digests and a final digest. |
| `any-of.cpp` | Included. Retains all integer/floating comparisons and live-out reduction cases, all upstream correctness input patterns. Replaces hosted allocation/callback/random/printing helpers. |
| `argmin-argmax-int.cpp` | Included. Retains all macro-generated integer kernels, type combinations, predicates, starts/strides, truncation, and explicit VF/interleave cases. Retains trip counts and boundary/duplicate-index input patterns. |
| `argmin-argmax-fp.cpp` | Included. Retains comparison and fmin/fmax kernels, float/double types and index variants; requires IEEE math without fast-math. |
| `early-exit.cpp` | Included. Retains all scalar-loop kernels and correctness case classes, including multiple exits, stores, live-outs, odd and small trip counts; harness exit positions are sampled. Callable wrappers preserve captured initialization lambdas. |
| `find-last.cpp` | Included. Retains all kernels, type/trip-count combinations and boundary cases. Adds initialized guard elements because upstream decrementing loops start at index TC, one past its original allocation. |
| `fmax-reduction.cpp` | Included. Retains all 13 actual kernels including the two hardcoded 1025-iteration loops. Allocates and initializes 1025 elements instead of upstream's 1024. Samples harness index positions while preserving NaN, infinity, denormal and signed-zero test classes. |
| `recurrences.cpp` | Included. Retains all three first/second/third-order recurrence kernels and 1000-element correctness checks. |
| `runtime-checks.cpp` | Included. Retains all memory-overlap, nested-loop and stencil kernels, original element/stride types and trip counts. Every checked array contributes to observable checksum. |
| `VPlanNativePath/outer-loop-vect.cpp` | Included. Retains matrix multiplication, auxiliary induction, indirect accesses and triple loop nest. Original main's implicit zero return becomes explicit in renamed entry. The largest two arrays occupy about 3 MB together. GCC compilation does not reproduce LLVM's VPlan-native vectorization mode; the scalar loops remain valid auto-vectorizer inputs. |

There are **no excluded .cpp candidates** in the inspected tree. No candidate
requires an ISA-specific intrinsic, explicit-vector extension, OS API in its
kernel, or unsupported platform language feature.

The only other source files are `common.h` and `argmin-argmax-common.h`: both
are included support headers, adapted in generated output rather than standalone
executables. `CMakeLists.txt` and `VPlanNativePath/CMakeLists.txt` are excluded as
hosted LLVM build descriptions. Every `*.reference_output*` file is excluded as
hosted expected stdout data, not a kernel; this includes the orphan
`index-select.reference_output`, which has no source candidate in this checkout.
The reference text is not a golden checksum for the adapted harness.

Within gcc-loops, `example5`, `example13` and `example14` are defined but never
called by upstream main; `example21` has a commented-out BENCH. They remain
source definitions but are **not counted as executed coverage**. No replacement
workloads or invented pointer-matrix input drivers are introduced. Executed
functions are example1, example2a/b, example3, example4a/b/c, example7, example8,
example9, example10a/b, example11, example12, example23, example24, example25.

## Harness and runtime contract

Build with GCC g++ C++17, `-nostdinc++ -ffreestanding -fno-builtin -fwrapv
-fno-exceptions -fno-rtti -fno-threadsafe-statics`, normal target/optimization
flags, and the parent runtime include directory. Link with the C driver and the
parent bare-metal runtime; no libstdc++, C++ exception library, global new/delete,
RTTI, or C++ startup support is required. `-nostdinc++` is essential because the
parent runtime's C math/stdlib headers otherwise resolve to hosted C++ forwarding
headers. Builtin fmin/fmax may require libm's float and double symbols.
Do not enable fast-math: fmax checks deliberately test NaNs and signed zeros.
`-fwrapv` defines signed arithmetic used by original boundary/random integer
inputs. It is supplied as record metadata for the parent builder.

The runtime must expose `av_begin(const char *)`, `av_end(const char *)`, printf
with `%s`, `%u`, `%ld`, malloc/free, abort, and any compiler-emitted memory helpers.
Only integer output formats are required, even for floating diagnostics. Float
failure diagnostics print a type marker and contribute the value to the digest.
The math helpers use compiler NaN/signbit/infinity builtins and fmin/fmax. The
runtime C math `isnan`/`signbit` macros are undefined in generated support to
avoid corrupting qualified compatibility helper declarations.

Generated compatibility helpers cover only facilities actually used by the
upstream harness: array ownership with malloc/free, numeric limits, deterministic
random filling, sort/reverse/fill/equal/reduce, small index vectors, pairs/arrays,
initializer lists, trivial captured callbacks, and printf-backed diagnostics.
The callback storage is bounded and statically checks trivial copyability and
capture size. It cannot silently accept an arbitrary future nontrivial callback.
No hosted STL implementation is linked. The fixed-seed LCG replaces Mersenne
Twister; floating random values are bounded finite multiples of 1/32 instead of
host-dependent full-range distributions. Upstream explicit extrema, NaNs,
denormals and duplicate-target cases remain. This changes random test data and
stdout, not the tested scalar-loop algorithms.

Clang's original loop pragmas remain in source. GCC ignores those pragmas;
the adapter additionally marks `ScalarFn` lambdas with GCC's
`optimize("no-tree-vectorize,no-tree-slp-vectorize")` attribute to keep the
correctness oracle scalar. The remaining vector candidates inherit parent flags.
Upstream optnone call barriers become noinline for GCC portability.

Each main returns zero on success and aborts on any upstream miscompare or
allocation failure. It prints `llvm/NAME checksum=... observations=... PASS`.
Scalar return values and checked output arrays feed an unsigned digest; NaNs
are canonicalized and zero signs ignored for floating digest values. These
observations prevent loops from vanishing as unused computations. Checksums are
intended for scalar-versus-vector builds of the same target/ABI, not cross-endian
or cross-word-size comparisons.

GCC examples use Mi=1 rather than the hosted 2^18 multiplier, retaining the warmup
and every BENCH invocation with at least one measured iteration (including
example8's Mi/4 case). Examples4b/4c hash their global a/b outputs instead of unchanged ia/ib
arguments. Example23 hashes its output ua instead of unchanged input
usa; example24 hashes ic rather than constant zero. The original kernel code and
array dimensions are retained. Whole-suite timing includes initialization and
correctness checks; GCC examples additionally emit individual av_begin/end pairs.

For fmax, only harness position enumeration is sampled: full-N positions become
{0,1,3,15,16,31,32,63,64,511,512,N-1}, and 64-position zero/infinity loops become
{0,1,15,31,63}. Offset sweeps and all original kernels remain. Timing measures a
correctness workload, not upstream hosted benchmark seconds.

Early-exit single-exit positions are sampled at 0,1,2,3,7,15,16,31,32,63,64,
127,255,N-2,N-1. Multiple-exit grids use the Cartesian product of deduplicated
{0,1,2,3,7,15,16,TC/2,TC-1,TC}, rounded down to the original stride; the original
no-exit TC case is preserved whenever it belonged to the original grid.
Runtime-checks samples {-100,-99,-33,-17,-16,-7,-2,-1,0,1,2,7,16,17,33,99,100}
instead of all 201 ordinary overlap offsets. Nested offsets include +/-2*(inner+1),
+/-(inner+1), +/-inner, +/-1 and zero. Stencil offsets include +/-MaxOffset and
adjacent values, +/-Reach and adjacent values, +/-100, +/-17, +/-16, +/-7,
+/-2, +/-1 and zero. These changes reduce simulation time while preserving all
kernel/type/stride combinations, alias/nonalias boundaries and dependence signs.

## Validation

The generated ten translation units compile with native GCC g++ at O3 using the
freestanding/no-STL flags above and the real parent runtime header, plus a small
native printf/timing implementation. All candidates execute original correctness
checks and print a deterministic PASS checksum. All ten native O3 executables
have identical full stdout and checksums when compared with builds using
`-fno-tree-vectorize -fno-tree-slp-vectorize` (final sampled early-exit/runtime
and repaired GCC digest rechecks also pass with `-fno-tree-vectorize`).
The final native GCC digest is 2885533902; fmax is 1278167175; sampled early-exit
is 4206169694; sampled runtime-checks is 2528620478. Target build/run validation
belongs to the parent integration; read its manifests for RISC-V execution
results. Native validation alone does not establish RISC-V vectorization.
