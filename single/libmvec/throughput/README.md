# libmvec 香山裸机吞吐测试协议

本目录是已安装 libmvec 的香山裸机吞吐基准（full-development profile），不覆盖旧构建、不替代正确性/ABI
验收。性能数据只来自非 static gsim 的目标端 `rdcycle`；NEMU 只做功能预检，其 cycle 不得当性能。
脚本入口在 `single/libmvec/script/`（build/run/collect/perf_compare），本目录只保留协议与 C 源码：
`throughput/`（perf.c、kernel*.c/h）、`runtime/`（entry_rne.c、tls_anchor.c、tls-safe.ld）、
`tests/abi/`（abi.c、abi-data.h）。历史 v2/kv1 日志只按采集时的协议核验，不套用下文的 v3 质量门槛；
本目录的协议不替代正确性、ABI 或数学精度验收。

## 构建（`script/build.py`）

```sh
python3 -B single/libmvec/script/build.py \
  --prefix <toolchain-prefix> --data-dir <frozen-input-headers> \
  --output <new-build-dir> --case log --profile n16
```

- `--prefix`：含 `bin/riscv64-unknown-linux-gnu-*` 与 sysroot 的工具链前缀；`--data-dir`：显式的冻结
  输入头目录（按函数提供 `<fn>.h`）；`--output` 必须是新目录。
- `--case`：可重复，取 14 个函数名（log/exp/sin/cos/pow/logf/expf/sinf/cosf/powf/log10/atan/atan2/acos），
  以及准确性用例 `abi-public`、`float3-unit`。
- `--profile`：`n16`（默认，lengths=[16]，batches 与 full 相同：log/exp 为 [1,4,16]、其余为 [1,4]）、
  `format-smoke`（仅 `--case log`，lengths=[16]、batches=[1]，只用于格式确认）、`full`
  （lengths=[16,128,1024]、batches=[1,4]，log/exp 追加 16）、`grid`（必须显式 `--lengths`/`--batches`）。
  长度与批量只允许在 grid 下显式给出。
- `--math-archive <archive>`：显式替换静态向量库。给出时链接 `entry_rne.o`/`tls_anchor.o`，标量 libm、libc 和 libgcc 按绝对路径链接，不经可能引入安装版 libmvec 的 `-lm`。链接映射须出现显式归档成员，不得从安装库补入缺失符号。未给出时使用安装版 `libmvec.a`。
- 其他：`--direct-interface {nx,array}`（默认 nx）、`--lto`、`--timing-repeat R`（1–64，窗口内内核重复
  次数，不是工作负载维度，K 仍是调用次数）。
- 契约：安装库至少应有 36 个发布符号；显式候选只须满足所选用例使用的入口，不补齐未选择的函数。模式矩阵不使用 mask/fixed-VL 变体；有匹配接口时按函数追加 LMUL4/8。
- 准确性用例 `abi-public` 要求安装库/归档提供 `abi.c` 声明的全部入口（最少覆盖 36 个发布符号），
  逐符号记录 `abi_symbols` 并打印 `LIBMVEC_ABI`；`float3-unit` 只跑数组入口单元测试。

## Manifest（schema_version = 1）

`schema_version: 1`、`suite: libmvec-xs`、`build_status`、工具身份（gcc 版本、binutils 等）、库与
sysroot 身份、源码快照、support archives、oracle（冻结输入头 sha256）及 cases。

每个性能 case 记录 name、suite、BIN/ELF 与 sha256，以及 `expected`：function、profile、
`wire_format: csv3`、modes、lengths、batches、phases（first=1,warmup=2,steady=7）、sample_count、
calibration_count、min_checks（1000）、`order_policy: rotate-all-(模式数+控制数)`、timing_repeat。
准确性 case（abi-public/float3-unit）不使用紧凑 CSV，`expected.wire_format` 保持 kv1 语义。

- 固定 10 轮：第 0 轮 first，第 1–2 轮 warmup，第 3–9 轮 steady；相位与轮次是协议的一部分。
- 基础模式编号 0–8：scalar_strict、scalar_fast、auto_m1、auto_m2、direct_m1、direct_m2、rdcycle、
  empty、copy。库/编译器支持时追加 direct_m4、direct_m8（编号 5 之后、控制之前，共 11），以及
  编译器声明 `__riscv_simd_math_abi_v1_lmul8` 时的 auto_m4、auto_m8（共 13）；overlay 头按该宏把
  scalable notinbranch 掩码从 0x22 扩到 0x2222，旧编译器会拒绝更宽参数而不是忽略。
- `auto_m4`/`auto_m8` 允许 GCC 生成更窄的余数循环（auto_m8→m4/m2/m1、auto_m4→m2/m1）；实际调用
  符号记入 `observed_vector_calls`，不是错误。
- 实际执行顺序：mode = (order + r) % (模式数+控制数)；CSV 按 mode 排版，解析时
  order = (mode - r) % (模式数+控制数) 精确还原。
- 解析器必须对照 manifest，不猜测协议、不接受降级格式。

## 串口协议：CSV v3（当前默认）与历史 CSV v2 / KV v1

性能 case 的目标输出是新紧凑 CSV v3；collector 仍接受历史日志的 CSV v2 与 KV v1 原文。协议版本由
manifest 的 `expected.wire_format` 决定（v3 为当前新构建；v2/kv1 只对应历史数据），不能把不同格式
混在一次验收里。一次成功验收的 CSV 块必须是单一 stdout 或 stderr 输出流中的连续块，行尾均为完整的
LF 或 CRLF；不得跨流拆分、混合行尾或把残行拼进块。CSV v2 头没有 `timing_repeat`，按隐式 R=1 解码，
仅对应历史数据，不享受 v3 的 20,000-cycle 稳态/CV≤1% 质量门槛。

CSV v3 头（7 字段）：`LIBMVEC_CSV,3,<function>,<vlen_bits>,<seed>,<profile>,<timing_repeat>`；
CSV v2 头（6 字段）没有最后的 `timing_repeat`（历史数据只能 repeat=1）。两种版本的行结构相同：

```csv
LIBMVEC_CSV,3,log,128,123456789,format-smoke,1
M,strict,fast,auto1,auto2,direct1,direct2,rdcycle,empty,copy
m,hash,b0,c0,b1,c1,...,b9,c9
B,16,1
D,0,<hash>,<b0>,<c0>,...,<b9>,<c9>
...
D,8,<hash>,<b0>,<c0>,...,<b9>,<c9>
T,<checks>,0,<logical-record-count>
E,perf-log,pass
```

- 头字段固定 seed=123456789、rounds=10；vlen_bits 必须是 128 的 2 的幂倍数；case 名由 `perf-<function>`
  确定，并与 E 行、manifest 交叉核验。
- M 行：固定模式图例；下一行 m 是 D 记录的字段表头（`hash` + 10 对 `b`/`c`）。
- B 行：n、K；按 manifest 的 lengths 外层 / batches 内层依次输出，每块恰好（模式数+控制数）个 D 行
  （9 模式 9 行、11 模式 11 行、13 模式 13 行），mode 按 0..N-1 顺序且不缺不重。
- D 行：mode、十轮共享 hash、10 对起始计数 b 和耗时 c。`c` 是目标端 uint64 的 end-begin；
  host 以 `end = (b + c) mod 2^64` 重建。保存 b 可核查时间轴与轮次，而不是只留汇总均值。
- hash：同一 n/K/mode 十轮输出的摘要。guest 打印前逐轮检查一致；不一致打印 `LIBMVEC_ERROR` 并非零
  退出，不打印成功 E 行。不同实现不要求 hash 相同，各自与 MPFR/契约误差对照。
- T 行：逐元素/边界检查数、失败数、逻辑样本总数。E 行：唯一且完整的 case + pass。
- 以 9 模式 `format-smoke` 为例：15 个物理行承载 90 个逻辑样本（9 行 D × 10 轮）；完整配置的物理行数
  随 lengths×batches 块数线性增长。物理行数减少不代表丢弃逻辑样本，也不代表内核变快。

KV v1（历史）：每行一个 `LIBMVEC_*` tag 与空格分隔 `key=value`：
`CONFIG`（case/function/vlen_bits/seed/rounds/profile）、`SAMPLE`/`CALIBRATION`
（case/function/mode/n/k/phase/round/order/cycle_begin/cycle_end/cycles/checksum）、
`CHECK`（case/checks/failed=0）、`COMPLETE`（case/status=pass）；准确性 ABI 程序为 36 条
`LIBMVEC_ABI`，任意错误仍是非零退出。

## 运行（`script/run.py`）

```sh
python3 -B single/libmvec/script/run.py nemu --manifest <build>/manifest.json \
  --nemu <riscv64-nemu-interpreter> --output <new-timestamped-run-dir>
python3 -B single/libmvec/script/run.py gsim --manifest <build>/manifest.json \
  --emu <non-static-gsim> --reference <refmodel> --dramsim3-ini <ini> \
  --output <new-timestamped-run-dir>
```

- `nemu` 只做功能预检；`gsim` 用于性能。gsim 必须显式给 `--emu`（**非 static**）、`--reference`、
  `--dramsim3-ini`，不做隐式版本猜测。
- direct GSIM 默认单 case 超时 `24h`；只有明确授权不限时才传 `--timeout none`，仍可响应取消并回收进程组。`--jobs 2 --no-smoke` 可让两个已预审 case 同时启动；`--no-smoke` 只跳过初始串行预检，不跳过完成后的严格采集。
- `--backend run-emu` 通过外部 `run_emu.sh` 调度时必须显式给 `--runner`；直接后端最多 4 并发，
  外部后端 1–32。
- 运行目录必须全新且带日期时间，不放在 build 路径；每 case 独立 cwd、staged BIN 双 SHA、stdout/stderr
  分开；工具/配置/命令身份写入运行元数据。仿真结束前不得把中间结果当验收结论。

## 采集与对比（`script/collect.py`、`script/perf_compare.py`）

```sh
python3 -B single/libmvec/script/collect.py --run <run-dir> --require-complete
python3 -B single/libmvec/script/perf_compare.py --function log --n 128 --k 1 4 \
  --before 接口A=<accepted.csv> --after 接口B=<accepted.csv> \
  --max-regression 5 --output <new-comparison.csv>
```

- collector 严格解码 v3/v2/kv1，审计样本矩阵、轮次、trap、终态、checksum 类型与工具身份；在 run 目录的
  `reports/` 下写 `samples.csv`、`results.csv`、`groups.csv`、逐 case 的原始 CSV 副本、`results.json`、
  `summary.md`、`dram-config.json`，以及诊断用的 `recovered-samples.csv`/`recovery.json`。`groups.csv`
  按 case/mode/n/K/phase 汇总 count、rounds、timing_repeat、窗口下限与 CV、quality_ok 和原始/逐元素中位数；
  `rdcycle`/`empty`/`copy` 控制组只保留原始样本，不进入按元素归一化的汇总，也不作为加速比分母。
  `--require-complete` 对未通过运行返回非 0；部分行只作诊断，不进入有效性能汇总。
- 仓库不再提供独立 `recover.py`。只有完整运行目录才有审计；仅有截断日志、缺运行元数据时无法完成
  严格验收，也不能把行级回收当作通过。
- `perf_compare.py` 只比较已验收 CSV 的相同 interface/function/n/K 标签、九个模式、十轮中的 steady
  周期（按 R 归一），要求两侧 `wire_format` 与 `timing_repeat` 相同；输出 before/after 中位数、
  `change_pct`、范围和 `checksum_equal`。`--function` 支持全部 14 个可构建函数：log/exp/sin/cos/pow/
  logf/expf/sinf/cosf/powf/log10/atan/atan2/acos。v3 非控制模式必须满足每个稳态窗口 ≥20,000 cycle、
  sample CV ≤1%，差值绝对值 <0.2% 记为 tie；v2/kv1 仅作历史对照，不套用该质量门槛。
  `--max-regression` 是回退百分比上限（默认 5%，仅作筛选，不是正确性证明），`--output` 必须是新文件。
  单次运行比较只用于筛选，必须具备同配置的另一独立复跑；不同模拟器、不同窗口或未验收数据不得混算。

## 快速迭代流水线（`script/iterate_throughput.py`）

一条命令串起「建单模式镜像 → NEMU 预检 → GSIM → 汇总表」：一个函数建一份 `--split-modes`
清单（每个测量模式一个镜像），镜像之间并行仿真，`run.py` 的 `--jobs` 上限 4，因此流水线按 4 个
镜像一片切开并行；墙钟由最慢的镜像决定。

```sh
python3 -B "$S/iterate_throughput.py" --prefix "$PREFIX" --data-dir "$DATA" \
  --case sin,exp --emu "$EMU" --reference "$REF" --dramsim3-ini "$DRAM_INI" \
  --profile fast --jobs 10 --output "$OUT" --pair-check --timeout 24h
```

- `--profile fast`（默认）：`R=20`，最慢的单元格（`exp` 标量约 9.3 周期/元素）仍有
  128×20×9.3 ≈ 23,800 原始周期，高于 20,000 门限；`--profile full` 是 `R=32` 的正式档。
- 统计口径与正式解析一致：十轮中前 3 轮预热、后 7 轮 steady，`cycles_per_element` =
  steady 中位窗口周期 ÷ R ÷ (N·K)；控制列（`rdcycle`/`empty`/`copy`）不作为吞吐。
- 门限：root 7 个 steady 窗口、每个 ≥20,000 原始周期、CV ≤1%、运行与测量状态均为 passed；
  任一不满足则非零退出，并在 `summary.csv` 的 `gate` 列写明原因。
- `--pair-check` 打印自动向量化相对直接调用的差（`auto_mN` vs `direct_mN`），用于确认两套调用路径仍一致。
- NEMU 预检每镜像独立进程执行（参考模型在同一进程内不能重复运行），并按清单 `required_markers` 判定。

## 测量与证据边界

- 先缓冲全部样本再打印；oracle、初始化、校验、checksum、打印都在计时外。`rdcycle` 控制只读一对
  计数器，empty/copy 各调用 K 次；不扣开销、不删除离群值。控制组只保留原始样本/原始计数，
  不生成按元素归一化指标；v3 的窗口下限/CV 质量判定只施加于非控制模式。
- 构建、NEMU、gsim、collect 分离；完整参数运行前先要有小用例格式确认；仿真期间按授权与批次规则
  采集，不自动恢复已停止的测试。
- 通过条件：E 行 `pass`、真实 RTL Core0 GOOD TRAP、进程 rc=0、无差分错误、manifest/BIN/工具身份
  核验通过；截断、缺块、额外/乱序行、整数溢出、版本不匹配均不能通过整次验收。
- checksum/hash 一致只说明同一固定输入下十轮摘要一致，不证明数学全域正确；线性/比率不证明微架构
  因果。证据、身份、失败与未测范围须由报告与独立审查核对，不能只引用本文件。
