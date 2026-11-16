# Exu 波形跟踪工具（实验性）

`exu_wave.py` 是针对 XiangShan RTL 提交 `409f42957227518357df5b444f4dba70563165dd` 及其具体 Verilator 波形层级开发的**保守工具**，不适用于未经核验的 RTL 或其他配置。它不启动仿真、不修改 FST，不从提交时刻推测 Exu 出入点。必须自行确认 FST、`signals.tsv`、RTL 源码与模拟器确实互相匹配。名称、FU 语义、pipe 数、输入/输出协议或时钟变化时须重新核验后修改工具；缺少必要信号即报错，不自动猜测配置。

## 分析边界与身份

- 向量 `issuePipes[0..5]`：入口是对应 `exu.ex_0_valid` 对应的**寄存** `ex_0`，而不是 `in_uop` 或提交；运算结果出口是同一 Exu 的 `out_uop.valid` 及其 ROB flag/value、debug uopIdx。仅 `pipe=0` 的 VFMac 可取得 `verified_vfmac`，而且要求 `Vfmac.in_ex_0_valid`、ROB 有效世代与 debug/control uopIdx 一致、唯一候选、按此动态 uop 自身 `latency` 在对应 `Vfmac.out_ex_N_valid` 输出、时间差为 N；其他 FU 仍是 `uncertain`。`completed` 只代表匹配到输出候选，不代表无条件证明身份或生命周期。
- 内存 `issuePipeLdu[0..2]`、`issuePipeStaMou[0..1]` 与 `issuePipeStdMoud[0..1]`：入口是内存 Exu 的寄存 ex0，考虑持有、重放、撤销和替换。Exu 输出首先经过 VAGQ；**VAGQ 的输入握手本身不等于 LSU 接收**。只有 LDU/STA 对应 LSU 端口在同周期 valid=ready=1、meta.valid=0 且 ROB flag/value 与 control/debug uop 身份均匹配（即仲裁后的原生直通）才记录 `lsu_accepted`；仅在其它有效世代、首个尝试和校准条件也满足时标记 `verified_lsu`。VAGQ 拆分的多次请求尚无法安全关联，STD 的 LSU ready 在样本中未暴露，因而只给 `memory_handoff_only` 和 `handoff_*`，不填出口或延迟；不使用访存完成、ROB 提交、或者单纯的 VAGQ 握手作为 LSU 接收。
- 此工具**不跟踪非访存的标量整数/标量浮点 Exu**、VPerm 单元、VAGQ 拆分 LSU 请求或所有 vector store 到 LSU 的路径。不能把结果当作全机 Exu 覆盖率。某路径虽有 ex0 和 out，但 FU 接收、重试或具体映射未充分验证，也只能输出 `uncertain`。
- ROB 从采样的 `canAccept && req.valid && req.bits.firstUop && !redirect.valid` 建立分配世代与 PC；同采样执行事件按边沿前的旧世代处理。提交信号只用于撤销有效世代，不用于计算出入点。循环指针重用会生成新世代；同一 `(ROB flag/value, generation, uop)` 即使迁移到不同 pipe 也被视为可能重发，最后对所有重复身份的候选（含先前候选）降为不确定；部分不同管线的独立合法片段也可能因此被保守排除。若相同 pipe 重叠则不按 FIFO 造匹配。`seq_num` 只展示，绝非唯一键。redirect 使用 409f 的 `RobPtr.needFlush` 与固定依赖 `utility` 提交 `eb8e12b00a33e4716e7778175f6dd45b7a3a9ede` 的环形指针比较；ROB 控制 X、缺失映射、输出 X、冲突或出入点未观察到时保守失效/不确定。
- 全波形从时间 0 读取，不能截断开头后假设之前的 ROB 分配；若某个世代发生在波形外，输出中 `generation`、`pc` 留空。遇到无法识别的关键值，相关状态失效，不进行猜配；部分未知控制只有统计而不一定生成一行，不能据输出行数推断精确总数。

## 时基

`TOP.clock` 可能只转储高电平，本工具使用 ROB 的 `GTimer`；必须观察到该时基连续加一、无未知和重复时间组，且对于 `pipe0` 输入到寄存 ex0 下一周期的身份匹配和 VFMac latency=1 的 ex0→out 一周期身份匹配各至少 10 次、全部匹配，才能填写周期字段。样本不足或时基异常时全局留空全部周期列。输出的 `elapsed_cycles` 是同一已证明 uop 的出口周期减去寄存 ex0 入口周期，0 代表同周期交接，**不是**指令架构延迟、提交延迟、总访存完成时间；不确定条目的该列留空。校准只支持已观察时域及验证过的协议，并不能自动保证另一个时钟域、另一个配置或每种 FU 受理语义。

## 输入及运行

前提：Python 3；`fst_events.c` 和 `fst_signals.c` 在 `single/libmvec/`；本机 Verilator 含 `fstapi.c`/`fastlz.c`/`lz4.c`。不要在 `build` 路径运行测试。先建立独立带日期时间的运行目录；stdout/stderr 必须分开。下面在该运行目录下运行，变量需替换为实际绝对路径：

```bash
S=/绝对路径/source/single/libmvec
R=/绝对路径/runs/20261114-164952-exu-wave
F=/已经完成且经哈希核验的绝对路径/trace.fst
I=/usr/local/share/verilator/include/gtkwave

gcc -O2 -I"$I" "$S/fst_events.c" "$I/fstapi.c" "$I/fastlz.c" "$I/lz4.c" -lz -lm -o "$R/fst_events"
gcc -O2 -I"$I" "$S/fst_signals.c" "$I/fstapi.c" "$I/fastlz.c" "$I/lz4.c" -lz -lm -o "$R/fst_signals"
"$R/fst_signals" "$F" > "$R/signals.tsv" 2> "$R/signals.stderr"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$S/tools" python3 -m unittest -v test_exu_wave > "$R/unit.stdout" 2> "$R/unit.stderr"
PYTHONDONTWRITEBYTECODE=1 python3 "$S/tools/exu_wave.py" --fst "$F" --signals "$R/signals.tsv" \
  --fst-events "$R/fst_events" --csv /任务目录/data/带日期的-exu.csv \
  > "$R/analyze.stdout" 2> "$R/analyze.stderr"
```

默认读取全波形；可加 `--end-time` 限制**包含**的 FST 时间，以减少输出和内存。FST 导出器按压缩块做粗时间裁剪，Python 又按时间精确裁剪。不要降低 `--min-checks` 冒充原有 10 样本校准；若覆盖不同配置需重新制定有证据的校准，而不是调整阈值凑通过。

## CSV 字段

| 字段 | 含义 |
|---|---|
| `pipe` | 向量 0–5 或内存 IssuePipe 名称 |
| `rob_flag`, `rob_value`, `generation` | 指针与此轨迹内分配世代；缺失代表未观察到 |
| `pc` | 分配时 debug PC，十六进制；没有有效映射则空 |
| `uop_idx`, `debug_uop_idx`, `seq_num` | ex0 控制 uop、debug uop、调试序号；序号不作为键 |
| `attempt` | 此 ROB 世代 / control uop 内观察到的第几次 ex0 尝试，跨 pipe 共用计数 |
| `fu_type`, `opcode`, `fu_latency` | 原始 FU 类型位图、内部 opcode（十六进制）、RTL 控制字段，非反汇编结果 |
| `entry_fst_time`, `exit_fst_time` | ex0 寄存采样与可信或候选输出的原始 FST 时间；不确定行的输出若存在仍须结合 `reason` 使用 |
| `entry_cycle`, `exit_cycle`, `elapsed_cycles` | 校准过的 GTimer 入口、出口及已验证生命周期之差；没有证明时差值留空 |
| `handoff_fst_time`, `handoff_cycle` | 内存 Exu→VAGQ 交接时刻，**不是** LSU 接收；只有中间握手时有值 |
| `fu_accept` | `vfmac_observed`、`lsu_path`（仅表明该路径，是否接收须看 `outcome`）或 `unverified` |
| `outcome` | `completed` 运算输出候选；`lsu_accepted` 原生 LSU 握手；`memory_handoff_only` 只看到 VAGQ 输入；`vstd_response_unresolved` vector store 目标未证明；`flushed` 重定向冲刷；`rejected` 被拒绝、可能重发；`cancelled` 源依赖取消；`ambiguous` 候选重叠；`trace_end` 尾部未见出口；`signal_unknown`、`rob_control_unknown`、`redirect_unknown`、`memory_owner_lost`、`generation_reused`、`retired_without_exu_output` 均是无法证明原有身份或出口的保守结束。缺少未实际出现的类别并不表示已覆盖所有路径。 |
| `certainty`, `reason` | `verified_vfmac`、`verified_lsu` 限上述验证范围；`uncertain` 不允许计入可信延迟；`reason` 为英文诊断 token，以分号分隔 |

此工具和单测为未提交的实验文件；将本工具应用到新 FST 后应另存一份按相同规则的校准、版本与局限说明，勿直接复用历史可信结论。
