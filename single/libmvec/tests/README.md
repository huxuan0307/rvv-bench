# libmvec 验证工具

本目录按验证职责组织，不把历史结果当作当前提交已经通过的证据。入口脚本在 `../script/accuracy/`，主机测试在 `host/`；构建和模拟器路径、数学 archive、oracle 及输出均由调用者提供，不自动查找历史任务。

| 组件 | 用途 | 边界 |
|---|---|---|
| `abi/nx_gate.c`、`abi/nx_probe.S` | 七函数 Nx 数值、环境、incoming VL 和寄存器探针 | 只覆盖所选函数、m1/m2 和实际 VLEN |
| `unit/` | 历史 array 数值及语义门禁；独立 float3 array 测试 | 各函数覆盖不同，不能用性能 checksum 替代 |
| `abi/direct_frm_probe.c` | 小规模五舍入模式直接入口观察 | 不做 MPFR 真值判断或完整 PCS 验收 |
| `accuracy/rounding_accuracy.c` | 完整冻结 corpus 的五模式逐 lane 采集 | 不复现完整历史门禁的所有语义 |
| `../script/accuracy/mpfr_accuracy.py` | 已采集输出的 MPFR 区间数学误差分析 | 主机分析，不执行目标，不验 PCS、flags 或 errno |
| `../script/accuracy/rounding_group_compare.py` | 两组已完成分析的输出位和调用状态比较 | 描述差异，不自动判定任一方正确 |

`unit/float3_unit.c` 通过性能框架的 `float3-unit` case 构建，检查 sinf/cosf/powf array 的零长度、尾部、in-place、flags 和 frm；其编译需要仓库 `autovec/` 及 `tests/abi/` 的 include 路径。它与七函数历史门禁是不同用例。四个新增函数使用同一精度采集框架，不从候选内核运行独立 selftest。

## 运行目录与主机测试

所有测试工作目录、stdout/stderr、TMPDIR/TMP/TEMP 和 staging 都放在名称含日期时间、全部上级路径均不含 `build` 的独立运行目录。构建产物放另一目录；两者必须互不包含。以下变量均由调用者设置为绝对路径：`SRC` 是本目录的上级 libmvec 源目录，`RUN` 是本轮新的日期运行目录，`BUILD` 是另一新构建目录。不要覆盖或自动恢复旧运行。

`PERF_ORACLES` 由调用者设为冻结七函数 perf 头的绝对目录；不要用当前生成数据或缺失的历史路径替代。

```sh
[ ! -e "$RUN" ] || exit 1
mkdir -p "$RUN/tmp"
(cd "$RUN" && TMPDIR="$RUN/tmp" TMP="$RUN/tmp" TEMP="$RUN/tmp" \
  TEST_TMPDIR="$RUN/tmp" LIBMVEC_PERF_ORACLE_DIR="$PERF_ORACLES" \
  PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover \
  -s "$SRC/tests/host" -p 'test_*.py' >host.stdout 2>host.stderr)
```

`TEST_TMPDIR` 是部分主机测试创建临时用例目录的显式根目录，必须存在且位于本轮日期运行目录；它与 TMPDIR/TMP/TEMP 一并设置。命令仅展示用法，测试结束应核对真实退出码、失败和 skipped 项，并保留两个输出流。

主机测试使用显式文件加载，不修改全局 `sys.path`。gate、frm、rounding 和组比较的测试使用合成记录，覆盖缺失、重复、乱序、截断、伪造控制流及身份不符等拒绝规则；它们不代表真实目标已通过。旧库构建规则测试在 `host/test_oldarchive_build.py`。

MPFR 测试需要主机标准 LP64 MPFR/GMP ABI，指针及 C long 均为 64 位；已验证环境为 x86-64 Linux、MPFR 4.2.1。其他 ABI 或任意替代库需要独立验证。完整测试还需显式设置 `LIBMVEC_PERF_ORACLE_DIR` 指向冻结的七函数 perf 头目录；未设置时相关 fixture 测试显示 skipped，不能声称完整 MPFR 测试通过。不存在从任务目录层级推导输入的位置规则。

## 七函数历史门禁

`gate.py` 不构建数学库。支持 sin、cos、log、logf、exp、expf、pow 的 array/Nx × m1/m2，保留原调用长度、顺序和 chunk 相位，不安全分片。压缩输入为六个 gzip `.dat`：exp-unit、expf-unit、log-unit、logf-unit、trigvec-unit-data、powvec_unit_data。压缩封装可不同，解压内容必须匹配脚本内冻结 SHA。

```sh
python3 -B "$SRC/script/accuracy/gate.py" prepare \
  --oracle-dir "$COMPRESSED_ORACLES" --headers-dir "$HEADERS"
python3 -B "$SRC/script/accuracy/gate.py" run \
  --new-archive "$ARCHIVE" --gcc "$GCC" --scalar-libm "$LIBM" \
  --headers-dir "$HEADERS" --build-dir "$BUILD" --run-dir "$RUN" --build-only
```

`prepare` 输出共享头和 `oracles.csv`，拒绝覆盖。`--build-only` 明确选择只构建；移除该选项并显式给出 `--nemu` 才启动目标。`--functions` 可以缩小范围，sin/cos 的 array 用例必须一起选择；LTO archive 使用 `--lto`。每次重跑需两个新目录。指令/时间上限不足时应报告未完成，不得删测后标为完整通过。

冻结样本数量为 log 104965、exp 102039、expf 100608、logf 101385、pow 124238、sin/cos 各 103084。历史限制为 sin/cos/log/exp/pow 1、expf 2、logf 3 个有序编码步距。参考是 MPFR512 定向区间转换到目标格式的 RNE 位模式，没有保存未舍入真值；该步距不是严格数学真值 ULP。最大有限值与同号无穷可能仅距一步，部分零/次正规边界也沿用历史限制，不能把 pass 推广成特殊分类或五模式精度证明。

门禁保留 array 零长度、special/mixed、flags/errno、tail/canary、in-place 等历史用例；pow 在 115422 普通样本边界切分且禁止普通区段 scalar fallback；unary Nx 按实际 fallback 参数检查普通范围不得回退。Nx 探针覆盖 incoming VL=0、1、VLMAX−1、VLMAX，tu/mu、v1–v7 和 v24–v31 的逐字节哨兵、返回 vstart=0、frm=RNE，以及 TLS、errno 和 sticky flags。它采集 vl/vtype 但不要求保持；超过 vlenb=2048 或 m2 lanes=512 明确失败。最后一个 Nx 向量复制末输入填满，只统计真实 oracle lane。

门禁使用 RNE 实验设置，outer entry_rne 对五个合法 frm 验证进入 RNE 后恢复，不能据此推导最终产品舍入契约。通过还需实际 rc、真实 GOOD TRAP、无错误、完整完成行与数量、全部覆盖行、ELF/BIN/archive 身份一致。`commands.csv` 保留命令、cwd、返回状态及分离的输出，`tests.csv` 保存产物和 staging 身份，`coverage.csv` 保存函数/接口/LMUL 的预期数、实测数、限制和状态，`identities.csv` 保存依赖 SHA，`summary.md` 保存汇总。built、not-run 或部分 pass 不能表示全部通过。

BIN 独占复制到本轮 staging 后才运行，构建 BIN 与 staging BIN SHA 双重核对；运行前后含失败/超时均再检查。旧记录缺 staging 列时，不能倒填为新版运行事实。固定 corpus 通过不代表所有 ABI、VLEN 或数学全域通过。

## 小规模五 frm 观察

`frm_observe.py run` 对单个函数 Nx/array × m1/m2 × 五 frm 采集输入、输出 bits 和返回 frm/fflags/errno。模式 0–4 分别为 RNE、RTZ、RDN、RUP、RMM；样本涵盖普通、边界、零、次正规、无穷、qNaN/sNaN 和混合，不是 MPFR oracle。每次预置 flags=0 或 NV|DZ、errno=0 或 89，检查 sticky 未丢失。

```sh
python3 -B "$SRC/script/accuracy/frm_observe.py" run \
  --function log --archive "$ARCHIVE" --gcc "$GCC" --scalar-libm "$LIBM" \
  --build-dir "$BUILD" --run-dir "$RUN" --build-only
```

去掉 `--build-only` 并显式给 `--nemu` 才执行。Nx 只用 incoming VL=VLMAX；虽然共享汇编采样向量状态和保持寄存器，此观察器不检查它们，不能声称 PCS 通过。array 长度为 1、VLMAX−1、VLMAX、VLMAX+1、2×VLMAX+1，实际 VLEN 由 FRM_CONFIG 给出，拒绝错误长度、输入及缺失/重复 lane。

`compare` 接受 `--function`、`--before/--before-stderr/--before-commands`、`--after/--after-stderr/--after-commands` 和新的 `--output`。两边必须有匹配日志路径且唯一 rc=0 的 nemu 命令、stdout 的真实 GOOD TRAP 和 stderr 的 UART 完整记录。缺 FRM_CONFIG 的历史记录只能在独立确认 VLEN 后显式用 `--legacy-vlenb`，不能冒充程序测得。输出 cases 为调用数，state_diff 为返回状态有差异的调用数，value_count 为 lane 数，value_diff 为输出位不同的 lane 数；工具完成不代表差异为零或数学正确。

## 完整 corpus 五 frm 采集

`rounding_accuracy.py run` 在每函数独立 ELF 中采集全部冻结 corpus 的 array/Nx、m1/m2 和五模式；输入头 SHA 和数量必须匹配。

```sh
python3 -B "$SRC/script/accuracy/rounding_accuracy.py" run \
  --gcc "$GCC" --archive "$ARCHIVE" --scalar-libm "$LIBM" \
  --headers-dir "$HEADERS" --role candidate --functions log \
  --build-dir "$BUILD" --run-dir "$RUN" --build-only
```

只有显式给 `--nemu`、正的 `--max-instr` 和 `--timeout` 才运行，与 `--build-only` 互斥。Nx 沿用 VLMAX 分组和 incoming VL 轮转；array log/exp/logf/expf 使用长度 1,2,3,4,5,7,8,9,15,16,17,31,65,129,257，sin/cos/pow 使用 257，pow 在 115422 处精确切分。每调用先清 flags/errno 再设置模式，直接调用数学入口，无 main 数学 wrapper。严格关闭 fast math、融合、builtin、LTO 和自动向量化。返回后采样 frm/flags/errno；flags 是整次调用聚合，不是逐 lane。

此观察器没有复现完整历史门禁的 in-place、tail、零长度、PCS 寄存器、sticky-state、fallback 禁止/计数及全部 canary，不能声称门禁等价。它保留逐语义输入广播、mixed 与 Nx 四种 incoming VL；语义样本无 frozen reference，不计算参考步距。调用 flags/errno 初值与部分历史循环不同，可能影响带状态实现。

每函数先链接测 errno TLS offset 后重编译，最终 readelf 再核对；目标先验 tp/anchor/errno 地址。源码、oracle、语义源、archive、运行时、ELF/BIN 和说明复制进 snapshot 并验 SHA。GCC/binutils/NEMU/系统静态库不复制，分析仍需其记录的原绝对路径和 SHA；离线复验须另外归档外部工具。build-only 计划不能分析成已模拟。

```sh
python3 -B "$SRC/script/accuracy/rounding_accuracy.py" analyze \
  --run-dir "$RUN" --output-dir "$RUN/analysis"
```

分析复核实际 rc、唯一真实 GOOD TRAP、所有原始日志的字节数/SHA、完整 LF/CRLF 行、组序/输入/位宽/frm/TLS/覆盖和全部依赖，拒绝错误、超时、截断、缺失、重复或乱序。全部核验完成才发布 COMPLETE.csv，partial 无效。lanes.csv.gz 按每样本五行相邻，列为 function/interface/lmul/sample_id/frm/x_bits/z_bits/y_bits/ref_bits/rne_ref_ulp；C 为 corpus ID，S 为语义 ID，语义 ref 为空。groups.csv.gz 逐调用保存组号、n、真实 lane 数、incoming VL、frm/flags/errno。summary.csv、paired.csv 仍只描述到 RNE 参考位的距离；coverage.csv 和 COMPLETE.csv 给出覆盖与身份。

七函数总计 739403 个样本，2 接口×2 LMUL×5 模式为 14788060 corpus lane，另加语义数据和原始 padding；原始串口约 0.5GB/每 archive，未压缩 CSV 通常超过 1GB，需预留数 GB 和充分运行预算。没有安全跨调用分片或自动恢复。

## 四函数限额 Nx RNE 与真值门禁

四函数的精度采集同样由框架提供 `main()`。`--nx-only --samples N` 真正调用 atan、atan2、acos、log10 的 unmasked Nx m1/m2，只采 RNE；不调用 array、fixed 或 masked 入口。`N` 是每接口、每 LMUL、每函数的冻结语料采样数；`C<i>` 对应第 `floor(i×1024/N)` 条。`atan2` 以 `y,x` 顺序传入两个参数。冻结头的再生成见[四函数 MPFR 参考头](newmath/README-mpfr-accuracy.md#复现四函数冻结-rne-参考头)。

```sh
FUNCS=atan,atan2,acos,log10
python3 -B "$SRC/script/accuracy/rounding_accuracy.py" run \
  --gcc "$GCC" --archive "$ARCHIVE" --scalar-libm "$LIBM" \
  --headers-dir "$HEADERS" --role candidate --functions "$FUNCS" \
  --nx-only --samples 16 --build-dir "$BUILD" --run-dir "$RUN" --build-only
```

`ARCHIVE` 和 `LIBM` 都必须是真实静态归档。`LIBM` 应为工具链的标量归档（例如 `libm-2.44.a`），不能是会连带引入安装版向量库的 `libm.a` 链接脚本。

正式采集需要另一次明确的 NEMU 运行和完整分析。分析产生完整证据后，可使用同一语料和采样数验证真值误差：

```sh
python3 -B "$SRC/script/accuracy/mpfr_accuracy.py" "$ANALYSIS/lanes.csv.gz" \
  --capture-complete "$ANALYSIS/COMPLETE.csv" \
  --oracle-dir "$HEADERS" --profile unit --functions "$FUNCS" \
  --interfaces Nx --lmuls 1,2 --samples 16 --max-ulp 1 \
  >"$VERIFY/truth.csv" 2>"$VERIFY/truth.stderr"
```

`VERIFY` 为另建的日期时间目录。MPFR 对每条有限输出证明真值 ULP 误差区间：上界 ≤ 阈值才通过，下界 > 阈值即失败，未决不算通过。`--capture-complete` 验证采集交接身份，不是第二次独立执行；code point 距离、异常分类和语义采样不能替代特殊值契约、完整 PCS 或其余接口精度验收。

## 独立 MPFR 数学误差分析

`mpfr_accuracy.py` 只处理已有真实目标输出。输入为 CSV、csv.gz 或 stdin（-），表头与 lanes 一致，rne_ref_ulp 可选但只校验格式。输入/参考/输出是 binary32 恰好 8 或 binary64 恰好 16 个十六进制位；pow 有 z，一元 z 为空。同一函数/接口/LMUL/sample_id 必须五行相邻且模式各一次、输入参考一致；各块不可重开，不可缺失、重复或空输入。

```sh
python3 -B "$SRC/script/accuracy/mpfr_accuracy.py" "$LANES" \
  --oracle-dir "$HEADERS" --profile unit \
  --functions sin,cos,log,logf,exp,expf,pow --interfaces Nx,array --lmuls 1,2 \
  >"$RUN/summary.csv" 2>"$RUN/analysis.stderr"
```

unit 使用完整冻结单元语料，perf 使用每函数 1024 条冻结性能输入。必须显式选择对应 oracle-dir；分析器核对硬编码 SHA、每个 C ID 的 x/z/ref 和完整笛卡尔积。S 样本以 semantic_S_unvalidated 单列，不冒充语料覆盖。gzip 记录压缩字节身份，stdin 无输入字节身份。

工具从 IEEE 位精确重建输入，用 MPFR RNDD/RNDU 得真值区间，默认尝试 512/1024/2048 位；未决不能算通过。不使用 Python 浮点或把 MPFR_RNDA 当 RMM。各模式相对实际 RNE 输出的绝对数学误差，以精确输出中点和真值区间判 closer/tie/worse/unresolved。真值 ULP 的正规分母为 2^(floor(log2(|truth|))-fraction_bits)，次正规、极小和零用最小次正规间隔；分箱 zero、le_0.5、le_1、le_2、le_4、gt_4 需完整区间证明，跨 binade 或阈值未决单列。

参考只在真值区间两端转 RNE 一致时判 match；已证 mismatch/zero_sign_mismatch 非零退出，并在 stderr 流式输出每项 REFERENCE_MISMATCH。rc=0 不表示所有参考已证明或所有目标结果正确。报告必须保留 truth、reference、comparison_eligibility、特殊值、MPFR 范围异常和 unresolved 分母。有限真值却输出 Inf/NaN 单独统计。数值误差中 ±0 都为零，符号分类不证明跨 frm 零符号语义；NaN 分类不验 payload/quieting。此分析不验逐 lane flags/errno、sticky、frm 恢复、PCS 或完整接口语义。

## 两库逐 lane 与组状态比较

`rounding_accuracy.py compare --baseline "$BASE_ANALYSIS" --candidate "$CAND_ANALYSIS" --output "$NEW_CSV"` 核对双方输入、参考、组序、VLEN 和已记录环境，要求 archive SHA 不同，其他已记录源码、oracle、工具、预算和超时相同；保守地连生成头的绝对路径不同也可能拒绝。未记录的加载器/系统/模拟器内部配置不在证明范围，不能仅凭比较将差异归因于 archive。

`rounding_group_compare.py --baseline "$BASE_ANALYSIS" --candidate "$CAND_ANALYSIS" --output "$NEW_CSV"` 独立读取原 COMPLETE 和 lanes/groups/summary/paired/coverage 五文件，校验角色、顺序、覆盖、VLEN、SHA 和 observer/environment 一致；无需导入或运行旧观察器。完整流式核对双方及五模式的每个 x/z/ref 和真实 lane，逐组比较 frm_after、fflags、errno。默认只输出有状态差异的 group 行及全部 summary，可用 --all-groups 输出所有组。groups_compared 为组数，三种状态 differences 分别计数，any_state_differences 是至少一项不同的组数而非三者之和，lane_bit_differences 是真实输出位差异数而非 ULP。输出独占创建，校验通过后硬链接原子发布，partial 无效。

组比较不重验原始 UART/NEMU，不能独立复验未导出的 Nx padding；SHA 是身份校验，不是签名或串口传输正确性证明。清零起始的组级状态不是 sticky/errno 保留或逐 lane 状态验收，差异也不自动判定哪方正确。

## 迁移与冻结身份

迁移保留 oracle SHA、样本数、阈值、调用分组和数据 schema。移动源码和修改路径会生成新的脚本/源码身份，不应改写任何既有 manifest、COMPLETE 或原始日志的 hash。rounding analyze/compare 要求执行 observer SHA 与对应 run 快照一致；旧 run 继续使用其 snapshot/observer 中的原脚本，新运行使用当前脚本，不允许调用者通过覆盖旧 hash 绕过校验。组比较器可独立处理满足原 COMPLETE 规则的旧结果。历史 checksum 清单保留其历史提交归属，不能当成当前新布局的通过凭据。
