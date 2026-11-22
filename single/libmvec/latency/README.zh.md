# libmvec 配对性能与延迟框架

[配对驱动](paired_main.c) 持有 `main()`、遍历矩阵并输出协议；[探针模板](paired_probe_impl.inc) 直接调用所选归档的 `_ZGV`。数学实现不包含测试入口。配对注册 atan、atan2、acos、log10、sin、exp 的双精度公开入口，各定义 m1/m2、固定/Nx、masked/unmasked 八个探针；实际仅能选取所用归档提供的 ABI 格。atan2 参数顺序是 `y,x`。新增注册本身不代表已在目标校准，私有 `_len` 不在此矩阵。独立的[吞吐框架](../throughput/README.md)使用 `LIBMVEC_CSV,3`；不能用其解析器读取配对协议 2。

## 工作量与依赖

- **延迟**：`B=S=1`，上一次返回值经 `r-r` 和 `seed+(r-r)` 形成真实依赖，下一次调用仍收到原始 seed 位。masked 先清理 inactive 返回 lane，atan2 则把同一个零加到两个 operand。测到的是含加载、桥接和 ABI 代价的调用链，不是纯内核延迟。
- **吞吐**：独立调用按相同 corpus 前缀和谓词运行；`B` 重复 pass，`S=1,2,4` 控制窗口内的独立 streams。`N` 是每 pass 的标量元素数，`L` 是每调用 lane 数；完整工作量是 `calls=R×K×B×(N/L)`。`N%L!=0` 或 `(N/L)%S!=0` 的点有显式 skip，不改工作量去凑结果。
- **输入**：显式指定冻结的 binary64 正规、非零输入头与 SHA256 清单；参考必须有限。各头须包含 `DATA_COUNT`、`ORACLE_LIMIT`、`input_bits`、`reference_bits`；`second_bits` 仅用于 atan2。若独立吞吐工具的一元头带第二参数占位，先派生严格一元头并锁其 SHA256，保留原头、派生关系和输入/参考位型，不把占位作为第二输入。`exp` 的正输入在仅延迟模式中限为 `x ≤ 709`，在包含吞吐模式中限为 `x ≤ 687`（给正值累加留余量）；有限下溢结果可维持闭环，不代表下溢语义已验。`sin` 不限有限正规输入幅度，但慢归约数据若纳入应单列其分布，避免冒充常见输入。RNE 下只有返回有限数的 active lane 才能用 `r-r` 恢复输入；调度校准需另以 trace 核验冻结前缀返回有限、恢复位型正确。窗口外的编码步距检查和 checksum 可定位问题，但不能代替真值 ULP、特殊值、完整 ABI 或消费者 LTO 验收。

## 构建

从 rvv-bench 根目录执行。下例只构建，不启动模拟器；`PAIRED_OUT` 必须是新目录，`DATA` 必须包含对应 `.h` 及清单。

```sh
S=single/libmvec/script/latency
python3 -B "$S/build_latency.py" --protocol paired \
  --prefix "$PREFIX" --data-dir "$DATA" --header-sha-file "$DATA/SHA256SUMS" \
  --functions atan,atan2,acos,log10 --vlen 128 --modes both --serial 1 \
  --lengths 128,256 --batches 4 --paired-k 0,1,2,4,8 --streams 1,2,4 \
  --rounds 10 --timing-repeat auto --output "$PAIRED_OUT" --tag paired
```

- `--functions sin,exp --only-case m1_fixed_unmasked` 把两个函数的共同 ABI 格装入同一镜像；同一归档/配置下，`--modes both` 可在同一进程测延迟和吞吐。`--only-case` 支持逗号或分号分隔多个公共探针名，同时裁剪编译、所需链接符号和运行矩阵。两侧换归档的性能对比仍需各自的镜像与运行。
- `--calib` 增加五个有独立 ID 的延迟对照，用 atan 输入，不冒充函数数据。`--trace --timing-repeat 1` 构建独立的非计时参数位/掩码追踪，不可用来测周期。
- 默认链接安装库。`--math-archive` 只在配对模式启用，必须提供所编译的全部 `_ZGV` 符号；标量参考来自工具链的真实 `libm-2.44.a`，不经自动引入 `libmvec.a` 的 `-lm`。显式传入 `--candidate-id` 标识候选。`--lto` 审计最终链接后的基准，不代表实际消费者的 LTO 验收。
- 构建目录含 ELF/BIN、编译命令、快照和 SHA256、链接映射、依赖审计及机器用 manifest。`--skip-audit` 只让审计失败的镜像保留下来，不能把失败改为通过；未冻结的语料和审计失败的构建只用于诊断。
- LTO 依赖自动审计只是筛查。验收须逐格检查最终 ELF：`rdcycle` 确实包围实际目标调用；延迟路径从返回寄存器经过桥接写回下次调用的 ABI 实参并跨回边，不能只有一条未被消费的同源 `vfsub`；吞吐路径的实参从独立种子加载而不是上一次调用的结果。找不到明确计时边界、返回到实参的数据流或独立种子加载，不能仅凭自动审计成功签收。

## 配对协议 2

紧凑计时日志按 [paired_protocol.h](paired_protocol.h) 编码：`PAIR_HEADER` 固定矩阵和计时配置，`PAIR_CASE` 在第一条 `P` 前完整映射索引、ABI、函数、`L`；`PAIR_SEED` 绑定各函数的 `N` 前缀。`count` 是加载的最大请求前缀，非整个头的 `DATA_COUNT`。窗行为：

```text
P,<index>,<l|t>,<N>,<B>,<K>,<S>,<round>,<calls>,<cycles>,<R>
```

`round=0` 为 first，1–2 为 warmup，3 起为 steady。窗口在区间外缓冲输出；`PAIR_CHECK` 可能早于同一组最后一条 `P`。按键而非相邻行核对 `PAIR_SKIP`、`PAIR_WARN`、`PAIR_CHECK`，最后只允许一个 `PAIR_END`，其中 `records/checks/failed` 必须与明细一致。`K=0` 不运行计时工作，但普通探针仍做窗口外的 `L` 个检查；校准对照不做数学检查。带 `R` 的紧凑行是当前计时格式；无 `R` 的紧凑或 verbose `PAIR` 历史记录不能用作当前计时验收。

`R` 是一个 `rdcycle` 区间内的重复倍数，范围 1–64。`auto` 每点用一个不记入正式轮次的窗口估算 `R`，目标 40,000 原始周期，可能在不同点、模式或候选中选不同 `R`。普通探针的这次预测也做窗口外检查。`calls` 已包含 `R`；成本直接用 `cycles/calls`，拟合时用 `cycles/R` 和 `calls/R`，不能再多除一次。`K=0` 只作诊断；非零工作报告要求原始 steady 窗全部不少于 20,000 cycles、至少五个 steady 窗，样本 CV ≤1%；相对差的绝对值低于 0.2% 是平局，边界不算平局。

## 运行与验收

[配对运行器](../script/latency/run_paired.py)保存带时间戳的独立运行、staging、DiffTest 和进程证据；[配对解析器](../script/latency/paired_parse.py)只读这些记录，不启动目标。仅在获准启动模拟器后执行第一条命令；`RUN` 和 `ANALYSIS` 是两个新的输出目录，`RUN` 的名称含 `YYYYMMDD-HHMMSS`，两者都不能位于构建路径下。

```sh
python3 -B "$S/run_paired.py" --manifest "$PAIRED_OUT/manifest.json" \
  --kind gsim --emu "$EMU" --reference "$REF" --dramsim3-ini "$DRAM_INI" \
  --output "$RUN" --seed 123456789 --timeout 2h
python3 -B "$S/paired_parse.py" --run "$RUN" --out "$ANALYSIS"
```

- `samples.csv` 保留每轮的阶段、调用数和原始周期；`cells.csv` 按点给出稳态窗数、最小/中位周期、每调用成本、样本 CV 和质量状态。其中 `control` 为零工作诊断，`insufficient-steady`、`short-window`、`unstable` 分别为轮数、窗口和抖动不达标，只有 `eligible` 可用于拟合。
- `fit.csv` 对同一 case/mode/N/B/S 下至少两个达标的非零 K 点，用中位原始周期除以 R 对调用数除以 R 拟合，含斜率、截距和最大相对残差。`acceptance.csv` 的 `complete` 仅表示单次运行及身份完整，不表示所有点达标，更不是性能 A/B 结论。
- `paired_parse.py --manifest FILE --log FILE` 只核对 guest wire，输出 `evidence=diagnostic`，不生成计时 CSV；它不接收旧协议、trace 或不完整前缀。自动短窗预警来自未输出的预测窗；解析核对已有预警，质量仍按实际稳态窗判定。
- 正式比较需要同配置的独立运行、冻结输入、完整窗口与 oracle、正确 footer、成功退出、单行 RTL Core 0 GOOD TRAP、DiffTest 参考及无错误/禁用证据。trace 镜像只用于参数位追踪，不由当前计时运行器验收；不同模拟器不做性能 A/B。

## 快速迭代流水线

[iterate_paired.py](../script/latency/iterate_paired.py)把「建镜像 → NEMU 预检 → GSIM → 汇总表」串成一条命令：每个 `(函数, 探针)` 单独建镜像并并行推进，墙钟由最慢单元决定。它不改变协议口径，只是把窗口压到刚过门限并并行化，用于实现迭代期的「这一版能不能接受」；正式证据仍用 `--profile full` 复核。

```sh
python3 -B "$S/iterate_paired.py" --prefix "$PREFIX" --data-dir "$DATA" \
  --header-sha-file "$DATA/SHA256SUMS" --emu "$EMU" --reference "$REF" \
  --dramsim3-ini "$DRAM_INI" --functions sin,exp \
  --probes m1_scalable_unmasked,m1_auto --profile fast --jobs 4 \
  --output "$OUT" --pair-check --timeout 2h
```

- `--profile fast`：`K=1`、`R` 按「40,000 窗口目标 ÷ (单轮调用数 × 预期单次周期)」逐单元推算（`sin`→5、`exp`→7）、仍取 8 轮（稳态窗不少于 5 个）；`--profile full` 即 `K=1,2`、`R=auto` 的正式档。
- `--pair-check` 打印自动向量化链相对手写链的差，用于确认两套调用方式仍然一致。
- 门限：`quality=eligible`、最短窗 ≥20,000 cycles、稳态窗 ≥5、样本 CV ≤1%；任一不满足则以非零状态退出并在 `summary.csv` 的 `gate` 列写明原因。
- `summary.csv` 每行一个 `(函数, 探针, K)`：`median_cycles_per_call` 单次调用中位周期、`min_window_cycles` 最短窗、`sample_cv`、`quality`、`gate`。
- 已验证的档位等效性：`fast` 与 `full` 的手写链差 0.03%，自动向量化链差 0.03%（`sin`）与 0.26%（`exp`），见任务数据的 `fast-vs-full.csv`；NEMU 预检每镜像单独进程执行，因为参考模型在同一进程内不能重复运行。

历史闭环延迟（`LAT_*`）见[历史延迟与扩展方法](INTEGRATION.zh.md)。
