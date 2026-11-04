# GCC 默认策略 RVV 自动向量化报告

编译器：`riscv64-unknown-linux-gnu-gcc (g6afcc4f6d) 16.1.0`。

基线：`-O3 -march=rv64gcv -mabi=lp64d -mcmodel=medany -ffreestanding -fno-builtin`。未设置 fast-math、向量化成本模型、LMUL、VLEN 或强制向量化选项。运行时单独关闭向量化，其代码不参与 benchmark 成功位点统计。

## 总体结果

| 测试组 | 可执行文件 | 构建成功 | 裸机运行通过 | GCC 成功位点 |
| --- | ---: | ---: | ---: | ---: |
| tsvc | 151 | 151 | 151 | 99 |
| polybench | 30 | 30 | 30 | 85 |
| llvm | 10 | 10 | 10 | 160 |

标量参考比对：191 项精确匹配，0 项在数值容差内，0 项不匹配或缺失。详见 comparison.json。

## 统计口径与限制

- 成功位点：GCC `-fopt-info-vec-all` 中 loop vectorized / basic block part vectorized 的源码位置与消息去重；TSVC 排除逐项重复编译的公共 arrays/common/dummy 文件，其他测试组仍包含初始化、检查代码；不等价于内核覆盖率。
- missed 位点只计 couldn't vectorize loop，GCC 同一循环有多次分析与变换，成功和 missed 可同时出现，不能据此直接计算失败率。
- 内核 RVV 数量为反汇编中的静态指令数，含 vset 指令，不能解释为运行时执行次数或加速比。TSVC 使用 sNNN/v* 函数，PolyBench 使用 kernel_* 函数；LLVM 的混合 harness 统计是筛选后的近似值，精确归因见逐函数 JSON。
- TSVC：151 项独立源码/ELF/BIN，LEN_1D=320、LEN_2D=32、iterations=32；保留独立 dummy 翻译单元且不启用 LTO，每个程序仅执行一项。宿主时间调用换为 cycle 读取。独立 BSS 与上游部分不完全初始化导致 23 项 checksum 不同于旧聚合运行，但全部与各自独立标量参考匹配。
- PolyBench 使用 SMALL_DATASET；LLVM 保留可移植标量循环与检查，适配范围和排除原因见移植说明。
- NEMU 验证裸机启动和程序成功退出；各向量版本另外与关闭 loop/SLP vectorization 的同编译器构建逐项比较。PolyBench 比较所有 live-out 值，TSVC 比较原始 checksum，LLVM 用例比较上游驱动 checksum。模拟器周期不代表硬件性能。
- 没有开展 LLVM 编译器实验；LLVM test-suite 在这里是由 GCC 编译的测试源码集合。

## 逐项结果

| 测试组 | 用例 | 构建 | 运行 | 成功位点 | missed 位点 | 内核静态 RVV |
| --- | --- | --- | --- | ---: | ---: | ---: |
| tsvc | s000 | built | passed | 1 | 1 | 8 |
| tsvc | s111 | built | passed | 1 | 1 | 5 |
| tsvc | s1111 | built | passed | 1 | 1 | 11 |
| tsvc | s112 | built | passed | 2 | 1 | 32 |
| tsvc | s1112 | built | passed | 2 | 1 | 28 |
| tsvc | s113 | built | passed | 1 | 1 | 6 |
| tsvc | s1113 | built | passed | 0 | 2 | 0 |
| tsvc | s114 | built | passed | 1 | 2 | 5 |
| tsvc | s115 | built | passed | 1 | 2 | 7 |
| tsvc | s1115 | built | passed | 1 | 2 | 41 |
| tsvc | s116 | built | passed | 0 | 2 | 0 |
| tsvc | s118 | built | passed | 0 | 3 | 0 |
| tsvc | s119 | built | passed | 1 | 2 | 36 |
| tsvc | s1119 | built | passed | 1 | 2 | 89 |
| tsvc | s121 | built | passed | 1 | 1 | 5 |
| tsvc | s122 | built | passed | 2 | 2 | 12 |
| tsvc | s123 | built | passed | 0 | 2 | 0 |
| tsvc | s124 | built | passed | 1 | 1 | 13 |
| tsvc | s125 | built | passed | 1 | 2 | 41 |
| tsvc | s126 | built | passed | 0 | 3 | 0 |
| tsvc | s127 | built | passed | 1 | 1 | 11 |
| tsvc | s128 | built | passed | 1 | 1 | 8 |
| tsvc | s131 | built | passed | 1 | 1 | 5 |
| tsvc | s132 | built | passed | 1 | 1 | 39 |
| tsvc | s141 | built | passed | 0 | 3 | 0 |
| tsvc | s151 | built | passed | 1 | 1 | 5 |
| tsvc | s152 | built | passed | 1 | 1 | 10 |
| tsvc | s161 | built | passed | 1 | 1 | 18 |
| tsvc | s1161 | built | passed | 1 | 1 | 22 |
| tsvc | s162 | built | passed | 1 | 1 | 6 |
| tsvc | s171 | built | passed | 1 | 2 | 5 |
| tsvc | s172 | built | passed | 2 | 2 | 5 |
| tsvc | s173 | built | passed | 1 | 1 | 5 |
| tsvc | s174 | built | passed | 1 | 1 | 5 |
| tsvc | s175 | built | passed | 1 | 2 | 5 |
| tsvc | s176 | built | passed | 0 | 0 | 0 |
| tsvc | s211 | built | passed | 0 | 2 | 0 |
| tsvc | s212 | built | passed | 0 | 2 | 0 |
| tsvc | s1213 | built | passed | 0 | 2 | 0 |
| tsvc | s221 | built | passed | 0 | 2 | 0 |
| tsvc | s1221 | built | passed | 1 | 1 | 5 |
| tsvc | s222 | built | passed | 1 | 2 | 7 |
| tsvc | s231 | built | passed | 1 | 2 | 33 |
| tsvc | s232 | built | passed | 0 | 3 | 0 |
| tsvc | s1232 | built | passed | 1 | 2 | 5 |
| tsvc | s233 | built | passed | 0 | 4 | 0 |
| tsvc | s2233 | built | passed | 1 | 3 | 36 |
| tsvc | s235 | built | passed | 1 | 2 | 10 |
| tsvc | s241 | built | passed | 0 | 2 | 0 |
| tsvc | s242 | built | passed | 0 | 2 | 0 |
| tsvc | s243 | built | passed | 1 | 1 | 11 |
| tsvc | s244 | built | passed | 0 | 2 | 0 |
| tsvc | s1244 | built | passed | 0 | 2 | 0 |
| tsvc | s2244 | built | passed | 1 | 1 | 8 |
| tsvc | s251 | built | passed | 1 | 1 | 7 |
| tsvc | s1251 | built | passed | 1 | 1 | 11 |
| tsvc | s2251 | built | passed | 0 | 2 | 0 |
| tsvc | s3251 | built | passed | 1 | 1 | 11 |
| tsvc | s252 | built | passed | 1 | 1 | 14 |
| tsvc | s253 | built | passed | 1 | 1 | 16 |
| tsvc | s254 | built | passed | 1 | 1 | 16 |
| tsvc | s255 | built | passed | 0 | 2 | 0 |
| tsvc | s256 | built | passed | 0 | 3 | 0 |
| tsvc | s257 | built | passed | 1 | 2 | 44 |
| tsvc | s258 | built | passed | 0 | 2 | 0 |
| tsvc | s261 | built | passed | 0 | 2 | 0 |
| tsvc | s271 | built | passed | 1 | 1 | 12 |
| tsvc | s272 | built | passed | 1 | 1 | 20 |
| tsvc | s273 | built | passed | 1 | 1 | 19 |
| tsvc | s274 | built | passed | 1 | 1 | 17 |
| tsvc | s275 | built | passed | 0 | 3 | 0 |
| tsvc | s2275 | built | passed | 1 | 2 | 11 |
| tsvc | s276 | built | passed | 1 | 1 | 12 |
| tsvc | s277 | built | passed | 0 | 2 | 0 |
| tsvc | s278 | built | passed | 1 | 1 | 23 |
| tsvc | s279 | built | passed | 1 | 1 | 38 |
| tsvc | s1279 | built | passed | 1 | 1 | 16 |
| tsvc | s2710 | built | passed | 0 | 2 | 0 |
| tsvc | s2711 | built | passed | 1 | 1 | 12 |
| tsvc | s2712 | built | passed | 1 | 1 | 12 |
| tsvc | s281 | built | passed | 0 | 2 | 0 |
| tsvc | s1281 | built | passed | 1 | 1 | 16 |
| tsvc | s291 | built | passed | 1 | 1 | 16 |
| tsvc | s292 | built | passed | 0 | 2 | 0 |
| tsvc | s293 | built | passed | 1 | 1 | 3 |
| tsvc | s2101 | built | passed | 1 | 1 | 41 |
| tsvc | s2102 | built | passed | 0 | 3 | 0 |
| tsvc | s2111 | built | passed | 0 | 3 | 0 |
| tsvc | s311 | built | passed | 1 | 1 | 7 |
| tsvc | s31111 | built | passed | 0 | 2 | 0 |
| tsvc | s312 | built | passed | 0 | 2 | 0 |
| tsvc | s313 | built | passed | 1 | 1 | 9 |
| tsvc | s314 | built | passed | 0 | 2 | 0 |
| tsvc | s315 | built | passed | 1 | 2 | 15 |
| tsvc | s316 | built | passed | 0 | 2 | 0 |
| tsvc | s317 | built | passed | 0 | 2 | 0 |
| tsvc | s318 | built | passed | 0 | 2 | 0 |
| tsvc | s319 | built | passed | 0 | 2 | 0 |
| tsvc | s3110 | built | passed | 0 | 3 | 0 |
| tsvc | s13110 | built | passed | 0 | 3 | 0 |
| tsvc | s3111 | built | passed | 1 | 1 | 12 |
| tsvc | s3112 | built | passed | 0 | 2 | 0 |
| tsvc | s3113 | built | passed | 0 | 2 | 0 |
| tsvc | s321 | built | passed | 0 | 2 | 0 |
| tsvc | s322 | built | passed | 0 | 2 | 0 |
| tsvc | s323 | built | passed | 0 | 2 | 0 |
| tsvc | s331 | built | passed | 1 | 1 | 17 |
| tsvc | s332 | built | passed | 0 | 2 | 0 |
| tsvc | s341 | built | passed | 0 | 2 | 0 |
| tsvc | s342 | built | passed | 0 | 2 | 0 |
| tsvc | s343 | built | passed | 0 | 3 | 0 |
| tsvc | s351 | built | passed | 1 | 1 | 41 |
| tsvc | s1351 | built | passed | 1 | 1 | 5 |
| tsvc | s352 | built | passed | 1 | 1 | 50 |
| tsvc | s353 | built | passed | 1 | 1 | 77 |
| tsvc | s421 | built | passed | 1 | 1 | 5 |
| tsvc | s1421 | built | passed | 1 | 1 | 5 |
| tsvc | s422 | built | passed | 1 | 1 | 5 |
| tsvc | s423 | built | passed | 1 | 1 | 5 |
| tsvc | s424 | built | passed | 1 | 1 | 5 |
| tsvc | s431 | built | passed | 1 | 1 | 5 |
| tsvc | s441 | built | passed | 1 | 1 | 27 |
| tsvc | s442 | built | passed | 0 | 2 | 0 |
| tsvc | s443 | built | passed | 1 | 1 | 13 |
| tsvc | s451 | built | passed | 0 | 2 | 0 |
| tsvc | s452 | built | passed | 1 | 1 | 12 |
| tsvc | s453 | built | passed | 0 | 2 | 0 |
| tsvc | s471 | built | passed | 1 | 1 | 9 |
| tsvc | s481 | built | passed | 0 | 2 | 0 |
| tsvc | s482 | built | passed | 0 | 2 | 0 |
| tsvc | s491 | built | passed | 1 | 1 | 11 |
| tsvc | s4112 | built | passed | 1 | 1 | 15 |
| tsvc | s4113 | built | passed | 0 | 2 | 0 |
| tsvc | s4114 | built | passed | 1 | 1 | 16 |
| tsvc | s4115 | built | passed | 0 | 2 | 0 |
| tsvc | s4116 | built | passed | 1 | 2 | 0 |
| tsvc | s4117 | built | passed | 1 | 1 | 15 |
| tsvc | s4121 | built | passed | 1 | 1 | 6 |
| tsvc | va | built | passed | 1 | 1 | 3 |
| tsvc | vag | built | passed | 0 | 2 | 0 |
| tsvc | vas | built | passed | 0 | 2 | 0 |
| tsvc | vif | built | passed | 1 | 1 | 8 |
| tsvc | vpv | built | passed | 1 | 1 | 5 |
| tsvc | vtv | built | passed | 1 | 1 | 5 |
| tsvc | vpvtv | built | passed | 1 | 1 | 6 |
| tsvc | vpvts | built | passed | 1 | 1 | 9 |
| tsvc | vpvpv | built | passed | 1 | 1 | 7 |
| tsvc | vtvtv | built | passed | 1 | 1 | 7 |
| tsvc | vsumr | built | passed | 1 | 1 | 7 |
| tsvc | vdotr | built | passed | 1 | 1 | 9 |
| tsvc | vbor | built | passed | 1 | 1 | 42 |
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
