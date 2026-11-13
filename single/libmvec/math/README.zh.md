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

## log 波形定位与实验候选

数学库及性能构建均设置 `--param=gpr2vr-cost=0 --param=fpr2vr-cost=0`。显式 RVV intrinsic 的常量加数不一定能通过该成本设置消除广播；必须检查最终机器码，不能用参数存在推断 `.vf` 替换成功。

`--log-hot-lifetime` 启用实验性的热路径生命周期隔离：整数 special/near-one 分流后使用空 `+vr` 汇编约束切断旧输入活跃区间，约束本身不产生机器指令。当前 GCC 下它消除了 m2 Nx 入口为整寄存器复制生成的零 VL 配置。此选项尚非默认，最终性能需独立验收。

同 SHA `409f429572` 的 Verilator 波形确认原 m2 每次入口 `vsetivli zero,0,e64,m2` 触发零 VL 写回冲刷；候选对应计时窗口冲刷为零。短测是定位证据，不代替 steady7、同批 scalar 对照或完整数值/PCS 门禁。

具体运行记录和性能数值保存在任务目录，不在源码使用说明中维护。实验选项不表示已对任意配置证明性能收益。

可从已构建的 N16 log 镜像拆出六种实现的独立短波形镜像：

```sh
python3 -B single/libmvec/build_trace.py \
  --build single/libmvec/build/log-n16-lifetime \
  --output /absolute/new-trace-build
```

可用 `--mode auto_m2` 仅生成一种实现，`--k` 控制计时调用次数。短驱动只使用16个冻结输入、两次对应实现预热、一次计时区间及输出误差检查；复用原 kernel 对象和库，不执行全量功能前置循环。构建来源需保留，其命令和数据头仍是复现依赖。完整数值/ABI验收必须另跑。

`../fst_signals.c` 与 `../fst_events.c` 可分别导出 FST 层级和指定 handle 的变化事件。使用系统 Verilator 的 `gtkwave/fstapi.c`、`fastlz.c` 并链接 `-lz -llz4 -lm`。文件扩展名不决定波形格式。每个波形应重新提取 handle；ROB 提交事件需结合 `isCommit` 与 `commitValid`，且不能把 PC 保持、walk 或 flush 间隔误算为指令延迟。周期时间轴应与 guest rdcycle、顶层 timer 校准。

许可证及版权来源见 COPYING.LIB、LICENSES.md 和各源文件中的原注释。
