"""Generate standalone bare-metal PolyBench/C cases from an upstream checkout."""

from __future__ import annotations

import re
import shutil
from pathlib import Path


# Keep these explicit: the legacy Nussinov.orig.c is not the maintained kernel.
KERNELS = (
    "datamining/correlation",
    "datamining/covariance",
    "linear-algebra/blas/gemm",
    "linear-algebra/blas/gemver",
    "linear-algebra/blas/gesummv",
    "linear-algebra/blas/symm",
    "linear-algebra/blas/syr2k",
    "linear-algebra/blas/syrk",
    "linear-algebra/blas/trmm",
    "linear-algebra/kernels/2mm",
    "linear-algebra/kernels/3mm",
    "linear-algebra/kernels/atax",
    "linear-algebra/kernels/bicg",
    "linear-algebra/kernels/doitgen",
    "linear-algebra/kernels/mvt",
    "linear-algebra/solvers/cholesky",
    "linear-algebra/solvers/durbin",
    "linear-algebra/solvers/gramschmidt",
    "linear-algebra/solvers/lu",
    "linear-algebra/solvers/ludcmp",
    "linear-algebra/solvers/trisolv",
    "medley/deriche",
    "medley/floyd-warshall",
    "medley/nussinov",
    "stencils/adi",
    "stencils/fdtd-2d",
    "stencils/heat-3d",
    "stencils/jacobi-1d",
    "stencils/jacobi-2d",
    "stencils/seidel-2d",
)

INSTRUMENTATION = re.compile(
    r"^([ \t]*)polybench_start_instruments;[ \t]*$\n"
    r"(?P<body>.*?)"
    r"^([ \t]*)polybench_stop_instruments;[ \t]*$\n"
    r"^[ \t]*polybench_print_instruments;[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
KERNEL_FUNCTION = re.compile(r"^(static\s+)?void\s+(kernel_[A-Za-z0-9_]+)\s*\(", re.MULTILINE)



def _adapt_source(source: str, name: str) -> str:
    """Replace PolyBench timing and legacy DCE gating, preserving kernel code."""
    kernel_match = KERNEL_FUNCTION.search(source)
    if kernel_match is None:
        raise ValueError("upstream source has no kernel function")
    kernel_name = kernel_match.group(2)
    if len(KERNEL_FUNCTION.findall(source)) != 1:
        raise ValueError("expected exactly one kernel function")

    def replace_timing(match: re.Match[str]) -> str:
        body = match.group("body")
        call = re.search(rf"\b{re.escape(kernel_name)}\s*\(", body)
        if call is None:
            raise ValueError(f"instrumented block does not call {kernel_name}")
        return (f'{match.group(1)}av_begin("{name}");\n'
                f'{body}'
                f'{match.group(3)}av_end("{name}");\n'
                f'{match.group(3)}(void)0;')

    source, timing_replacements = INSTRUMENTATION.subn(replace_timing, source)
    if timing_replacements != 1:
        raise ValueError(f"expected one instrumentation block, found {timing_replacements}")
    if not re.search(rf"\b{re.escape(kernel_name)}\s*\(", source):
        raise ValueError(f"adaptation removed kernel call {kernel_name}")
    source = KERNEL_FUNCTION.sub(r"\1__attribute__((noinline)) void \2(", source, count=1)

    # The default PolyBench DCE macro hides output behind argc/argv. Emit the
    # upstream dump unconditionally after timing, which also makes results
    # observable to the bare-metal runtime.
    source, dce_replacements = re.subn(
        r"polybench_prevent_dce\((print_array\(.*?\))\);",
        r"\1;",
        source,
        flags=re.DOTALL,
    )
    if dce_replacements != 1:
        raise ValueError(f"expected one live-out dump, found {dce_replacements}")

    source = re.sub(r"^#include <unistd\.h>\s*\n", "", source, flags=re.MULTILINE)
    source = re.sub(r"^#include <polybench\.h>\s*$", '#include "polybench.h"', source, flags=re.MULTILINE)
    source = source.replace('#include "polybench.h"', '#include "av_runtime.h"\n#include "polybench.h"', 1)
    return source


def _polybench_header() -> str:
    """Small compatibility header for upstream PolyBench source and headers."""
    return r'''#ifndef AUTOVEC_POLYBENCH_H
#define AUTOVEC_POLYBENCH_H
#include "av_runtime.h"
#define POLYBENCH_LOOP_BOUND(x, y) (y)
#define POLYBENCH_RESTRICT
#define POLYBENCH_C99_SELECT(x, y) (x)
#define POLYBENCH_PADDING_FACTOR 0
#define POLYBENCH_ARRAY(x) (x)
#define POLYBENCH_FREE_ARRAY(x) free((void *)(x))
#define POLYBENCH_DECL_VAR(x) (*x)
#define POLYBENCH_1D(var,d1,dd1) var[d1]
#define POLYBENCH_2D(var,d1,d2,dd1,dd2) var[d1][d2]
#define POLYBENCH_3D(var,d1,d2,d3,dd1,dd2,dd3) var[d1][d2][d3]
#define POLYBENCH_4D(var,d1,d2,d3,d4,dd1,dd2,dd3,dd4) var[d1][d2][d3][d4]
#define POLYBENCH_5D(var,d1,d2,d3,d4,d5,dd1,dd2,dd3,dd4,dd5) var[d1][d2][d3][d4][d5]
#define POLYBENCH_1D_F(var,d1,dd1) var[d1]
#define POLYBENCH_2D_F(var,d1,d2,dd1,dd2) var[d1][d2]
#define POLYBENCH_3D_F(var,d1,d2,d3,dd1,dd2,dd3) var[d1][d2][d3]
#define POLYBENCH_4D_F(var,d1,d2,d3,d4,dd1,dd2,dd3,dd4) var[d1][d2][d3][d4]
#define POLYBENCH_5D_F(var,d1,d2,d3,d4,d5,dd1,dd2,dd3,dd4,dd5) var[d1][d2][d3][d4][d5]
#define POLYBENCH_1D_ARRAY_DECL(v,t,d,dd) t *v = malloc(sizeof(t) * (d))
#define POLYBENCH_2D_ARRAY_DECL(v,t,d1,d2,dd1,dd2) t (*v)[d2] = malloc(sizeof(t) * (d1) * (d2))
#define POLYBENCH_3D_ARRAY_DECL(v,t,d1,d2,d3,dd1,dd2,dd3) t (*v)[d2][d3] = malloc(sizeof(t) * (d1) * (d2) * (d3))
#define POLYBENCH_4D_ARRAY_DECL(v,t,d1,d2,d3,d4,dd1,dd2,dd3,dd4) t (*v)[d2][d3][d4] = malloc(sizeof(t) * (d1) * (d2) * (d3) * (d4))
#define POLYBENCH_5D_ARRAY_DECL(v,t,d1,d2,d3,d4,d5,dd1,dd2,dd3,dd4,dd5) t (*v)[d2][d3][d4][d5] = malloc(sizeof(t) * (d1) * (d2) * (d3) * (d4) * (d5))
#define POLYBENCH_DUMP_TARGET stdout
#define POLYBENCH_DUMP_START fprintf(POLYBENCH_DUMP_TARGET, "==BEGIN DUMP_ARRAYS==\n")
#define POLYBENCH_DUMP_FINISH fprintf(POLYBENCH_DUMP_TARGET, "==END   DUMP_ARRAYS==\n")
#define POLYBENCH_DUMP_BEGIN(s) fprintf(POLYBENCH_DUMP_TARGET, "begin dump: %s", s)
#define POLYBENCH_DUMP_END(s) fprintf(POLYBENCH_DUMP_TARGET, "\nend   dump: %s\n", s)
#endif
'''


def prepare(source: Path, output: Path) -> list[dict]:
    """Adapt all 30 PolyBenchC kernels into output and return case records.

    ``source`` is the PolyBenchC checkout root. ``output`` is a caller-owned
    generated-source directory; no upstream files are copied into this repo.
    """
    source = Path(source).resolve()
    output = Path(output).resolve()
    if not (source / "utilities" / "polybench.h").is_file():
        raise FileNotFoundError(f"not a PolyBenchC checkout: {source}")

    output.mkdir(parents=True, exist_ok=True)
    cases: list[dict] = []
    for rel_dir in KERNELS:
        kernel_dir = source / rel_dir
        c_candidates = [path for path in kernel_dir.glob("*.c") if path.stem.lower() == kernel_dir.name.lower()]
        if len(c_candidates) != 1:
            raise ValueError(f"expected one matching kernel source in {kernel_dir}, got {len(c_candidates)}")
        c_path = c_candidates[0]
        name = c_path.stem.replace("-", "_").lower()
        case_dir = output / name
        case_dir.mkdir(parents=True, exist_ok=True)

        header_src = kernel_dir / f"{c_path.stem}.h"
        if not header_src.is_file():
            raise FileNotFoundError(header_src)
        header_dst = case_dir / header_src.name
        header_text = header_src.read_text(encoding="utf-8")
        header_text = header_text.replace('"%0.2f "', '"%.9g "')
        header_text = header_text.replace('"%0.2lf "', '"%.17g "')
        header_dst.write_text(header_text, encoding="utf-8")

        adapted = _adapt_source(c_path.read_text(encoding="utf-8"), name)
        c_dst = case_dir / c_path.name
        c_dst.write_text(adapted, encoding="utf-8")
        compat = case_dir / "polybench.h"
        compat.write_text(_polybench_header(), encoding="utf-8")

        cases.append({
            "suite": "polybench",
            "name": name,
            "sources": [str(c_dst.resolve())],
            "include_dirs": [str(case_dir.resolve())],
            "defines": ["SMALL_DATASET"],
        })
    return cases
