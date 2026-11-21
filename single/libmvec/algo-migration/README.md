# 实验候选：ARM SVE 派生与 glibc 标量派生

两类候选按同一布局归档：`kernels/<函数>/<来源>.c` 是算法，`entries/<函数>/<来源>_m{1,2}.c` 是 ABI 适配层，`kernels/common/` 放跨函数共享的表与入口模板。来源名只有两种：

| 来源 | 函数 | 算法出处 | 许可 |
|---|---|---|---|
| `arm` | `atan`、`atan2`、`acos`、`log10` | glibc `sysdeps/aarch64/fpu/sve/`，取自 rvv-bench `refactor/libmvec-layout` 的 `4f5f5f93bafbc7f6781e37037eb64c60f9e4272b` | LGPL-2.1-or-later |
| `glibc` | `sin`、`cos`、`exp`、`log`、`pow`、`sinf`、`cosf`、`expf`、`logf`、`powf` | glibc 标量 `sysdeps/ieee754/dbl-64/`、`flt-32/` 算法的 RVV 改写；原为 rvv-bench `e38a9d5` 的 `math/` 目录，2026-11-19 `be38208` 改名后迁入本布局 | LGPL-2.1-or-later |

许可见 [COPYING.LIB](COPYING.LIB) 与 [LICENSES.md](LICENSES.md)；每个核的文件头保留上游版权与来源。共享依赖在 `kernels/common/`：`newlib.h`、`scalar_tables.h`、`trigvec_data.c/.h`、`trigvec_impl.h`、`trigfvec_impl.h`、`sincosfvec_data.c`、`expfvec_data.c`、`f32_m8_shell.inc.h`、`unary_entries.inc.h`。`sin`/`cos`/`sinf`/`cosf` 的算法由共享的 `trigvec_impl.h`、`trigfvec_impl.h` 参数化，因此这四个函数没有各自的 `kernels/<函数>/glibc.c`，其入口直接包含共享核。

## 两类的接口差异

- `arm` 四函数：m1/m2、定长/Nx、有掩码/无掩码共八个 `_ZGV` 定义；`atan2` 参数依次为 `y,x`，带掩码入口在整数域清理 inactive 输入，适配器进入 RNE 并恢复调用者的 `frm`。`atan2` 的有效零对输入仍可能在通用路径执行 `0/0` 并置 INVALID，尽管返回位随后被修正。
- `glibc` 十函数：m1/m2、定长/Nx 的**无掩码** `_ZGV` 定义，另有旧 array 入口（如 `expvec_m1`）。**没有掩码入口**，也没有 `_len` 私有入口。`kernels/<函数>/glibc_lmul48.c` 是 m4/m8 实验材料，默认不参与构建。

以上都是源码行为，不是完整 ABI、异常标志或数值验收。

## 构建与接入

工具需要 Python 3.10+ 及支持 RVV 1.0、`-mrvv-max-lmul` 的 RV64 GCC，同目录需有对应的 `ar`、`gcc-nm`、`readelf`。从 rvv-bench 根目录执行；`OUT` 是全新输出目录：

```sh
python3 -B single/libmvec/script/migration/build_archive.py \
  --variant arm --gcc "$RV64_GCC" --output "$OUT"
```

只编译原生对象，不启动模拟器，不安装或修改任何库。可用 `--functions` 限定函数。构建记录包含源码及许可快照、工具/依赖哈希、编译命令、归档成员和导出符号；完整 `arm` 四函数构建须有八个对象和 32 个带 `[VARIANT_CC]` 的公开符号，无 `main()`。`--help` 不写文件。

`glibc` 来源的候选仍用旧的归档构建器（`script/build_oldarchive.py`，`--source` 指向本目录）生成 `libnewmath.a`；它按函数与变体选择编译单元，产物可显式交给配对/吞吐构建器和精度框架。以 `OUT/libmvec-arm.a` 或 `OUT/libnewmath.a` 显式传给[配对构建器](../latency/README.zh.md)或[吞吐构建器](../throughput/README.md)的 `--math-archive`，以及[精度框架](../tests/README.md)的 `--archive`（`arm` 四函数为 `--nx-only`，标量库另给）。三套测试的 `main()` 仍在各自框架中。交叉链接不等于运行验收；精度、掩码副作用、完整 ABI、消费者 LTO 和 RTL 性能必须另行取证。
