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

## 覆盖与适配

- TSVC_2：151 个原始 main 中调用的入口。循环体保留，LEN_1D=320（40 的倍数）、LEN_2D=32、iterations=32，保障除以 LEN_2D 的重复循环非空。gettimeofday 改为 cycle 读数，输出 cycle 与原始 checksum。
- PolyBench/C：全部 30 个维护中的内核，SMALL_DATASET，保留参数化循环边界和原始 kernel 调用，kernel 标记 noinline 方便归因。替换宿主 instrumentation/内存分配接口，无缓存冲刷或调度。输出 live-out 数组用于参考比较，详见 [移植说明](polybench_notes.md)。
- LLVM Vectorizer：10 个上游 C++ 标量循环程序，替换 STL/计时宿主驱动并保留检查，详见 [移植说明](llvm_notes.md)。对特别大的宿主数据组合采用明确记录的边界采样；保留循环内核长度。兼容层只服务这些用例，不是通用 C++ 标准库。

`av_runtime.c` 设置 FS/VS 和单 hart TLS，提供内存分配、stdio shim、cycle 读取。复用 thirdparty 的 AM/klib 和 link.ld；数学函数链接工具链静态 libm/libgcc，TLS 中 errno 使其不依赖宿主 Linux。内存分配为单次运行 bump allocator，free 不回收；图像入口和 AM I/O 与 rvv-bench 其他裸机测试一致。

## 结果与证据

`build/O3/` 保存原始 .vect 优化诊断、tree-vect 详细 dump、逐例完整编译链接命令、ELF/BIN、反汇编、NEMU 日志和运行状态；`reports/` 保存可提交的报告、逐项 CSV 与逐函数 JSON。`build/` 中的上游派生源码和编译产物全部忽略提交。

报告区分编译器成功位点、函数级循环向量化与静态 RVV 指令。初始化循环也可能向量化，不能把某个测试文件出现 RVV 指令直接当作它的主内核已向量化。数值核对结果保存于 comparison.json，PolyBench 使用高精度 live-out 输出及明确浮点容差；TSVC 核对上游 checksum，不能代替所有内部元素逐一检查。
