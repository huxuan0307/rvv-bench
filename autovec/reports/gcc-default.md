# GCC 默认策略 RVV 自动向量化报告

编译器：`riscv64-unknown-linux-gnu-gcc (g6afcc4f6d) 16.1.0`。

基线：`-O3 -march=rv64gcv -mabi=lp64d -mcmodel=medany -ffreestanding -fno-builtin`。未设置 fast-math、向量化成本模型、LMUL、VLEN 或强制向量化选项。运行时单独关闭向量化，其代码不参与 benchmark 成功位点统计。

## 总体结果

| 测试组 | 可执行文件 | 构建成功 | 裸机运行通过 | GCC 成功位点 |
| --- | ---: | ---: | ---: | ---: |
| tsvc | 1 | 1 | 1 | 109 |
| polybench | 30 | 30 | 30 | 85 |
| llvm | 10 | 10 | 10 | 160 |

标量参考比对：41 项精确匹配，0 项在数值容差内，0 项不匹配或缺失。详见 comparison.json。

## 统计口径与限制

- 成功位点：GCC `-fopt-info-vec-all` 中 loop vectorized / basic block part vectorized 的源码位置与消息去重，包含初始化、检查代码；不等价于内核覆盖率。
- missed 位点只计 couldn't vectorize loop，GCC 同一循环有多次分析与变换，成功和 missed 可同时出现，不能据此直接计算失败率。
- 内核 RVV 数量为反汇编中的静态指令数，含 vset 指令，不能解释为运行时执行次数或加速比。TSVC 使用 sNNN/v* 函数，PolyBench 使用 kernel_* 函数；LLVM 的混合 harness 统计是筛选后的近似值，精确归因见逐函数 JSON。
- TSVC：LEN_1D=320、LEN_2D=32、iterations=32；保留独立 dummy 翻译单元且不启用 LTO，全部原始主程序入口均执行。宿主时间调用换为 cycle 读取。
- PolyBench 使用 SMALL_DATASET；LLVM 保留可移植标量循环与检查，适配范围和排除原因见移植说明。
- NEMU 验证裸机启动和程序成功退出；各向量版本另外与关闭 loop/SLP vectorization 的同编译器构建逐项比较。PolyBench 比较所有 live-out 值，TSVC 比较原始 checksum，LLVM 用例比较上游驱动 checksum。模拟器周期不代表硬件性能。
- 没有开展 LLVM 编译器实验；LLVM test-suite 在这里是由 GCC 编译的测试源码集合。

## 逐项结果

| 测试组 | 用例 | 构建 | 运行 | 成功位点 | missed 位点 | 内核静态 RVV |
| --- | --- | --- | --- | ---: | ---: | ---: |
| tsvc | tsvc | built | passed | 109 | 242 | 1484 |
| polybench | correlation | built | passed | 1 | 12 | 0 |
| polybench | covariance | built | passed | 2 | 9 | 5 |
| polybench | gemm | built | passed | 5 | 7 | 15 |
| polybench | gemver | built | passed | 2 | 8 | 15 |
| polybench | gesummv | built | passed | 1 | 4 | 0 |
| polybench | symm | built | passed | 3 | 7 | 32 |
| polybench | syr2k | built | passed | 4 | 6 | 20 |
| polybench | syrk | built | passed | 4 | 6 | 15 |
| polybench | trmm | built | passed | 1 | 7 | 0 |
| polybench | 2mm | built | passed | 4 | 12 | 0 |
| polybench | 3mm | built | passed | 4 | 15 | 0 |
| polybench | atax | built | passed | 3 | 5 | 10 |
| polybench | bicg | built | passed | 3 | 5 | 3 |
| polybench | doitgen | built | passed | 2 | 12 | 34 |
| polybench | mvt | built | passed | 0 | 8 | 0 |
| polybench | cholesky | built | passed | 4 | 11 | 0 |
| polybench | durbin | built | passed | 4 | 2 | 22 |
| polybench | gramschmidt | built | passed | 2 | 11 | 0 |
| polybench | lu | built | passed | 4 | 12 | 0 |
| polybench | ludcmp | built | passed | 7 | 13 | 20 |
| polybench | trisolv | built | passed | 1 | 4 | 0 |
| polybench | deriche | built | passed | 3 | 13 | 10 |
| polybench | floyd_warshall | built | passed | 2 | 5 | 8 |
| polybench | nussinov | built | passed | 2 | 6 | 0 |
| polybench | adi | built | passed | 1 | 10 | 0 |
| polybench | fdtd_2d | built | passed | 6 | 11 | 34 |
| polybench | heat_3d | built | passed | 3 | 10 | 327 |
| polybench | jacobi_1d | built | passed | 3 | 2 | 18 |
| polybench | jacobi_2d | built | passed | 3 | 6 | 26 |
| polybench | seidel_2d | built | passed | 1 | 6 | 0 |
| llvm | gcc-loops | built | passed | 22 | 10 | 224 |
| llvm | any-of | built | passed | 16 | 5 | 392 |
| llvm | argmin-argmax-int | built | passed | 9 | 48 | 2260 |
| llvm | argmin-argmax-fp | built | passed | 9 | 27 | 1823 |
| llvm | early-exit | built | passed | 26 | 12 | 1934 |
| llvm | find-last | built | passed | 25 | 6 | 753 |
| llvm | fmax-reduction | built | passed | 20 | 24 | 318 |
| llvm | recurrences | built | passed | 1 | 5 | 21 |
| llvm | runtime-checks | built | passed | 29 | 18 | 1428 |
| llvm | VPlanNativePath-outer-loop-vect | built | passed | 3 | 6 | 37 |

## 复现与原始证据

在 rvv-bench 执行 `make -C autovec all run report`。各用例目录保存 commands.json、GCC .vect 文本及 tree-vect dump、链接日志、反汇编、ELF、BIN 和 NEMU run.log；build/O3 中保存编译器默认优化器与目标选项、构建清单及运行状态。报告旁的 CSV 用于汇总，JSON 保存去重位点和逐函数 RVV 指令计数。
