# RVV 七函数数学实现

这里是 rvv-bench 仓库内的正式算法工作代码，包含 log、exp、pow、sin、cos、logf、expf。每个函数提供 m1/m2 数组入口及标准 unmasked Nx 向量入口，两种接口共用寄存器数学核。接口声明见 newlib.h。

构建：

```sh
python3 -B single/libmvec/math/build.py
```

当前构建脚本默认使用已经验证的 GCC 工具链及 `-march=rva23u64`，输出在本目录 build/。算法 RNE-only，调用端需在计时区间外设置舍入模式；不要在每个向量批次内强制切换 frm。

源码来自已通过完整 MPFR512、特殊值、向量PCS和全量NEMU/gsim验证的 20261112-0126-newlib-full/source/newlib 快照。本目录建立时 C/头/表与该快照逐字节相同。任务快照及性能证据保留在仓库根目录 .dsh/tasks，用于历史追溯；后续实现修改应在本目录进行，不再在任务快照内开发。

公开Nx ABI保持不变。log现将任意special/near-one批次提前分流到遵守vector PCS的冷函数，普通表/FMA路径不再因scalar fallback而保存全部15个向量寄存器。分类发生在任何浮点运算之前；冷函数保留原计算与scalar lane顺序。性能收益须以实测为准。

log迭代构建使用 `--log-only --output NEWDIR`；`--baseline`恢复原inline冷路径，`--lto`生成LTO archive，`--prefix`显式选择工具链。LTO archive需用GCC驱动最终链接，不能将空的中间objdump当成最终机器码。独立门禁见 ../tests/newmath/README.zh.md。

N16完整模式性能入口：

```sh
python3 -B single/libmvec/build.py --case log --profile n16 \
  --math-archive /absolute/libnewmath.a --output /absolute/new-build
```

此入口使用rva23u64，保留scalar strict/fast、auto m1/m2、direct m1/m2与控制组；数学库显式链接在标量libm之前。性能构建及最终链接加 `--lto` 可比较跨单元优化。auto的m1/m2 TU保留非LTO目标码，因为当前GCC全LTO会合并其选项而退化为标量；其他性能TU、数学archive及最终链接使用LTO，报告必须明确这是混合LTO。公开Nx的noinline/noclone仍保持，不能将此选项称为调用已被内联消除。日志、manifest与最终反汇编在指定输出目录，不启动仿真。

许可证及版权来源见 COPYING.LIB、LICENSES.md 和各源文件中的原注释。
