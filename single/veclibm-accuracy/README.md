# Upstream veclibm accuracy tests on AM/NEMU

This directory builds **all 141 explicit upstream `TEST` declarations from 81
functional C++ sources plus `test_infra.cpp`** as independent bare-metal images.
It is not the performance microbenchmark, and the compatibility header is not a
full GoogleTest implementation. Only the APIs actually used here are supported:
`TEST`, `EXPECT_LT`, and `EXPECT_EQ`.

**131 tests contain upstream assertions; 10 are diagnostic/smoke-only.** Eight
special tests (`cdfnorminv`, `cosh`, `erfc`, `erfcinv`, `erfinv`, `lgamma`, `sinh`,
`tgamma`) only call the upstream special-value display helper. The `sincosI` and
`sincospiI` two-output stride helper computes and prints errors but upstream has
no `EXPECT` in that overload. These tests are executed and reported honestly as
`validation=diagnostic-only`, not silently upgraded to numerical assertions.
No new numerical threshold is invented for them. The final quick run executes
467 original assertions across the other 131 tests.

## Build and run

From the repository root:

```sh
make -C single/veclibm-accuracy             # calibrated quick sampling profile
make -C single/veclibm-accuracy run          # build + NEMU, unique evidence folder
make -C single/veclibm-accuracy CASE=exp.small_args
make -C single/veclibm-accuracy POINTS=16 GRID_POINTS=4 CONFIG=
make -C single/veclibm-accuracy full        # original point counts, build-full/
make -C single/veclibm-accuracy test
```

The default profile is `quick.json`, one cap per original `suite.test` identifier.
Explicit command-line `POINTS`/`GRID_POINTS` select uniform caps unless `CONFIG`
is also set. Both caps must be at least two. `CONFIG` is an optional JSON mapping
from exact case identifier to `{ "points": N, "grid_points": M }` (an optional
`repeats` knob exists for experiments, but every shipped quick case executes
exactly once). Overrides never raise an upstream sampling count: the effective
count is the minimum of the original count and the selected cap.

`build/manifest.json` uses a `cases` array with unique `id`/`name`, absolute `bin`
and `elf` paths, original source location/hash, selected point caps, build status,
image hash and a case-specific `require_marker`. `build/catalog.json` always
contains the complete 141-case catalog even when `CASE` selects one executable;
the manifest contains only the selected build set. Generated driver/infrastructure
sources and command/build/link logs remain under the build directory.

`full` uses a separate build tree and caps of 100000 (above or equal to every
upstream count), so it restores original interval/grid densities. Full execution
can be very expensive and is not the instruction-budgeted quick profile.

## Fidelity and necessary adaptations

* Functional test sources and input tables are compiled directly from upstream.
  The upstream checkout is never rewritten. Every original assertion expression
  and threshold is retained, including strict `<` comparisons.
* Only interval/grid point counts are capped in a generated infrastructure copy.
  Original interval/target loops, stride values, exceptional values and explicit
  input tables (including all 189 near-N*pi/2 arguments) remain intact. Interval
  generation retains the original formula with both endpoint indices.
* An existing unit-stride two-input grid defect is fixed in that generated copy:
  upstream writes `x[cnt++]` followed by `y[cnt]`, leaving `y[0]` uninitialized
  and writing `y[N]` out of bounds. Both arrays now use the same index before
  incrementing it. This does not change error criteria.
* The reference uses the toolchain's **real static libm and libc math helpers**,
  plus libgcc binary128 software arithmetic. Compile-time checks require a
  16-byte, 113-bit-mantissa `long double`. No approximate stubs are an oracle.
* With `-nostdinc++`, the C system math header lacks C++ overloads normally
  supplied by libstdc++. The small local math wrapper restores only used
  `cos(long double)`, `sin(long double)`, and `log2(long double)` overloads via
  `cosl`/`sinl`/`log2l` (and their float counterparts). Without this, the upstream
  `cospil` reference silently loses precision. No reference expression is edited.
* AM uses the existing autovec runtime, linker layout, vector/FPU initialization
  and single-hart TLS. A TLS alias supplies glibc's internal errno name without
  bringing in hosted startup.
* Upstream `%L` diagnostic printing is unsupported by AM klib and is suppressed.
  Every numerical assertion still runs in its original operand type. The adapter
  emits each LT assertion's double-formatted operands and every failing bitwise
  assertion; presentation narrowing never changes the comparison.
* A failed assertion increments the failure count; the independent executable
  returns nonzero. C assertions also emit an error and abort. Merely reaching
  GOOD TRAP without a complete marker is not sufficient for the shared runner.

Each successful image emits:

```text
VECLIBM_ACCURACY_COMPLETE case=exp.small_args checks=2 failed=0 body_instructions=...
```

Use the shared runner's **whole-program NEMU total guest instructions**, not the
body counter, for budget claims. The quick profile aims near ten million for
ordinary interval tests, while naturally small special-value/stride/fixed-table
cases stay below that target rather than padding work. Reduced sampling is not
a claim of exhaustive original-density accuracy coverage.
