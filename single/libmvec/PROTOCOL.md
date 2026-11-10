# libmvec 香山测试协议

本目录为独立套件，不覆盖旧构建。Manifest schema_version仍为1；性能串口协议通过expected.wire_format选择csv2（新默认）或kv1（历史数据）。准确性ABI程序保持kv1。

## Manifest

`schema_version: 1`、`suite: libmvec-xs`、`build_status: built`、工具身份、源码快照及cases。每项记录name、suite、BIN/ELF和SHA256，性能expected包括函数、profile、wire_format、六种模式、lengths、batches、phases、sample_count、calibration_count、min_checks及rotate-all-9策略。

- 完整配置：lengths=[16,128,1024]，batches=[1,4]，log/exp增加16。
- 格式小用例：profile=format-smoke，仅log，lengths=[16]、batches=[1]；不计入全参数验收。
- 每个配置固定10轮：第0轮first，第1–2轮warmup，第3–9轮steady。
- 模式编号0–8依次为scalar_strict、scalar_fast、auto_m1、auto_m2、direct_m1、direct_m2、rdcycle、empty、copy。
- 实际执行顺序：mode=(order+r)%9；CSV按mode排版，解析时order=(mode-r)%9精确还原。
- csv2的required_markers为`E,perf-<function>,pass`；kv1保持原LIBMVEC_COMPLETE标记。解析器必须对照manifest，不自动猜测协议或接受降级。

## 紧凑CSV v2

下例中的省略号仅用于说明；实际文件每行包含完整10轮，整数为无千位分隔的十进制uint64。

```csv
LIBMVEC_CSV,2,log,128,123456789,format-smoke
M,strict,fast,auto1,auto2,direct1,direct2,rdcycle,empty,copy
m,hash,b0,c0,b1,c1,b2,c2,b3,c3,b4,c4,b5,c5,b6,c6,b7,c7,b8,c8,b9,c9
B,16,1
D,0,<hash>,<b0>,<c0>,...,<b9>,<c9>
...
D,8,<hash>,<b0>,<c0>,...,<b9>,<c9>
T,<checks>,0,<logical-record-count>
E,perf-log,pass
```

- 首行：版本、函数、实际VLEN位数、seed、profile。固定seed123456789；case由perf-函数名确定，并与E行、manifest交叉核验。
- M行：固定模式图例；下一行：D记录的字段表头，D只是行类型前缀。
- B行：n、K；按manifest的lengths外层/batches内层依次输出，每块恰好9个D行，mode必须按0–8顺序且不缺不重。
- D行：mode、hash，以及10对起始计数b和耗时c。c为目标端uint64的end-begin，保留全部首次/预热/稳态数据；end由host按(b+c) modulo 2^64重建。保存b可核查时间轴与轮次，避免只留下不能追溯的汇总均值。
- hash：同一n/K/mode十轮输出摘要。目标端打印前逐轮检查hash一致，不一致打印LIBMVEC_ERROR并非零退出，不打印成功E行。不同实现不强求hash相同，仍各自逐元素对照MPFR及允许误差。
- T行：逐元素/边界检查数、失败数、逻辑样本总数。总数=块数×9×10，数学样本=块数×6×10、控制样本=块数×3×10。
- E行：case及pass，是整次运行通过的必要条件，必须唯一完整；同时要求真实RTL Core0 GOOD TRAP、进程rc0和无差分错误。中途截断、缺块、额外行、乱序、整数溢出、版本/配置不匹配均不能通过整次验收，但不因此丢弃已经完整打印且能解析的其他D行。
- 小用例从93行KV变为15行CSV，其中9行D容纳90个原始样本；完整LOG/EXP为95行CSV，其他函数65行CSV。物理行数减少不代表丢弃逻辑样本。

全部计时完成后才校验跨轮hash和打印。没有改变数学内核、实际执行轮次、顺序或计时边界；打印减少可降低整程序指令和UART模拟开销，不把这部分节省解释为libmvec内核变快。

## Host输出与可追溯性

采集器严格解码csv2，再执行完整的样本矩阵、轮次、trap、终态、checksum类型与工具身份审计。原始日志始终保留。`samples.csv`仍一行一个逻辑样本，保留函数、mode、n/K、phase/round、order、begin/end/cycles、checksum；`wire_format=csv2`表明end由begin+cycles还原，raw列保存对应原始D行。重复的host列不增加目标print开销。`groups.csv`按函数/模式/n/K/phase统计，并保留原始周期数组。

对于csv2，结束计数是可逆重建值，不声称目标打印了独立的第三份周期数据；同理checksum来自经过目标端十轮一致性检查的共享值。解析报告wire字段记录这些来源。

## 部分日志：逐行回收与运行验收分离

CSV v2的目标打印格式不变，旧BIN/旧日志也可使用新增回收能力。`compact_csv.expand`仍用于严格完整验收；`compact_csv.recover`独立负责诊断性行解析，不要求T/E/pass或GOOD TRAP。

- 看到有效公共头、模式/列定义与B(n,K)后，每个完整D行立即可还原10个逻辑样本，不等待同块其他8行或全程序结束。
- LF或CRLF是完整行边界；末尾半行、字段不全、越界/非法整数整行拒绝，不猜补数字。即使最后一行字段看似齐全，没有结束换行也不采纳，避免把截断数字误当完整数字。
- 一个D行损坏只影响该行，前后其他合法D行保留；模式用行内编号识别，不依赖前一D行成功。
- 需要前面的公共头与B上下文；任意单独抽出一条D且缺少上下文，不能猜测函数/n/K。B损坏会使上下文失效，直到新的有效B。未知版本或不匹配配置不猜测解码。
- 回收结果逐条带source/source_line、原始D行、row_status=parsed-unverified、measurement_valid=false。重复观察保留并诊断，不无声覆盖或假装唯一；尾部错误不撤销已经解析的记录。
- 采集器在处理运行中、缺result、被杀、超时、失败等提前返回前完成回收，stdout/stderr分别解析，绝不拼接两个文件的残行或继承对方上下文。
- 原始日志、BIN和manifest不改写。部分回收数据不进入有效性能分析；`--require-complete`仍对未通过运行返回非0。即使数据行全部齐全，缺E、缺真实trap、有RTL错误或身份核验失败，运行仍不通过。
- 目前目标先测量再集中打印：只有实际打印出来的完整行可恢复，尚在目标内存但没打印的数据不能凭空恢复。本改动不把print挪入计时区间，也不增加目标字符数。

完整运行目录可直接执行collector，在原报告目录新增独立诊断导出（下列是命令模板）：

```sh
python3 -B single/libmvec/collect.py --run RUN_DIRECTORY --require-complete
```

即使退出非0，也可读取`reports/recovered-samples.csv`及`reports/recovery.json`。报告同时保留严格运行状态与诊断回收条数。`samples.csv`额外注明run_status/case_status/measurement_valid，不把失败运行数据标有效。

只有截断日志而无完整运行元数据时可离线解析：

```sh
python3 -B single/libmvec/recover.py --manifest BUILD/manifest.json \
  --case perf-log --log stdout.log --log stderr.log --output NEW_RECOVERY_DIRECTORY
```

输出目录必须不存在。命令退出0仅表示解析/导出操作完成（可能0条），**不表示目标测试通过**；输出固定run_status=not-audited、measurement_valid=false。行级问题和数据来源记录在recovery.json，数值保留完整十进制整数。

## 历史KV v1

历史日志仍可由新collector读取，不改写旧原始文件。每行一个LIBMVEC tag与空格分隔key=value：

- CONFIG：case、function、vlen_bits、seed、rounds、profile。
- SAMPLE/CALIBRATION：case、function、mode、n、k、phase、round、order、cycle_begin、cycle_end、cycles、checksum。
- CHECK：case、checks、failed=0；COMPLETE：case、status=pass。
- ABI准确性：36条LIBMVEC_ABI，各符号唯一且完整覆盖manifest；任意错误仍为LIBMVEC_ERROR且非零退出。

## 测量与运行

先缓冲所有样本，再打印。oracle、初始化、校验、checksum和打印在计时外；first前已做正确性检查，不代表冷缓存。rdcycle控制只读一对计数器，empty和copy各调用K次；不扣开销、不删除离群值。

构建、NEMU、gsim、collect分离。每次用新目录、staged BIN双SHA、工具/配置/命令身份。NEMU仅功能；gsim指定最新非static版本与有效refmodel、开启差分，每case独立cwd与DRAM输出。完整参数运行须先获得小用例格式确认；仿真期间每半小时检查，暂停goal。结果必须完整通过才能进入有效汇总。
