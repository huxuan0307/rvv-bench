# 短 trace 与 FST 波形工具

本目录存放短 trace 的构建/运行脚本入口（`../script/build_trace.py`、`../script/run_trace_nemu.py`、
`../script/run_trace_verilator.py`）、trace 源码 `trace.c`，以及 FST 波形导出/重建工具
（`../script/exu_wave.py`、`fst_events.c`、`fst_signals.c`）。

## 1. 用途与边界

- **短 trace**：从已完成的 N16 非 LTO 吞吐基准构建目录复用原始 kernel/数学库对象，把完整矩阵压缩成
  N=16 的短窗口镜像，用于确认 kernel、入口与 `trace_region` 在短 trace 中仍被实际执行，而不重新跑
  完整矩阵。
- 它不是性能测量：trace 的 cycles 只是短窗口内的 rdcycle 读数，不能当吞吐或延迟结论；也不是正确性
  验收：只验证短 trace 的镜像身份、TRACE_RESULT 行与 GOOD TRAP。
- 本文档不授权启动新仿真；运行是独立步骤，须按当时授权、使用带日期时间的全新目录并分离 stdout/stderr。
- 当前构建脚本**只支持 `--case log`**：链接后 map 校验按数学库成员 `logvec.o` 与符号 `_ZGV2Nxv_log`
  编写，扩展其它函数需同时确认成员名与 Nx 符号形态后修改脚本，不能只改函数名冒充。

## 2. 前置条件（`build_trace.py` 会逐项拒绝）

源构建目录（`--build`）必须满足：

- manifest 为 `lto=false`、`direct_interface=nx`、`profile=n16`、`build_status=built`，且 `perf-log`
  case 已成功构建；源 ELF/BIN 的 sha256 与 manifest 记录一致。
- 源构建必须带**显式替换数学库**（构建时用 `script/build.py --math-archive <archive>`，而不是安装版
  `libmvec.a`）：`manifest.math_archive` 存在且 sha256 一致；链接 trace 中数学符号必须来自该归档的
  `logvec.o`，若出现 `libmvec.a(` 立即拒绝（避免安装库冒充候选）。
- 冻结 `trace.c`：默认从 manifest sources 的**新键** `single/libmvec/trace/trace.c`（源构建快照中的
  `source/single/libmvec/trace/trace.c`）读取并核对 sha256。只有显式 `--source-key legacy` 时才读
  历史构建的旧键 `single/libmvec/trace.c`；不做路径猜测，缺键即报错。
- 冻结输入头：`--data-dir` 中 `log.h` 的 sha256 必须等于源 manifest 的 oracle 记录；构建前后各核验
  一次，防止输入在编译期间被改。
- 源构建命令：必须含 `-fno-lto` 且链接命令含该归档路径；短 trace 构建只替换 `-c` 的源为冻结 `trace.c`
  并追加 `-DTRACE_*`，只替换 main.o 与输出 ELF；链接前后原 kernel/scalar 对象与归档 sha256 必须不变。

## 3. 构建（`../script/build_trace.py`）

```sh
python3 -B single/libmvec/script/build_trace.py \
  --build <existing-n16-nolto-nx-build> \
  --data-dir <frozen-input-headers> \
  --output <new-trace-build-dir> \
  --mode scalar_strict --mode direct_m2 --mode auto_m2 --k 1
```

| 参数 | 含义 |
|---|---|
| `--build` | 既有 N16、非 LTO、Nx direct 的吞吐基准构建目录（含 manifest.json） |
| `--data-dir` | 与该基准一致的冻结输入头目录 |
| `--output` | 新的输出目录（不存在；脚本拒绝复用） |
| `--mode` | 可重复，取 scalar_strict/scalar_fast/auto_m1/auto_m2/direct_m1/direct_m2 |
| `--case` | 仅 `log`（见 §1） |
| `--source-key` | `new`（默认，新布局键）或 `legacy`（仅历史构建的旧键） |
| `--k` / `--offset` | 链式调用次数（默认 1）/ 冻结输入偏移（0..1008） |
| `--indices` | 可选，16 个 0..1023 的逗号分隔冻结输入下标 |

产物：每个 mode 的 `<mode>-main.o`、`<mode>.elf`、`<mode>.bin`、`<mode>.map`、编译/链接/objcopy/
反汇编的 stdout/stderr 日志；`commands.json`/`commands.txt`；汇总 `trace-manifest.json`，记录源
manifest 路径与 sha256、冻结输入路径与 sha256、trace 源路径与 sha256、offset/indices、每个镜像的
bin/elf sha256 与 `linked_inputs` 身份。

## 4. 运行

NEMU 预检（功能与镜像身份，不做性能）：

```sh
python3 -B single/libmvec/script/run_trace_nemu.py \
  --manifest <trace-build>/trace-manifest.json \
  --nemu <riscv64-nemu-interpreter> \
  --output <new-timestamped-run-dir> [--max-instr 50000000] [--timeout 120]
```

- 逐镜像 staging 并双 SHA 核验（复制前/后、源 BIN、NEMU 自身 sha256）；每个 mode 独立日志。
- 每个 mode 判定：rc=0、未超时、staging SHA 不变、出现 GOOD TRAP、恰好一条 `TRACE_RESULT`、
  无错误标记；结果写 `results.csv`（mode、bin/staged/nemu sha256、rc、timeout、trace_result、
  good_trap、status）。

Verilator 短 trace（含 FST 波形）：

```sh
python3 -B single/libmvec/script/run_trace_verilator.py \
  --trace label1=<trace-manifest-1> [--trace label2=<trace-manifest-2>] \
  --emu <non-static-verilator-emu> --reference <refmodel> --dramsim3-ini <ini> \
  --output <new-timestamped-run-dir> [--seed 123456789] [--jobs 3] [--timeout 48h]
```

- `--emu`、`--reference`、`--dramsim3-ini` **必须全部显式**，不做隐式版本猜测；输出目录必须全新
  （脚本拒绝已存在/符号链接路径），按仓库规范带日期时间命名且不得位于 build 路径。
- 每个 case 独立 `work/`、staged BIN、`trace.fst`（`--dump-wave --dump-commit-trace`）；单 case
  `result.csv` 与汇总 `results.csv` 记录 label/mode/status/reasons/rc/timed_out/aborted/耗时/
  BIN 与 staging 前后 SHA/manifest SHA/`TRACE_RESULT` 的 n,k,begin,end,cycles,failed/GOOD TRAP/
  FST 字节数与 sha256。
- 只要 status 不是通过（缺 TRACE_RESULT、超时、abort、SHA 漂移、缺 GOOD TRAP），该 case 即失败；
  部分日志只能作为诊断，不能当通过。

主机侧回归（不启动仿真）：`tests/host/test_trace_verilator.py`。

## 5. 局限与证据边界

- 短 trace 只证明**该窗口、该镜像**里的调用与符号存在；`TRACE_RESULT` 与 GOOD TRAP 只说明短程序
  跑完并打印了自洽的行，不证明数值正确性、全输入域、ABI 兼容或性能。
- `trace-manifest.json`/SHA 一致只证明源对象与镜像身份未变，不证明微架构 RAW 依赖或因果；RAW 需要
  独立波形/RTL 证据，不在本工具自动证明范围。
- 仅支持 `log`、N16、非 LTO、Nx direct、显式替换归档；其它组合必须另建脚本或先扩展校验，不能靠
  改参数绕过。
- 冻结源码只从 manifest 声明的新键读取；历史构建必须显式 `--source-key legacy`，找不到冻结
  trace.c 就阻断，不允许按目录名/commit 猜历史路径。

## 6. FST 波形导出与 Exu 重建（实验性）

`../script/exu_wave.py` 是针对 XiangShan RTL 提交 `409f42957227518357df5b444f4dba70563165dd` 及其
具体 Verilator 波形层级开发的**保守工具**，不适用于未经核验的 RTL 或其他配置。它不启动仿真、不修改
FST，不从提交时刻推测 Exu 出入点；必须自行确认 FST、`signals.tsv`、RTL 源码与模拟器互相匹配。

- **方法**：以向量 `issuePipes[0..5]` 为向量 Exu 入口（寄存 `ex_0`），以同 Exu 的 `out_uop.valid`
  及 ROB flag/value、debug uopIdx 为出口候选；只有 `pipe=0` 的 VFMac 可取得 `verified_vfmac`，且要求
  输入/输出身份、ROB 有效世代、uopIdx 唯一、按动态 uop 自身 latency 输出。内存 Exu 只认 LSU 端口
  同周期 valid=ready=1、meta.valid=0 且 ROB/control 身份匹配的原生直通为 `lsu_accepted`；VAGQ 输入
  握手不等于 LSU 接收。其余状态保守记为 `uncertain`，**不得计入可信延迟**。
- **时基**：`TOP.clock` 可能只存高电平，工具使用 ROB 的 `GTimer`；必须观察到时基连续加一、无未知和
  重复时间组，且校准匹配样本足够（每类身份匹配≥10 次）才填写周期字段；否则全局留空。`elapsed_cycles`
  是同一条已证明 uop 的出口周期减入口周期，**不是**架构延迟、提交延迟或访存完成时间。
- **输入**：全波形从时间 0 读取，不能截断开头后假设之前的 ROB 分配；`--end-time` 只限制包含的 FST
  时间。导出器需自行用系统 Verilator 的 `fstapi.c`/`fastlz.c`/`lz4.c` 编译 `fst_events.c` 与
  `fst_signals.c`（本机 `gtkwave` include 目录按实际路径替换），命令示例：

```sh
S=/绝对路径/source/single/libmvec
R=/绝对路径/runs/20261114-164952-exu-wave
F=/已经完成且经哈希核验的绝对路径/trace.fst
I=/usr/local/share/verilator/include/gtkwave
gcc -O2 -I"$I" "$S/trace/fst_events.c" "$I/fstapi.c" "$I/fastlz.c" "$I/lz4.c" -lz -lm -o "$R/fst_events"
gcc -O2 -I"$I" "$S/trace/fst_signals.c" "$I/fstapi.c" "$I/fastlz.c" "$I/lz4.c" -lz -lm -o "$R/fst_signals"
"$R/fst_signals" "$F" > "$R/signals.tsv" 2> "$R/signals.stderr"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$S/script" python3 -m unittest discover -s "$S/tests/host" -p 'test_exu_wave.py' -v > "$R/unit.stdout" 2> "$R/unit.stderr"
PYTHONDONTWRITEBYTECODE=1 python3 "$S/script/exu_wave.py" --fst "$F" --signals "$R/signals.tsv" \
  --fst-events "$R/fst_events" --csv /任务目录/data/带日期的-exu.csv \
  > "$R/analyze.stdout" 2> "$R/analyze.stderr"
```

- **输出与边界**：CSV 行给出 pipe、ROB flag/value/generation、pc、uop 身份、attempt、fu_type/
  opcode/fu_latency、入口/出口 FST 时间与校准周期、handoff、`fu_accept`、`outcome`、`certainty`/`reason`。
  `completed` 只表示匹配到输出候选；`uncertain` 或 `handoff_*` 不能当可信生命周期；缺少未实际出现的
  类别不表示已覆盖所有路径。该工具不跟踪非访存标量 Exu、VPerm、VAGQ 拆分请求等，不能当全机 Exu
  覆盖率。
- 应用到新 FST 后应另存一份按相同规则的校准、版本与局限说明，勿直接复用历史可信结论；校准样本不足
  或时基异常时保留空周期列，而不是降低阈值凑通过。
