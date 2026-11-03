# rvv-bench 后续 benchmark 审查

范围：只读审查综合性能报告、旧构建输入、当前工作区实现与 latency 工具。未修改源码，未编译、未运行仿真。当前工作区有多处用户修改；以下入口以本次读取到的工作区内容为准。综合结论：优先修复测量可信度；现有常规 runner 和生成器的矩阵粒度不足以表达链数/LMUL/VL、索引模式或 mask 分布，建议把新实验实现为显式 workload case，而不是暗中增加传统宽表的列。

## 已验证纠正：旧宽表不能直接代表峰值吞吐

2026-11-03追加：直接vfmadd.vv e64,m1,VL2测试已完成1/8/16/24链、16/64/256循环扫描，36个RTL样本全部差分PASS。24链最长raw吞吐3.981 inst/cycle、长度差斜率4.000 inst/cycle；8链仅约1.96 inst/cycle。因此旧m1八链CPI约0.53不能作为硬件峰值上限。详见[实际测试及原始数据](logs/p1-vfmadd-vv-20261103/report.md)。

这里不是指vfmadd可以消除自身隐式读vd：每条累加链有真实反馈RAW，吞吐测试需要足够多独立链隐藏反馈延迟，并扫描至饱和。传统gen.S的普通m1/m2/m4/m8累加器池只有8/4/2/1条；宽化MAC因main.c已按最大EMUL选择下一档模板，实际链数为4/2/1，不能无条件当峰值。普通非累加纯写vd指令则不能仅因目的重用就判定RAW。

另：传统randomize将vector和FP标量初始化为all-ones，即浮点NaN输入。该宽表测得的是这些数据和依赖模式下的workload CPI，不能自动当正常有限数的峰值，尤其div/sqrt等可能有特殊值路径。新P1有限值FMA与旧NaN宽表不是同一刺激，不能只归因链数而忽略输入差异。

当前未全面重写旧宽表，不宣称所有opcode已消除依赖。旧冻结批次不改动，峰值结论应使用明确链数、有限输入、长度扫描及正确性检查的专门用例。此前对宽化/窄化非法对齐的审查猜测已撤回：main.c的最大EMUL调度使真实调用模板对齐合法，不能用未被该vtype调用的函数展开判错。

## P1：先消除已知测量混杂

### 1. 标量修复后独立重测

旧 QUICK 综合报告指出 `add/mul` 镜像含标量 RAW，16 行被明确标为不可信；不要用旧结果作为 latency/throughput 基线，也不要覆盖冻结批次。当前 `instructions/rvv/gen.S:222-226` 已将标量源移到固定 `a5/a6`，并说明不与目的轮换寄存器冲突；`randomize` 在 `gen.S:797-799` 初始化这些固定源。用该修复之后的独立构建/批次分别跑 `add`、`mul`，检查 ELF 反汇编确认源寄存器固定且无意外 loop-carried RAW，再与旧值并列而非覆盖。报告构建提交、dirty 状态、工具链/镜像、WARMUP/UNROLL/LOOP/RUNS、逐 run cycles 和哈希。吞吐比较与 latency 结论分开，避免单一 CPI 掩盖计时开销。

### 2. FMA 独立累加链 1/8/16/24

报告的 vfmadd e64m1 CPI≈0.53 来自每 8 条才轮换目的寄存器的循环链；这是依赖受限数据，不能据此估算峰值。吞吐版固定 e64m1 与有限、稳定的正常数，分别分配 1/8/16/24 个互不重叠 accumulator；每条链使用不变的乘数输入和独立 `vd`，保证只有各自的旧 `vd` 累加 RAW，不发生不同链的寄存器组重叠。分别测 `vfmadd.vv` 与 `vfmacc.vv`，以及可选 `.vf` 形式；前两者旧 `vd` 扮演的运算角色不同，不得推定反馈延迟相同。跨链数保持目标动态指令总数相同（增加 loop repeat，或以总目标指令数归一），并做至少多个测量长度拟合 `cycles = fixed + N*CPI`；逐 run 保留。另测单链延迟以区别 latency 和多链吞吐。检查汇编/反汇编计数、链依赖图及最终 accumulator 正确性。当前 latency 工具不是这个实验的替代品：`latency_gen.py:317-340` 仅生成 chain 长度 4/16/64 的 RAW 模板，`latency_main.c:33,87-101` 只枚举这些长度；其通用直接模板 `latency_gen.py:140-151` 对部分 FMA 编码为同目的链，属于 latency 测量方向，不是独立 accumulator 吞吐矩阵。

### 3. Load 以 EMUL 匹配轮换目的寄存器

报告指出 `vle8/e64m8` 的数据 EMUL=1，而旧旋转依据 LMUL=m8，只保留一个目的寄存器位置；`tu` 因而可能测成旧目的合并反馈链。控制实验要使数据 EMUL 相同，并将固定 `vd` 与足够轮换的、不重叠目的组作为配对变量。最关键配对：`vle8/e64,m8`（EMUL1）轮换 v8..v15，对照 `vle8/e8,m1`（EMUL1）轮换 v8；两者相同 EEW、EMUL、VL/载荷，另对固定 vd 复测。再扫若干合法组合验证普遍性。每个用例先初始化 vd 为非全 1 哨兵；`tu` 后检查未写元素保持，`ta` 只对非 agnostic 活跃元素断言。分开 VL=1 与 VL=VLMAX、ta/tu，并避免目的组重叠。注明单位是目标指令 CPI/活动字节而非 DRAM 带宽。当前旋转主体在 `gen.S:668-706`；`main.c:92-100` 将 load 的轮换组由所选 LMUL 索引，未按 EEW/SEW 计算 EMUL，是需要重审的入口。

### 4. VL=1 扫 LMUL，固定动态指令数

先用无 RAW 的 `vadd.vv`（以及对照 `vmul.vv`）固定 `VL=1`，扫所有合法 LMUL mf8..m8；另固定 SEW、源寄存器/数据，保持定时区目标指令总数恒定，不能让不同代码体的展开指令条数随 LMUL 改变。输出实际 `vl`、`vtype/vl` 读回、LMUL、动态目标数、汇编静态指令/uop（若模型可计）与 cycles。使用独立目的轮换避免 WAW/RAW，并设置较长多个测量长度减轻边界影响。报告建议也记录 split/issue/writeback 计数；这些是实现诊断，不能从单纯 CPI 推断。常规 main 目前仅 `vlarr={0,1}`，但把所有配置压入 e8..e64 × LMUL 宽表，且只输出 CPI（`main.c:40-47,72-116,150-166`）；需给 VL1-LMUL 专项保留 case 级动态指令数与每 run 结果。

## Indexed memory 与 sparse mask 设计

### Indexed load/store 覆盖矩阵

当前生成器明确写有 `/* TODO: indexed load/stores */`（`gen.S:539`）；已有 `m_loadstore` 仅生成 unit-stride `vle/vse`（`gen.S:126-138,454`）及 strided `vlse/vsse`（`gen.S:467`），segment 也只有 unit/strided（`gen.S:469-491`）。`insns.list` 当前不含 `vluxei* / vloxei* / vsuxei* / vsoxei*`。不要只补 opcode 清单：需要 `m_bench_all` 生成模板、EEW 类型 setup、指针表/名称/内存标志以及合法性过滤。

- 覆盖四个 indexed 家族：unordered load `vluxei{8,16,32,64}.v`、ordered load `vloxei...`、unordered store `vsuxei...`、ordered store `vsoxei...`。区分 ordered/unordered，不以一种代替另一种；mask 变体由既有 `m_mask` 模式产生。
- data EEW 取实现/ELEN 支持的 8/16/32/64；index EEW 独立取 8/16/32/64，并显式记录。检查 `EMUL_data=LMUL*EEW_data/SEW`、`EMUL_index=LMUL*EEW_index/SEW` 均落在合法范围（不大于 8，且 index vector 的 LMUL/EEW 编码合法），以及寄存器组不超 v31、不与 vd/其他操作数非法重叠。保留不支持组合为空/skip reason，不能静默删去。
- VL 覆盖 1、短中间 VL（例如 VLMAX/2，边界取整并记录实际 vl）、VLMAX。每行记录 AVL 与 `vl` CSR 实际值，不能只记请求值。
- index 模式至少包含：连续/单位偏移、固定重复同一地址、随机但可复现且唯一、随机重复（冲突/热点）、跨 cacheline/页但在已分配缓冲区范围内。loads 同时记录地址工作集/是否热重复；stores 的重复 index 会产生 WAW，作为单独语义 case 而不是吞吐默认值。偏移按字节定义，按目标 EEW 对齐，检查所有生成地址在 buffer 边界内。
- 正确性：load 用索引编码可识别的数据（如每位置唯一值），逐元素比较活动元素；masked-off 元素按 mask/tail agnostic 规则比较，不错误要求固定值。store 在每次样本前填充 canary，核验被索引且活跃的目标字节变更、非活动与越界周边不变；唯一 index 作为吞吐主结果，重复 index 的 last-writer/ordered 语义专测。随机种子必须进入 manifest。
- 防污染：load 的地址 index、目的 vd、index 向量、mask 寄存器、base/stride 各自分离；不将 load 结果用于下一条地址计算（否则变成 load-use latency）。多个并发 load 使用不同目的组轮换，EMUL 匹配；store source 与 index 分离。消除 store-to-load RAW、同地址 WAW（除专门 alias 模式），但验证时用独立检查阶段，不把校验指令计入计时。

### Sparse mask 分布及成本拆分

常规 `gen.S:m_mask` 只产出无掩码和 `v0.t` 两种形式（`gen.S:40-43`），`randomize` 将 v0 置全 1（`gen.S:781-796`）；`main.c:150-166` 遍历 tail/mask policy 而不枚举 mask 内容。因此当前所谓 masked 性能基本是全开 mask，完全没有稀疏 mask 覆盖。

对 arithmetic、indexed/unit load、store 分别建 mask pattern case：全 0；全 1；单个/少量连续 active（首/中/尾）；确定性伪随机 p=1/8、1/2；固定分组/交错（如每 8 个 active 1 个）。将 `VL` 和 active count/比例作为字段，特别注明 pattern 在 VL 内的有效位，不让 VL 外 mask 位干扰。每种 mask 固定 seed/bitset；计数实际 active elements，结果同时报 cycles/issued target instruction、active elements/cycle 和 active bytes/cycle，不能用稀疏后的“有效吞吐”掩盖 mask 扫描仍消耗的成本。全 0 专门揭示早期 predicate skip 与仍扫描/发射的成本。

分成两类基准：A) 预先构造 mask、只计 timed body，报告 mask 已就绪时每 active element 吞吐；B) mask 建立基准（生成/加载 pattern、predicate 算术/viota 等所需成本），独立报告 cycles/element 和 setup 指令数。另可给 end-to-end 合计，但不得把 B 的 mask 构造摊进 A 后称作指令本身吞吐。每次计时前后核验 mask 内容/活跃数；使用独立 mask 来源寄存器，避免被目标指令覆盖，也避免目标 vd 与 mask 组/索引向量交叠。masked-off 的目的值允许符合 ISA agnostic 规则而非强制某常量；对 active 元素严格校验输出。不同 tail/mask policy 单独记录。

## 当前 runner、生成器与数据管道限制

- `instructions/rvv/Makefile:45-57` 的 `insn`/`insn-forms` 按 opcode/form 构建单独 ELF；runner 命令是外部 `run_emu.sh`，本仓 `run.sh` 则配置 QEMU 用户态，不是该 gsim runner。runner 通常以测试名归档结果，不能原样传 workload 维度。
- `insns.list` 提供粗粒度指令前缀，实际 forms 从当前 `gen.S` 经 m4/CPP 提取（Makefile:9-13,64-68）。manifest/build-manifest 描述编译定义、二进制路径/hash/测试名，不包含 VL/LMUL/EEW/EMUL/index/mask case。本批 manifest 是 513 个 opcode/form 测试；这是冻结旧批次，不代表当前工作区新生成输入。
- 传统主程序每个指令输出一个宽表行，SEW×LMUL 是静态列，策略/AVL 仅由固定循环枚举；RUNS<=4 时直接均值，逐 run cycles 不输出（`main.c:101-116`）。`extract_csv.py` 位于已归档批次目录，不是通用当前工具；它将宽表固定解析为 instruction/vl/policy/alignment + 配置列，并从生成汇编 `bench_names`/`bench_is_memory` 推 coverage，期望每组固定 8 行（`logs/gsim-instructions/quick-20261103-015313/extract_csv.py:22-28,98-118,212-237`）。新增 case 不登记在旧 extractor 时会被判缺失/无法覆盖。
- latency 工具自动生成的 form census 仍来自 active `gen.S`，但只覆盖保守 RAW 模板：e32,m1,VLMAX，ta/ma 与 tu/mu，链长 4/16/64（Makefile:62-80；`latency_main.c:83-118`）。load 模板只接受 unit/strided 基础形式和 vlseg2（`latency_gen.py:107-115`），将载入值抽到标量再做地址反馈（`latency_gen.py:213-219`），这刻意测 load-to-use latency，不是 indexed memory throughput。stores 一律跳过（`latency_gen.py:107-108`）；indexed 不会被当前生成器识别为可测形式。
- `latency_main.c` 的 `body_insns` 是模板语义计数（kind 1/2/3 各目标加辅助指令；kind 4 每步 3 条），但 CSV 没有逐条 case 配置、mask 内容/seed、ELEN/EMUL 或状态字段。不要把 latency CSV 和常规 CPI CSV 混用。

## Manifest、coverage、CSV 的建议扩展

用显式 case manifest：每项包含稳定 `case_id`、opcode/form、operation class、SEW/LMUL、data EEW 与 index EEW、计算所得 EMUL、AVL/实际 VL、ta/tu 与 ma/mu、mask pattern/seed/active count、index pattern/seed/address working set、目标循环/UNROLL/RUNS、dynamic target/body 指令数、expected output/hash、预期结果状态、构建 ELF 与源/工具链/镜像哈希。unsupported、build-fail、runtime-fail、timeout、pass、invalid-stimulus 分开编码。

覆盖审计必须从 case manifest（而不是仅从 `.S` 字符串表）计算期望组合，并逐 case 对照运行事件、CSV 样本数与正确性状态。报告缺失 case、意外 case、重复 key、partial run、不同原因的 skip；支持组合留空原因需结构化字段，不能当作 0 CPI。宽表保留给传统矩阵；新增 P1、indexed、mask 数据用长表，一行一 case × sample，字段包含上述参数、cycles、normalized 指标、dynamic instruction count、seed、校验结果与 provenance。版本化 schema，旧 CSV/extractor 仍可复现冻结批次，不要就地重解释旧的 33-field 输入或已发布结果。每批保存 immutable manifest、binary/source hashes、命令行、退出记录、原始逐 run 日志及 extractor/schema 版本。

## 代码入口索引

- 综合结论与建议实验顺序：`logs/gsim-instructions/quick-20261103-015313/performance-report.md:87-94,117-127,210-218`。
- 常规 form/策略生成：`instructions/rvv/gen.S:40-43,126-138,221-240,454-491,539`。
- LMUL 旋转与固定动态体：`instructions/rvv/gen.S:656-766`；测试初始化和 wrapper：`gen.S:781-840`。
- 配置合法性/EMUL 选择：`instructions/rvv/main.c:72-100`；矩阵与 CSV：`main.c:40-47,101-116,150-166`。
- 指令 census/build 入口：`instructions/rvv/Makefile:9-13,45-80`；候选形式清单：`instructions/rvv/insns.list:93-105,162-189,262-340`（当前缺 indexed forms）。
- RAW generator 分类/模板/输出：`instructions/rvv/latency_gen.py:74-126,129-219,229-277,280-343`；runner matrix 和 CSV：`instructions/rvv/latency_main.c:33-34,67-101,114-118`。
- 旧批次 coverage schema：`logs/gsim-instructions/quick-20261103-015313/extract_csv.py:22-28,98-118,212-237,244-270,283-319`。
