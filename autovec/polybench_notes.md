# PolyBench/C integration

`prepare_polybench.py` adapts the 30 maintained PolyBench/C kernels from the supplied upstream checkout. It does not vendor or modify upstream files. Call `prepare(source: pathlib.Path, output: pathlib.Path)`; `source` must be the PolyBenchC checkout root containing `utilities/polybench.h`, and `output` receives generated standalone per-kernel directories. Each returned record follows the autovec interface:

```python
{
    "suite": "polybench",
    "name": "atax",
    "sources": ["/absolute/path/to/generated/atax/atax.c"],
    "include_dirs": ["/absolute/path/to/generated/atax"],
    "defines": ["SMALL_DATASET"],
}
```

Every generated case includes its upstream benchmark-specific parameter header and an adapted local `polybench.h` compatibility header. Kernel, initialization, and output routines remain from upstream. The generated translation unit wraps the original kernel call with `av_begin(name)` / `av_end(name)` without removing the call, marks the kernel function `noinline` for report attribution, and runs the upstream live-out dump unconditionally after the timed region. The compatibility header matches PolyBench's default parametric loop-bound choice (`POLYBENCH_LOOP_BOUND(x, y)` expands to `y`). That dump is deterministic for a fixed build and input, keeps computed results observable, and produces checksum-like output without a separate kernel-specific checksum implementation. Generated benchmark headers use `%.9g` for float and `%.17g` for double live-out values (integer format remains `%d`) so scalar/vector output comparisons are not obscured by PolyBench's default two-decimal rounding. The runtime's `printf` and `fprintf` shims should capture it; `polybench.h` directs dumps to `stdout`.

## Parent runtime/build assumptions

- Provide `av_runtime.h` on the compiler include path. It must declare `av_begin` and `av_end` and include/provide the runtime-compatible standard I/O, allocation, string, and math interfaces.
- Runtime standard I/O shims include `printf` macro redirection and an `fprintf` shim. PolyBench dump functions use both.
- Link the toolchain static `libm` and `libgcc` for kernels using math functions. Runtime confirms exact upstream `sqrt`/`exp`/`pow` support and will provide `__errno_location`.
- Provide normal `malloc`/`free`; kernels allocate arrays through compatibility macros. `memcpy`/`memset` may be needed by compiler/runtime support.
- Compile as C99 or later because upstream uses variable-length array parameter declarations. Define `SMALL_DATASET` (also present in the case record) and do not define another dataset selector.
- Each generated C file is a complete executable with `main` returning 0. Parent controls compilation, linking, execution, and report parsing.

The source set deliberately uses maintained `medley/nussinov/nussinov.c`, not legacy `Nussinov.orig.c`, and excludes PolyBench utilities, which are replaced by the local compatibility header and runtime instrumentation.
