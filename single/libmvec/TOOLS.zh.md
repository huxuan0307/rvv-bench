# 数学库代码与工具使用索引

所有仓库命令从 rvv-bench 根目录执行。仓库代码是后续开发入口；外部 retained-libmvec-tools 是历史参考，不能当成已完成参数化的工具链。

## 选择入口

| 目的 | 入口 | 输入 | 输出与限制 |
|---|---|---|---|
| 构建七函数新算法库 | [math/build.py](math/build.py) | [math/](math/) 下源码，默认已验证 GCC 前缀 | math/build/libnewmath.a、对象、反汇编、属性、manifest；目标 rva23u64，目前无 LTO |
| 检查新库数值、特殊值及 Nx ABI | [tests/newmath/gate.py](tests/newmath/gate.py) | --new-archive、--gcc、--scalar-libm、--oracle-dir；实际运行另需 --nemu | --output 新目录中的 ELF/BIN、日志与 gate-results.json；--build-only 只构建。输出不能放 tasks |
| 审核已有正确性门禁产物 | [tests/newmath/audit_receipts.py](tests/newmath/audit_receipts.py) | 可重复 --gate-dir，--output | 产物身份与通过标志审计；不运行目标程序 |
| 合并原始测量生成周期汇总 | [analysis_newmath/cycles_summary.py](analysis_newmath/cycles_summary.py) | --input 四组来源的合并原始 CSV | --output 新 CSV；192行、10列、steady7 中位数；不输出加速比，不覆盖已有文件 |
| 对旧七函数批次做严格证据分析 | [analysis_newmath/analyze.py](analysis_newmath/analyze.py) | manifest位置参数，--input-root | --output 新目录；限定原 campaign/schema，不是任意批次通用分析器 |
| 构建已安装 libmvec 的基准 | [build.py](build.py) | --prefix、--case、--profile、--output；可选--math-archive/--external-sysroot/--binutils-prefix | 冻结测试源码、oracle、ELF/BIN及manifest；不是三库统一构建脚本的直接替代 |
| NEMU功能运行/gsim周期测量 | [run.py](run.py) | nemu/gsim 子命令，--manifest、--output | 隔离运行目录与 stdout/stderr；gsim命令会启动仿真 |
| 验收运行结果并导出样本 | [collect.py](collect.py) | --run、--require-complete | 完整性及退出状态验收，展开逐轮样本；不运行仿真 |
| 运行历史完整批次调度 | [node_campaign.py](node_campaign.py) | --manifest、--nemu-run、--output、--emu、--reference | 会启动ABI、七函数及复跑阶段；不用于直接启动当前log优化迭代 |
| 将任务代码移出归档树 | [archive_task_code.py](archive_task_code.py) | --tasks、--archive、--manifest | 默认只生成计划；--apply 实际移动文件，校验SHA；维护用途，不参与性能测试 |
| 生成纯 vfmacc 链标定内核 | [gen_pipe.py](gen_pipe.py) | --output、--chain | 覆盖 LMUL1/2/4/8 × 累加器1–32 的标定源码 pipe_calib.c；单文件生成，确定性输出 |
| 构建标定镜像 | [build_pipe.py](build_pipe.py) | --prefix、--output（带日期时间的新目录） | 裸机 ELF/BIN、反汇编、链接 trace 与 manifest；只做标定，不参与被测矩阵 |

完整原基准工具说明见 [README.zh.md](README.zh.md)，正确性命令见 [测试README](tests/newmath/README.zh.md)，汇总格式与边界见 [分析README](analysis_newmath/README.zh.md)。

## wrapper和测试源码

| 文件 | 职责 |
|---|---|
| [kernel-loop.c](kernel-loop.c) | 标量/自动向量化循环，编译为各模式；auto经编译器生成Nx调用 |
| [kernel-direct.c](kernel-direct.c) | 显式向量加载、Nx调用、存储；尾部标量处理 |
| [kernel-array.c](kernel-array.c) | 数组direct适配器，一次调用处理整个n |
| [perf.c](perf.c) | rdcycle计时、K次重复、轮次与模式轮转，计时后校验和打印 |
| [entry_rne.c](entry_rne.c) | 整个测试main外设置/恢复RNE，不逐批次切换舍入模式 |
| [tls_anchor.c](tls_anchor.c)、[tls-safe.ld](tls-safe.ld) | 裸机TLS空间与布局支持 |
| [overlay/bits/math-vector.h](overlay/bits/math-vector.h) | 编译器可见的Nx向量声明覆盖头 |
| [tests/newmath/nx_gate.c](tests/newmath/nx_gate.c)、[nx_probe.S](tests/newmath/nx_probe.S) | Nx数值/环境测试及寄存器保持、入站vl探针 |
| [tests/newmath/unit/](tests/newmath/unit/) | 各算法array数值、特殊值与环境测试，配合外部冻结MPFR数据 |

## 已知未完成事项

三库统一构建、array辅助构建、单独NEMU全批门禁、静态数学token审计和绘图脚本仍仅保留历史版本，尚未完成独立参数化。历史构建产物和部分输入源码已经清理，不应直接执行归档脚本尝试复现。其用途与缺失依赖逐项写在 retained-libmvec-tools/README.zh.md 中。

本次文档整理不代表重新验证了交叉构建、数值正确性或仿真性能；此前完成的工具测试与源码迁移结果另有记录。
