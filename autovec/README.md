# GCC 自动向量化裸机实验

三组已有测试源码接入 rvv-bench 的 AM 裸机环境，生成独立 ELF/BIN。使用指定 GCC 16.1.0，LLVM 编译器实验暂缓。LLVM test-suite 在此作为由 GCC 编译的 benchmark 来源。

## 构建与运行

源码默认来自相邻 compiler-experiment 的 benchmarks 目录；完整版本在该项目清单中固定，构建清单还会记录每个上游的实际 SHA、origin 和工作区状态。来源保持各自许可证，生成源码不提交到 rvv-bench。

```bash
# 从 rvv-bench 根目录
make -C autovec all
make -C autovec run
make -C autovec report

# 可指定其他 checkout 根目录
make -C autovec all SOURCES=/absolute/path/to/benchmarks

# 关闭向量化的参考版本，用同一架构和浮点语义核对结果
python3 autovec/build.py --scalar
python3 autovec/run.py --build autovec/build/O3-scalar
python3 autovec/compare.py
```

默认 `-O3 -march=rv64gcv -mabi=lp64d -mcmodel=medany`，使用 GCC 自身的向量化默认策略，无 `-ffast-math`、强制向量化、成本模型覆盖、LMUL 覆盖或 VLEN 覆盖。`OPT=O2` 可复现实验的另一优化等级，不能将 O2/O3 数据混在一起。LLVM 整数回归测试额外使用 `-fwrapv` 定义带符号回绕，标量和向量构建一致。

裸机必需的 `-ffreestanding -fno-builtin -nostdlib` 与 RVV ISA、优化等级是显式实验条件，不声称复现上游宿主 Makefile 的默认配置。禁用 LTO，TSVC dummy 保持独立翻译单元。

运行依赖 `/nfs/home/huxuan/opt/bin/riscv64-nemu-interpreter`，可通过 Make 的 `NEMU=` 或 run.py 的 `--nemu` 覆盖。NEMU 的 GOOD TRAP 和退出状态决定是否成功；超时/错误会保留日志并返回失败。NEMU 是功能验证环境，不将其 cycle 解释为硬件加速比。

## TSVC 独立构建与运行

```bash
# 构建全部 151 项（写入标准 build/O3/manifest.json，清单只含 TSVC）
make -C autovec tsvc
# 全套三组构建仍使用 make -C autovec all

# 只构建一个测试，清单与产物独立保存，不覆盖全套清单
make -C autovec tsvc-one CASE=s151
make -C autovec run-tsvc-one CASE=s151
# Python 等价入口
python3 autovec/build.py --suite tsvc --case s151
```

全套构建中每项产物位于 `build/O3/tsvc/s151/s151.elf` 和 `s151.bin`；单项构建位于 `build/O3/selected/tsvc/s151/tsvc/s151/`，对应单项清单位于 `build/O3/selected/tsvc/s151/manifest.json`。可直接向 NEMU 传入所需 BIN。

生成目录 `build/generated/tsvc/` 中，每个测试独立为 `s151.c`（仅该内核及实际依赖的 helper）和 `s151_main.c`（仅调用该项），公共部分为 `arrays.c`、`common.c`、`dummy.c` 和头文件。保留上游 main 中的参数表达式，使用 `#line` 保留原始内核源码行号。内核与 main 分离，不启用 LTO，不编译其他测试内核。

**初始化语义**：每个程序从全新 BSS 状态调用上游 init 和 initialise_arrays，不执行任何前序测试。上游未为部分四位编号提供独立初始化分支，另外有部分数组/别名依赖前序残留状态，因此独立模式不保证与原先顺序运行的 checksum 一样；本次实测 23 项与旧聚合运行不同。当前 151 项独立版本与各自标量参考均匹配。这是测试隔离后的输入状态差异，不应把聚合运行 checksum 作为独立运行的 golden。最新计时配置把每项外层 `nl` 重复循环统一为恰好 1024 次，取消上游按测试设置的乘除缩放；内层 LEN_1D=320、LEN_2D=32 不变。

## gsim 循环计时

```bash
python3 autovec/build.py --suite tsvc --jobs 16
python3 autovec/run_gsim.py --jobs 32 --timeout 0
# 仅测一项
python3 autovec/run_gsim.py --case s000 --jobs 1
# 从已有完整运行日志重新汇总，不启动模拟器
python3 autovec/run_gsim.py --collect-run autovec/build/gsim-1024/<run-id>
```

生成内核在原始计时边界内联两条 `csrr ..., cycle`，附带编译器 memory barrier；初始化和 checksum 在计时区间外，区间包含 1024 次外层重复、内层循环、dummy 调用及循环控制。未减去 CSR 读数开销，也未插入硬件 fence；结果是该代码区间的 CSR 周期差，不保证最后一次 store 已写回外部内存。输出 `AV cycles=... checksum=... iterations=1024`；`cycles/1024` 表示一次完整外层重复的平均周期，不是单个内层元素或一条 RVV 指令的吞吐。

脚本调用 PATH 中的 run_emu.sh（当前安装位置 `/nfs/home/huxuan/opt/bin/run_emu.sh`），自动选择 `/nfs/share/huxuan/emus/emu-gsim-*` 中修改时间最新者，开启 diff，参考模型为 `/nfs/share/huxuan/riscv64-nemu-interpreter-so-fixvfredusum`。并发范围 1–64，默认32；默认 `--timeout 0` 禁用超时，已有超时项应使用相同镜像重新运行并取消时限。按清单生成扁平 BIN 软链接目录，避免运行旧聚合镜像。日志位于忽略的 `build/gsim-1024/`，结果保存到 `reports/tsvc-gsim-1024/results.{md,csv,json}`，逐项检查 runner 状态、GOOD TRAP、cycle 输出和错误日志；超时不会当成成功。每次结果同时保存到 `reports/measurements/<run-id>/`，其中 `comparison-records.json` 按 benchmark 记录原始总周期、平均外层重复周期、编译器和参数、配置ID、镜像哈希及模拟器/ref路径，供跨编译器和参数对比。比较时应固定源码、数据规模、迭代数、计时边界和模拟器/ref版本；不同运行结果不会覆盖历史记录。

跨运行汇总命令：`python3 autovec/compare_cycles.py`，输出 `reports/cycle-comparison/comparison.{md,csv,json}`。按编译器、参数、源码、迭代次数、计时方式和模拟器/ref标识区分配置，同一配置每项选取最近成功结果；失败和超时尝试保留在 JSON audit，不覆盖已有成功周期。不同配置在表格中分别列出，不自动宣称加速比。

## 覆盖与适配

- TSVC_2：151 个原始 main 中调用的入口。保留内层循环体，LEN_1D=320（40 的倍数）、LEN_2D=32，各项外层重复统一为1024次。原 gettimeofday 边界改为内联 csrr cycle，输出总周期、原始 checksum 与外层迭代次数。
- PolyBench/C：全部 30 个维护中的内核，SMALL_DATASET，保留参数化循环边界和原始 kernel 调用，kernel 标记 noinline 方便归因。替换宿主 instrumentation/内存分配接口，无缓存冲刷或调度。输出 live-out 数组用于参考比较，详见 [移植说明](polybench_notes.md)。
- LLVM Vectorizer：10 个上游 C++ 标量循环程序，替换 STL/计时宿主驱动并保留检查，详见 [移植说明](llvm_notes.md)。对特别大的宿主数据组合采用明确记录的边界采样；保留循环内核长度。兼容层只服务这些用例，不是通用 C++ 标准库。

`av_runtime.c` 设置 FS/VS 和单 hart TLS，提供内存分配、stdio shim、cycle 读取。复用 thirdparty 的 AM/klib 和 link.ld；数学函数链接工具链静态 libm/libgcc，TLS 中 errno 使其不依赖宿主 Linux。内存分配为单次运行 bump allocator，free 不回收；图像入口和 AM I/O 与 rvv-bench 其他裸机测试一致。

## 结果与证据

`build/O3/` 保存原始 .vect 优化诊断、tree-vect 详细 dump、逐例完整编译链接命令、ELF/BIN、反汇编、NEMU 日志和运行状态；`reports/` 保存可提交的报告、逐项 CSV 与逐函数 JSON。`build/` 中的上游派生源码和编译产物全部忽略提交。

报告区分编译器成功位点、函数级循环向量化与静态 RVV 指令。初始化循环也可能向量化，不能把某个测试文件出现 RVV 指令直接当作它的主内核已向量化。数值核对结果保存于 comparison.json，PolyBench 使用高精度 live-out 输出及明确浮点容差；TSVC 核对上游 checksum，不能代替所有内部元素逐一检查。
