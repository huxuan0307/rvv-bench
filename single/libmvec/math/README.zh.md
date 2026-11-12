# RVV 七函数数学实现

这里是 rvv-bench 仓库内的正式算法工作代码，包含 log、exp、pow、sin、cos、logf、expf。每个函数提供 m1/m2 数组入口及标准 unmasked Nx 向量入口，两种接口共用寄存器数学核。接口声明见 newlib.h。

构建：

```sh
python3 -B single/libmvec/math/build.py
```

当前构建脚本默认使用已经验证的 GCC 工具链及 `-march=rva23u64`，输出在本目录 build/。算法 RNE-only，调用端需在计时区间外设置舍入模式；不要在每个向量批次内强制切换 frm。

源码来自已通过完整 MPFR512、特殊值、向量PCS和全量NEMU/gsim验证的 20261112-0126-newlib-full/source/newlib 快照。本目录建立时 C/头/表与该快照逐字节相同。任务快照及性能证据保留在仓库根目录 .dsh/tasks，用于历史追溯；后续实现修改应在本目录进行，不再在任务快照内开发。

公开Nx ABI保持不变。当前log在Nx接口下慢于标量，数组接口有小幅收益；下一阶段分析调用/寄存器开销及LTO等优化，使用N16简化性能集迭代，并维持独立正确性门禁。

许可证及版权来源见 COPYING.LIB、LICENSES.md 和各源文件中的原注释。
