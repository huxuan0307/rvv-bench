# 七函数五种 frm 的完整 corpus 观察器

实际工具位于 [`script/accuracy/rounding_accuracy.py`](../../script/accuracy/rounding_accuracy.py)，主机测试在 [`tests/host/test_rounding_accuracy.py`](../../tests/host/test_rounding_accuracy.py)；下文示例中的 `tests/newmath` 路径属于历史布局。

本工具只观察直接入口的五种合法 `frm`：0=RNE，1=RTZ，2=RDN，3=RUP，4=RMM。工具**不要求内核一律使用 RNE**，也不自动判定是否需要强制 RNE。目标输出可供独立的 MPFR 高精度真值分析器使用；本工具所称距离严格是**与 RNE 舍入参考值的 ULP 距离**。该参考是历史 MPFR512 区间约定先舍入到目标格式的位模式，而不是未舍入的数学真值；非 RNE 结果即使离这个位模式更近（或更远），也不能单凭此断言其对数学真值更准确（或更不准确），更不能当作 directed 模式正确舍入的证明。

## 范围与历史分组

- `log` 104965、`exp` 102039、`expf` 100608、`logf` 101385、`pow` 124238、`sin`/`cos` 各 103084 条 frozen corpus；每函数在自身独立 ELF 内逐次调用 Nx 与 array 的 m1/m2 和 5 种 frm。每接口每 LMUL 每模式都有完整 corpus。
- 按原 `nx_gate.c` 的 VLMAX 输入分组，最后不足一组复制最后样本补齐，并按组轮转来时 VL=0、1、VLMAX-1、VLMAX。复用 `nx_probe.S`，其在 `jalr` 返回后先读取并保存 vl/vtype/vstart，再读取 frm；这之间没有函数调用或浮点操作，但 frrm 并非返回后的第一条指令。C 侧再以严格编译的屏障读取 frm/flags。raw 串口包含补齐 lane，但导出只计真实输入；该探针以 VLMAX 恢复 VL 后存结果。
- array 的 `log/exp/logf/expf` 循环原始长度 1,2,3,4,5,7,8,9,15,16,17,31,65,129,257；`sin/cos` 每次 257；`pow` 257，并在索引 115422 边界精确切分。每次调用前清 fflags、errno 后以 `fsrm` 设置该模式，直接调用数学入口，然后立即采样 frm 和 flags，读取 errno。构建使用 `-fno-lto -fno-fast-math -frounding-math -ffp-contract=off -fno-tree-vectorize -fno-tree-slp-vectorize -fno-builtin`，无重置 `main` 的数学外 wrapper。仅 `_trm_init` wrapper 初始化 TLS/向量/浮点环境。
- Nx 增加历史语义输入的逐项 broadcast、循环 mixed 和全部四种 incoming VL；array 增加逐项 isolated 和一个 mixed 调用。语义样本没有 frozen RNE reference，输出原始 bits，不计算 ULP。历史 array 的 in-place、tail、零长度和 Nx ABI 寄存器、sticky-state 专门门禁**未复现**；fallback 计数/禁止、canary 全集和 scalar flags/errno 比较也没有复现。因此本工具**不是历史完整门禁，不声称验收等价**，只能说 corpus 调用的分组与输入相同。部分 flags/errno 的初值与历史 corpus 循环不同；调用状态有可能影响带状态实现。历史语义输出与测试时按实际 frm 的 scalar 不作比较。
- `flags`、`errno` 是**完整调用组级**结果（清零起始，flags 为 0–31 的 RISC-V fflags 位图），不代表任何单一 lane；内部冷路径标量运算也可能改变它们。

## 构建与 host 测试

需要 Python 3、显式 GCC、目标静态 libm、数学 archive 以及六个冻结头。工具校验文件的内置 SHA-256 和各 corpus 数量；不会从历史任务目录自动查找依赖。此处示例目录请事先确认可写且不存在；将 `E` 换为实验 worktree 的上层实验目录，`SRC` 为其中 source。以下先只构建一个函数，用不同的、此前不存在的构建目录和带日期时间的运行目录，不启动仿真：

```sh
E=/nfs/home/huxuan/repos/riscv-gnu-toolchain/.dsh/experiments/20261114-1646-sevenmath
SRC="$E/source/single/libmvec"
GCC=/nfs/share/huxuan/riscv-gnu-toolchain-gcc16.1.0-20261109-142438-6ba7795932e0-libmvec/bin/riscv64-unknown-linux-gnu-gcc
LIBM=/nfs/share/huxuan/riscv-gnu-toolchain-gcc16.1.0-20261109-142438-6ba7795932e0-libmvec/sysroot/usr/lib64/lp64d/libm-2.44.a
ARCHIVE="$E/build/log-lifetime-math-nolto/libnewmath.a"
DRIVER="$E/runs/20261114-181000-rounding-driver"
mkdir -p "$DRIVER/tmp"
(cd "$DRIVER" && TMPDIR="$DRIVER/tmp" TMP="$DRIVER/tmp" TEMP="$DRIVER/tmp" \
  PYTHONDONTWRITEBYTECODE=1 python3 "$SRC/script/accuracy/rounding_accuracy.py" run \
  --gcc "$GCC" --archive "$ARCHIVE" --scalar-libm "$LIBM" \
  --headers-dir "$E/inputs/oracle-headers" --role candidate --functions log \
  --build-dir "$E/build/20261114-181000-rounding-build" \
  --run-dir "$E/runs/20261114-181000-rounding-compile" --build-only \
  >"$DRIVER/stdout" 2>"$DRIVER/stderr")
```

构建所有函数省略 `--functions`；构建产物为 `build-dir/<函数>/accuracy.elf`、`.bin` 和 `.map`。每函数先链接发现 ELF 的 errno TLS 符号偏移，再以该偏移重新编译/链接，并通过 readelf 复核最终 ELF 的偏移未变。运行时在首次写 errno 之前核对 `tp == __tls_base == anchor 地址`、anchor 值和 errno 地址，失败输出 `RA_ERROR` 并退出；分析器还把 `RA_TLS` 行的实际/预期偏移与最终 ELF 的 readelf 结果交叉校验。程序每次要求新建构建/运行目录，绝不自动覆盖；所有命令 cwd、stdout/stderr 和 TMP 在 run-dir 下，编译输出仅进入 build-dir；staging 中的 bin 有独立 SHA。进程状态、NEMU stdout/stderr 的字节数和 SHA 及命令路径见 `commands.csv`，源码/输入/工具/ELF/bin 的 SHA 见 `identities.csv`。源码、语义表源、oracle、数学 archive、运行时依赖、ELF/bin 及脚本说明均复制到 `snapshot/` 并复核哈希；分析器可从快照读取 oracle 和语义源。外部 GCC、readelf、objcopy、NEMU、系统静态库的内容**不复制**，分析时仍需可通过记录的原绝对路径读取且哈希不变；若需脱机复验，应自行额外归档它们和路径/版本记录，并妥善保留 runs 整棵目录。`--build-only` 的 `plan.csv` 标记为仅构建，**不能分析为已仿真**。`--help` 安全，不启动仿真。

当前 RNE 限额路径：`run --samples N` 对每函数确定性抽取 N 条冻结输入、只采 RNE；四函数 atan/atan2/acos/log10 必须再显式加 `--nx-only`，只采真实 Nx m1/m2，不伪造 array。七函数默认路径仍为全量五模式或 `--samples` RNE，行为不变。

主机单测需按本项目约束使用独立带日期的运行目录（下方目录应为新目录），TMP、stdout 和 stderr 均在其中：

```sh
RUN="$E/runs/20261114-181100-rounding-host-tests"
mkdir -p "$RUN/tmp"
(cd "$RUN" && TMPDIR="$RUN/tmp" TMP="$RUN/tmp" TEMP="$RUN/tmp" \
  PYTHONDONTWRITEBYTECODE=1 python3 "$SRC/tests/host/test_rounding_accuracy.py" \
  >"$RUN/stdout" 2>"$RUN/stderr")
```

## 显式运行和后处理

**本工具不会隐式启动目标仿真。** 只有 `run` 显式传入 `--nemu /绝对路径` 并给出正的 `--max-instr` 与 `--timeout` 才运行；这与 `--build-only` 互斥。每个函数一个完整进程，一个镜像内全 5 种模式和全部接口/LMUL；不能保证 50M 指令上限足够，串口输出量和指令数很大，具体上限需用户自行决定，以超时或指令耗尽当作失败，不能当作完成。为了便于诊断，可以显式先选 `--functions log`；七函数完整运行则省略。base 与 candidate 必须分别提供实际 archive、独立 build-dir/run-dir、并分别用 `--role baseline` 与 `--role candidate`。启动命令的其他参数与上文一样，但不得传 `--build-only`，需要显式增加 `--nemu ... --max-instr ... --timeout ...`。

完成后针对各次 run 分别执行：

```sh
(cd "$RUN" && TMPDIR="$RUN/tmp" TMP="$RUN/tmp" TEMP="$RUN/tmp" \
  PYTHONDONTWRITEBYTECODE=1 python3 "$SRC/script/accuracy/rounding_accuracy.py" analyze \
  --run-dir "$RUN" --output-dir "$RUN/analysis" \
  >"$RUN/analyze.stdout" 2>"$RUN/analyze.stderr")
```

分析与比较都要求当前执行的脚本 SHA 与该次 run 归档的 observer SHA 一致，并在执行过程中复核；如果当前源码已变动，可用 `"$RUN/snapshot/observer/rounding_accuracy.py"` 代替上面命令中的当前脚本路径（仍需满足外部工具路径和身份要求）。比较时两次 observer SHA 也必须一致。`COMPLETE.csv` 保存并在比较时核验 lanes、groups、summary、paired、coverage、execution 的 SHA；`execution.csv` 保存每函数 NEMU 指令/墙钟统计，旧分析没有该文件时按原快照脚本校验。只有完整核验才写 `analysis/COMPLETE.csv`，中途失败的 `.partial` 不能当作有效结果：核验实际进程 rc=0，NEMU stdout 中唯一真实 GOOD TRAP，stderr 串口全记录与完成计数、函数/接口/LMUL/模式/组序/n/incoming VL/输入/输出位宽/frm；输入语义与 frozen header 按实际 VLENB 重建，不能缺、重、截断、重排或假报数量；所有 `RA_` 行须以 LF 或严格 CRLF 结尾，不接受末行缺 LF 或多余 CR。明确的 ERROR、assert、abort、timeout、fail/failure/failed 等致命诊断会拒绝（仅完整 token `failed=0` 除外，不豁免 `failed=0x1` 等）。运行结束即登记 stdout/stderr 的字节数和 SHA，分析前后再次检查以识别文件被改动或截断；这不是硬件串口传输正确性的证明。所有输入、工具和 ELF/bin SHA 都在分析前后复核。

- `lanes.csv.gz`：`function,interface,lmul,sample_id,frm,x_bits,z_bits,y_bits,ref_bits,rne_ref_ulp`。bits 为无 `0x` 的 8 或 16 位小写十六进制；单目 z 为空；`C` 后是 frozen corpus 零基索引，`S` 后为语义组号和 lane，语义 ref 为空。`rne_ref_ulp` 只在结果与参考均有限时为十进制；NaN/inf/语义为空。对每个 `(function,interface,lmul,sample_id)` 五行连续、模式各一次；不同函数/接口/LMUL 整块连续。此文件可对接独立 MPFR 真值分析。
- `groups.csv.gz`：每真实调用一行，含组号、n、有效 emit 数、Nx incoming VL、调用后 frm、flags、errno。各组 `C` ID 是起始样本下标，`S` ID 是语义组号；不把组级 flags/errno 当单 lane 数据。
- `summary.csv`：按函数/接口/LMUL/frm 统计 corpus 数、有限/非有限样本、bit 完全相等、有限值的最大/平均/总和及 0/1/2/3/4+ ULP 距离分布，以及对应历史**逐值谓词**超限次数。±0、±inf、NaN 参考/结果类别分别计数，含对应参考的 bit-exact 数与参考为 NaN 时结果也为 NaN 的数量。该谓词不能替代整个历史门禁。
- `paired.csv`：同一样本相对 RNE 模式分别更近/等距/更远的数量（仍是到 RNE 舍入参考值的距离）；非有限不可配对单列。`coverage.csv` 和 `COMPLETE.csv` 给出覆盖和身份。

若两次分别已核验、角色及函数一致且 archive SHA 不同，可额外逐样本比较：

```sh
(cd "$BASE_RUN" && TMPDIR="$BASE_RUN/tmp" TMP="$BASE_RUN/tmp" TEMP="$BASE_RUN/tmp" \
  PYTHONDONTWRITEBYTECODE=1 python3 "$SRC/script/accuracy/rounding_accuracy.py" compare \
  --baseline "$BASE_RUN/analysis" --candidate "$CAND_RUN/analysis" \
  --output "$BASE_RUN/analysis/baseline-candidate.csv" \
  >"$BASE_RUN/compare.stdout" 2>"$BASE_RUN/compare.stderr")
```

比较检查每行身份、参考、输入及次序一致；还拒绝采样源码、oracle、GCC、其他静态库、NEMU、最大指令数或超时的已记录身份不一致，只允许数学 archive 和由其生成的镜像不同。生成的 case header 含 oracle 绝对路径，故即使内容相同，两个 `--headers-dir` 路径不同也可能被保守拒绝；可为两次运行指定同一冻结目录。输出 bit 差异及对于同一 RNE 舍入参考的更近/等距/更远，不等于比较真实数学误差。此校验不覆盖系统环境、加载器/动态库、模拟器内部配置等所有混杂因素；**比较只具描述性，不能单凭它把变化归因于数学库**。`n` 包含语义样本，`nonfinite_or_semantic` 单列。若 VLEN 不同，Nx 的组与语义样本自然不同，比较将拒绝。

## 空间和时间量级

七函数 frozen 总计 739403 条，2 接口×2 LMUL×5 模式为 **14788060 条 corpus lane**，另有与 VLEN 相关的语义 lane（以及 Nx 的少量 padding 只在原始串口保留）。目标每个双精单目 lane 的原始数据为 32 个十六进制字符、单精单目 16 个、pow 48 个，加调用行头与换行；以 VLENB=32 粗算，七函数约 0.5 GB 级串口文本/每个 archive，CSV 未压缩通常超过 1 GB；gzip 实际压缩率取决于数据，需预留数 GB/每个 archive，以及独立编译产物空间。目标逐字符输出会明显放大 NEMU 指令数与耗时；本工具没有可跨调用分片的自动重启，不要声称每组满足固定指令预算。仅构建和 host 测试不表明目标数值已经验证。
