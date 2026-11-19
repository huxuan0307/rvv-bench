# ARM SVE 派生的 RVV 实验候选

四个 binary64 函数 `atan`、`atan2`、`acos`、`log10` 按函数归档：`kernels/<函数>/arm.c` 是算法，`entries/<函数>/arm_m{1,2}.c` 是 ABI 适配层。来源为 rvv-bench `refactor/libmvec-layout` 的 `4f5f5f93bafbc7f6781e37037eb64c60f9e4272b`。仅提取算法和适配层，移除内嵌 `CAND_SELFTEST`，其余源代码 token 不变；上游 glibc 文件及版权见各核的文件头，许可见 [COPYING.LIB](../oldarchive/COPYING.LIB)。

各函数提供 m1/m2、定长/Nx、有掩码/无掩码八个 `_ZGV` 定义。`atan2` 的参数依次为 `y,x`，带掩码入口在整数域清理 inactive 输入；适配器进入 RNE 并恢复调用者的 `frm`。这些是源码行为，不是完整 ABI 或异常标志验收。特别是 [atan2](kernels/atan2/arm.c#L33-L36) 的有效零对输入仍可能在通用路径上执行 `0/0` 并置 INVALID，尽管返回位随后被修正。此处保留历史算法用于显式对照，不作为生产库或默认基线。

## 构建与接入

工具需要 Python 3.10+ 及支持 RVV 1.0、`-mrvv-max-lmul` 的 RV64 GCC，同目录需有对应的 `ar`、`gcc-nm`、`readelf`。从 rvv-bench 根目录执行；`OUT` 是全新输出目录：

```sh
python3 -B single/libmvec/script/migration/build_archive.py \
  --variant arm --gcc "$RV64_GCC" --output "$OUT"
```

只编译原生对象，不启动模拟器，不安装或修改任何库。可用 `--functions` 限定函数。构建记录包含源码及许可快照、工具/依赖哈希、编译命令、归档成员和导出符号；完整四函数构建须有八个对象和 32 个带 `[VARIANT_CC]` 的公开符号，无 `main()`。`--help` 不写文件。

以 `OUT/libmvec-arm.a` 显式传给[配对构建器](../latency/README.zh.md)或[吞吐构建器](../throughput/README.md)的 `--math-archive`，以及[精度框架](../tests/README.md)的 `--archive`（四函数为 `--nx-only`，标量库另给）。三套测试的 `main()` 仍在各自框架中。交叉链接不等于运行验收；精度、掩码副作用、完整 ABI、消费者 LTO 和 RTL 性能必须另行取证。
