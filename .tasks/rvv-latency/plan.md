# RVV 指令延迟测试

状态：实施中
日期：2026-10-11

统一约束：所有延迟测试（含 warmup、控制链和验证运行）均使用当前合法 SEW/LMUL 对应的 `vl=VLMAX`，不引入缩短 VL 的测试模式。

## 1. 目标

在 `instructions/rvv` 中新增独立的 RAW latency 测试路径。普通向量结果和 mask 结果分别构造直接依赖链；向量到 mask、load 到地址分别通过明确的中介指令形成闭环。在链前后使用 `csrr cycle` 读取周期，输出原始周期数据，并区分直接链与组合路径。

本任务不改变现有吞吐量测试。现有测试使用寄存器轮换，latency 测试必须使用单独的生成宏、运行函数和输出模式。

所有向量 store（包括 unit-stride、strided、segment、indexed、mask 和 whole-register store）明确跳过，不为其设计延迟测试；这不限制计时区间外用于初始化测试数据的 store。

## 2. 第一阶段范围

### 2.1 纳入条件

一条指令同时满足：

1. 写入普通向量寄存器组（不是标量寄存器，也不是 mask 目的寄存器）。
2. 至少读取一个普通向量寄存器组。

因此第一阶段不按 `.vv` 后缀筛选，至少包括：

- `vadd.vv`、`vadd.vx`、`vadd.vi` 以及对应的整数 ALU、乘除、饱和、移位形式；
- `vfadd.vv`、`vfadd.vf`、FMA、比较之外的浮点向量目的指令；
- `vfsqrt.v`、`vfrec7.v`、`vfrsqrt7.v` 等单向量源指令；
- `vcpop.v` 等 Zvbb 单向量源、向量结果指令；`vcpop.m` 是 mask 到标量结果的另一条指令，不能混淆；
- `viota.m` 作为 mask-to-vector 中介链单独纳入，具体见第 10.4 节；
- `vsext.vf2/vf4/vf8`、`vzext.vf2/vf4/vf8` 和其它向量加宽/窄化指令；
- `vrgather`、`vslide*`、`vcompress.vm`、`vmerge` 等读向量并写向量的排列/合并指令；
- reduction、carry/merge 等只要目的仍是普通向量寄存器且存在普通向量源操作数的形式。
- `vcpop.m`、`vfirst.m` 作为 mask-to-scalar 中介链单独纳入，具体见第 10.3 节。

mask 到 mask 的逻辑及单源前缀指令追加到直接 RAW 链范围，具体模板见第 10 节。向量到 mask 的比较/carry 指令使用第 10 节的中介链，不冒充普通向量到向量直接链。

### 2.2 排除条件

- 不属于第一阶段普通向量直接链的标量结果：`vmv.x.s`、`vfmv.f.s`、`vcpop.m`、`vfirst.m` 等；其中 `vcpop.m/vfirst.m` 由第 10.3 节单独覆盖；
- 只从标量/立即数读取并写向量：`vmv.v.x`、`vmv.v.i`、`vmv.s.x`、`vfmv.v.f`、`vfmv.s.f`；
- 无可串联数据源的 `vid.v`；
- 只从内存读取并写向量、但没有向量源操作数的 load/segment-load；
- store、segment-store 以及其它没有向量目的结果的形式。

load 按第 9 节独立模型处理，store 明确跳过；其它缺少独立模型的形式按第 11 节记录。某条指令作为其它模型的中介或初始化指令出现，不代表该指令自身的延迟已被覆盖。

## 3. 依赖链模型

- 普通二元指令：把上一条结果接到一个向量源操作数，另一个源保持固定，例如 `vadd.vv v8, v8, v16`；标注串联的是哪个源端口或 old-vd 路径。
- 单源指令：形成合法 `vd -> vs` 链，例如 `vfsqrt.v v8, v8`；所有 `vmv*`/`vfmv*` move 指令明确不测。
- `vi/wi` 普通向量结果形式按第一阶段直接串联，例如重复 `vadd.vi v8,v8,1`，立即数不变；不按后缀覆盖无向量输入的 `vmv.v.i`。
- `vx/wx` 普通向量结果形式固定 GPR，例如重复 `vadd.vx v8,v8,t0`；`t0` 在整条链和计时区间内不变，不串联或插入更新 GPR 的指令。
- `vf/wf` 普通向量结果形式固定 FPR，例如重复 `vfadd.vf v8,v8,ft0`；`ft0` 不变，仅串联向量结果，不测标量源依赖。
- 输出为 mask 的 `vmseq.vi/vx`、`vmfeq.vf` 等仍按第 10 节中介链处理，不能仅凭后缀归入直接向量链。
- mask、widen/narrow、LMUL 变化和寄存器组重叠必须按 ISA 合法性建立专用模板；不能机械地把所有形式改成 `vd=vs`。
- 对无法形成纯向量 RAW 链的形式，记录为“待专用模型”，不得混入普通 latency 结果。

无合法源目的重叠的指令通过合法寄存器组交替串联；width-changing 指令明确记录生产者结果与消费者实际读取的寄存器/位宽，不插入改变 VL 的指令。

链长默认 `4、16、64`，作为可配置构建参数；一个测试程序依次运行三种链长。链长表示串行目标指令数，不能与外层重复次数混淆。

## 4. 参数矩阵

- 默认 `SEW=e32`、`LMUL=m1`；所有配置固定 `vl=VLMAX`，改变 SEW/LMUL 时重新计算对应 VLMAX；
- 对默认配置不合法的扩展/加宽指令选择 ISA 要求的合法 SEW/LMUL（如需要时 e64），仍固定对应 VLMAX，并记录配置例外，不缩短 VL；
- `(ta,ma)` 必须启用，默认 tail/mask 策略为 `(ta,ma)` 和 `(tu,mu)`，其它两组通过参数开启；
- `vrgather`、`vcompress`、`vslide*` 后续增加 LMUL 维度；
- reduction 后续增加 SEW 和 LMUL，始终使用各配置的 VLMAX，并记录实际 VL 以解释不同工作量下的延迟；
- 保留策略组合，但不为观察尾部行为而缩短 VL；VLMAX 下没有尾部元素的指令不能把 `ta/tu` 对比解释为隔离的尾部策略效应；
- 第一阶段优先覆盖可形成合法纯链的形式，不以“生成了汇编”代替依赖合法性检查。

## 5. 计时与输出

- latency 路径在 `gen.S` 中生成独立函数，不修改现有 `run_bench`；
- 使用统一 warmup、外层重复次数和 `csrr cycle` 起止边界；
- 每个配置输出原始周期，不在 C 侧直接除以指令数；计时区间按吞吐量测试的结构包围在 `LOOP` 个外层迭代和每轮 `UNROLL` 个串行链之间。
- 每个子测试函数在计时前执行 `WARMUP` 轮完整链；`WARMUP`、`UNROLL`、`LOOP` 和样本数均可在 latency 构建时配置，默认值分别为 `1000/8/64/8`，`QUICK=1` 时为 `16/8/16/2`。
- `chain_len` 一律统计目标指令数；直接链主体为 n 条，mask 比较中介链为 2n 条，load 中介链为 3n 条，中介指令数不混入目标指令分母；
- 同时输出等长 baseline，用于后处理剥离计时、循环和入口固定开销；
- CSV 至少包含：
  `instruction,mode,sew,lmul,vl,tail_policy,mask_policy,warmup,unroll,loop,chain_len,sample,cycles,body_insns`；
- `body_insns` 统计计时区间内的完整展开主体，即 `chain_len * body_multiplier * UNROLL * LOOP`；预热指令不计入该字段。
- `mode` 至少区分 `vector_direct`、`mask_direct`、`vector_to_mask_bridge`、`load_to_address_use` 和控制链；记录 `mediator` 与具体生产者/消费者路径；
- 浮点指令使用确定且非特殊的初始化数据，不能把全 `1` bit pattern 当作通用浮点输入。

## 6. 验证

1. 反汇编检查每一条相邻指令是否真正消费上一条向量/mask 结果；`vx/vf/vi` 的固定操作数不得被链体修改。
2. 检查 `4/16/64` 周期随链长近似线性增长，并使用斜率估计每条指令的延迟。
3. 检查 baseline 与 chain 的计时边界、外层循环和重复次数一致。
4. 检查 widen/narrow、mask、LMUL 变化的寄存器重叠均符合 ISA 约束。
5. 确认现有吞吐量 CSV、完整 `rvv` 目标和逐指令构建行为不回归。
6. 修改构建参数后使用 `make -B`，避免复用旧生成物。
7. 检查生成的 latency 目标完全排除 store，且不存在独立指令重复写同一目的寄存器却被误算为 RAW 链的情况。

## 7. 实施步骤

- [x] 按当前 m4/CPP 和 ISA 配置初步盘点测量模型与剩余形式（见第 11 节；不是已实现验收）。
- [ ] 冻结包含 masked 变体及具体依赖端口的用例清单。
- [ ] 为普通算术（含 vi/vx/vf）、单源、widen/narrow、permute 和 mask 直接链建立合法模板。
  - [x] 修正 `vslideup`/`vslide1up`/`vfslide1up` 与 `vrgather` 的目的-源寄存器非重叠约束；这些形式使用 `v8`/`v16` 交替构造 RAW 链，slide/gather 的立即数使用 1 以保证 VLMAX 下实际消费向量数据。
- [ ] 增加向量到 mask 的 masked-vadd 中介链及组合路径输出。
- [ ] 增加 `vcpop.m`/`vfirst.m` 的 mask-to-scalar `vmseq.vx` 交替闭环，并区分组合结果与差分估计。
- [ ] 增加 `viota.m` 的 mask-to-vector `vmseq.vi` 交替闭环。
- [x] 增加可配置链长和 latency 专用运行入口。
- [x] 为每个 latency 子测试增加 `WARMUP`、`UNROLL`、`LOOP` 包装，并让 `RUNS/LATENCY_RUNS` 控制样本数。
- [x] 增加独立 latency ELF、4/16/64 链长、原始 CSV 和基础 mode/body_insns 字段。
- [ ] 先用 `e32m1/VLMAX/(ta,ma)`、`(tu,mu)` 验证链路，再扩展特殊维度。
- [ ] 完成反汇编、CSV 解析和链长线性检查。

## 8. 非目标

- 第一阶段不测纯 load/store latency；
- 所有 `vmv*`/`vfmv*` move 指令不作为 latency 目标；
- 所有向量 store 从本任务中排除，不列为后续延迟测量工作；
- 不把 mask 中介链、标量目的和访存组合路径混入普通向量 RAW latency；
- 不改现有吞吐量计时公式、`bench-all.sh` 或完整 benchmark 的默认行为；
- 不以单个链长的 `cycles / n` 作为最终延迟结论。

## 9. 第二阶段：load-to-address-use 访存依赖链

第二阶段统一使用“load 结果提取为 offset，再与固定 base 相加生成下一次地址”的三指令组合路径，不把结果命名为纯 load latency：

```text
vload -> vmv.x.s -> add(base, offset) -> vload
```

### 9.1 统一模型与初始化

- 固定 base 寄存器 `s0 = B`，选择可表示的 offset `d`，初始地址 `t0 = B + d`；
- 在计时区间外初始化内存，使每次 load 后 `vmv.x.s` 提取出的值恰好为 `d`；不再要求加载的数据能容纳 XLEN 宽的完整指针；
- 首版取 `d=0`，将合法访问区域清零，形成 `B -> 0 -> B` 闭环；即使值为 0，也必须通过真实寄存器 `t1` 传递，不能把 `add` 改成独立的地址重置；
- 普通 load 示例（vtype 的 SEW 与示例 EEW 对齐）：

```asm
# s0 = B, t0 = B, memory initialized to zero before timing
vle8.v     v8, (t0)
vmv.x.s    t1, v8
add        t0, s0, t1
```

- 每次下一条 load 的 base 必须直接来自前一轮 `add` 的结果；stride、index 和 base B 保持固定；
- `chain_len` 表示 load 数；每个 load 配套一条 `vmv.x.s` 和一条 `add`，展开主体 `body_insns=3*chain_len`，外层循环/计时开销另记；
- 链长仍默认 `4/16/64` 并可配置，在一个用例中依次运行。

### 9.2 覆盖家族与结果提取

- `vle{8,16,32,64}.v`：从目的寄存器提取第 0 个元素；首版 `SEW=EEW`，使小 EEW 也可以直接使用统一模板；
- `vlse*.v`：同一三指令链，固定 VLMAX 和 stride 寄存器；完整初始化 VLMAX 下所有正/负 stride 访问地址，stride=0 单独标记；
- `vlseg*.v`、`vlsseg*.v`：从第 0 字段的首寄存器（如 `v8`）提取第 0 个元素；遵守 `EMUL*NFIELDS<=8`、寄存器组对齐和编号范围；其它字段/元素的访问区间也必须合法；
- `vlm.v`：从打包 mask 的低位提取 offset；首版 `SEW=8, LMUL=m1, vl=VLMAX`，保证低 8 位由本次 load 写入；这是 mask-load-to-scalar-address 路径，单独标记模式；
- indexed load：同一 base 依赖框架可复用，固定合法 index 向量；提取宽度按数据 SEW，而不是 opcode 编码的 index EEW。实际生成模板仍待补充；
- fault-only-first load：仅在无 fault 且 VL 保持 VLMAX 的条件下复用框架；发生 fault/VL 缩短时报告失败，不生成常规周期样本；
- whole-register load 另列为待专用模板；所有向量 store 明确排除，不再列为待设计测量模型。

### 9.3 SEW、EEW 与有效数据约束

- `vmv.x.s` 读取当前 SEW 宽的第 0 个元素，而不是按 load 的 EEW 读取；`SEW<XLEN` 时符号扩展，`SEW>XLEN` 时取低 XLEN 位；
- 对普通 load 若要测 `SEW!=EEW`，必须保证低 `min(SEW,XLEN)` 位全部由本次 VLMAX load 写入，并按实际打包布局初始化；不能调整 VL 或依赖旧目的值/agnostic 尾部位来满足提取条件；
- `vlm.v` 实际加载 `ceil(VLMAX/8)` 字节，且目的尾部始终 agnostic，与 `ta/tu` 设置无关；若使用 e32 提取，需要当前配置的 VLMAX 保证至少加载 4 字节，不满足则跳过该配置，不调整 VL；
- 不在每个三指令单元中插入 `vsetvli` 切换提取 SEW；若某个矩阵配置无法满足有效位约束，则跳过并报告原因；
- mask load 的 LMUL 不代表多寄存器 mask 结果；其它 masked load 必须保证地址 offset 所在元素实际 active；
- 非零 offset 必须满足提取后的符号扩展/截断规则及访问区间约束；默认零 offset 可避免不同宽度的地址闭环分歧。

### 9.4 中介指令控制链

控制模式同样包含三条指令，并保持首尾真实数据依赖：

```asm
# control only: s0 = 0, t0 = 0
vmv.v.x    v8, t0
vmv.x.s    t1, v8
add        t0, s0, t1
```

控制模式使用零 base，使地址加法的结果继续成为下一轮 `vmv.v.x` 的源；不能让控制链的 `add` 脱离 RAW 链。load 链与控制链使用相同的 vtype、链长、warmup、重复次数和计时边界，原始周期分开输出。

两者斜率之差只表示用 load 替换向量 move 后的组合路径差异，不是纯 load latency，也不等于单独测出了 `vmv.x.s` 或 `add` 的延迟。

### 9.5 参数与测量边界

- 所有 load 链、控制链、warmup 和闭环验证均固定 `vl=VLMAX`；不生成额外的短 VL 用例；
- `tama` 必须存在，默认启用 `tama/tumu`，其余策略可配置；`vlm.v` 可记录两组配置但不能宣称覆盖了 tail-undisturbed 行为；
- segment load 增加合法 LMUL 维度，indexed load 后续增加 SEW/LMUL；普通 load 首版 SEW 与数据 EEW 对齐，`vlm.v` 的 e8 配置作为明确例外；
- 初始化、必要的内存排序和充分 warmup 均在计时区间外完成；确认初始化 store 的转发未主导计时结果；
- offset=0 同地址闭环表示 warm-address 测试，实际 cache 命中状态需验证；cache 层级/miss 测试另建多节点 offset ring，不能仅因使用 ring 就认定发生 miss；
- 默认只消费第 0 个元素/第 0 字段，测的是该结果到下一次地址可用的路径；其它元素和字段可能重叠执行，不能宣称测出了整个向量或全部字段的完成延迟；
- 依赖链仍可能受 load 的其它元素执行、寄存器合并、结构资源和退休约束影响，比较时必须保留 VL、LMUL、NFIELDS、stride 等元数据；
- store、segment-store 不使用该 load-return RAW 模型。

### 9.6 第二阶段验证

1. 检查每个配置的实际 VL 等于 VLMAX，完整访问区域、对齐和有效载荷合法；连续运行后 `t0` 必须仍等于 `B+d`。
2. 反汇编确认 `vload -> vmv.x.s -> add -> 下一次 vload` 每一段均有真实 RAW 依赖，且控制链也消费上一轮 add 结果。
3. 在各自 VLMAX 下覆盖普通 load 的全部 EEW、正/负 stride、不同 segment 字段数和合法 LMUL；`vlm.v` 分别验证 e8 首版及 VLMAX 满足有效字节数的 e32 配置。
4. 检查没有读到 mask-off/尾部 agnostic 位；不能用 `tumu` 恰好保留旧数据来掩盖载荷未写满的问题。
5. 比较 load 链与控制链在 `4/16/64` 下的斜率和稳定性，确认首轮冷访问/初始化 store 不主导原始周期。
6. 输出显式记录 `mediator=vmv.x.s+add`、`address_mode=base_offset`、`cache_mode`、实际 SEW/EEW/VL 和具体访存形式。

## 10. Mask 延迟模型

### 10.1 读向量、写 mask：通过 masked vadd 闭环

覆盖整数比较 `vmseq/vmsne/vmslt*/vmsle*/vmsgt*`、浮点比较 `vmfeq/vmfne/vmflt/vmfle/vmfgt/vmfge`，以及向量到 mask 结果的 `vmadc/vmsbc` 形式。默认 e32/m1 示例：

```asm
vmseq.vv v0, v1, v2
vadd.vv  v2, v1, v3, v0.t
vmseq.vv v0, v1, v2
vadd.vv  v2, v1, v3, v0.t
```

- 目标指令写出的 v0 控制后续 vadd，vadd 写出的 v2 必须被下一条目标指令读取；不能改成目标指令反复读取固定 v1/v2 的独立序列。
- `vi/vx/vf` 比较形式固定立即数/GPR/FPR，同样把 vadd 的结果接到其向量数据源；带 carry-in 的形式分别标注向量源与隐含 mask 源依赖。
- 浮点比较后的中介仍用整数 `vadd`，按位消费/产生合法浮点编码；初始化避免变成意外 NaN 测试。
- 实际模板按 LMUL 选择对齐且互不冲突的普通向量寄存器组；上面的 v1/v2/v3 仅是 m1 示例，不能机械用于更大 LMUL。
- `chain_len=n` 表示 n 条目标比较/carry 指令，主体包含 n 条 masked vadd，`body_insns=2*n`，`mediator=vadd.vv(masked)`。
- 结果是 `目标指令 -> mask use -> vadd result -> 目标指令` 的组合 RAW 延迟；不能直接除以 n 后命名为纯比较延迟，也不能减去独立 vadd 测试就声称完全隔离。
- 初始化及逐指令数值检查保证 warmup 后链中仍有 active 元素，不能长期全 masked-off；每种比较/借位指令分别选择稳定或可追踪的输入，记录 mask 模式。
- 在 tama 下 inactive 数据可能保持原值或变成全 1；闭环不得要求这些位维持某个旧值。tumu 的旧目的合并依赖也属于该配置的路径，不视为可统一扣除的常数。
- 全部链体、控制链和 warmup 使用 VLMAX；检查目标数据源与 mask 控制两段依赖都存在。

### 10.2 读 mask、写 mask：按 vadd 的直接 RAW 方法

- `vmand.mm/vmandn.mm/vmor.mm/vmorn.mm/vmxor.mm/vmxnor.mm/vmnand.mm/vmnor.mm` 直接串联 mask 结果；不插入 masked vadd：

```asm
vmand.mm v8, v8, v16
vmand.mm v8, v8, v16
vmand.mm v8, v8, v16
```

- 一个 mask 源提供上一条结果，另一个 mask 源固定；`chain_len=n`、`body_insns=n`、`mode=mask_direct`。
- `vmsbf.m/vmsof.m/vmsif.m` 也是读 mask 写 mask，但 ISA 禁止源目的重叠，使用两个不同寄存器交替形成纯链：

```asm
vmsbf.m v8, v16
vmsbf.m v16, v8
```

- mask 始终占一个寄存器，不能按普通 LMUL 向量组机械扩展 mask 寄存器编号；仍使用当前配置的 VLMAX 个 mask 元素。
- mask 目的尾部为 agnostic，测试仅依赖有效 VLMAX 位；保留 tama/tumu 配置元数据，但不能假设它们改变 mask 目的尾部策略。

### 10.3 读 mask、写标量：通过 `vmseq.vx` 闭环

`vcpop.m` 和 `vfirst.m` 都读取 mask、写入 GPR，因此共用“mask -> scalar -> `vmseq.vx` -> mask”的交替链：

```asm
vcpop.m  t0, v0
vmseq.vx v1, v2, t0
vcpop.m  t0, v1
vmseq.vx v0, v2, t0
```

继续重复上述四条指令。两个 mask 目的寄存器 `v0/v1` 交替使用，使每条 mask-to-scalar 指令都消费上一条 `vmseq.vx` 的结果；每条 `vmseq.vx` 都消费上一条 mask-to-scalar 指令的 `t0` 结果。

- `vcpop.m` 的稳定初始化：输入 mask 只有 bit 0 为 1，固定向量 `v2` 的第 0 个元素为 1、其余元素为 0。这样 `t0=1`，`vmseq.vx` 产生同样的单 bit mask。
- `vfirst.m` 的稳定初始化：输入 mask 只有 bit 0 为 1，固定向量 `v2` 的第 0 个元素为 0、其余元素为 1。这样 `t0=0`，`vmseq.vx` 仍产生只有 bit 0 为 1 的 mask。必须避免空 mask，否则 `vfirst.m` 会产生 `-1`，链的数值状态会改变。
- 上述初始化、`v2` 填充和 `vtype` 设置均在计时区间外完成；正式链全部使用当前配置的 VLMAX。
- `chain_len=n` 表示 n 条 mask-to-scalar 目标指令和 n 条 `vmseq.vx`，主体 `body_insns=2*n`，`mode=mask_to_scalar_bridge`，`mediator=vmseq.vx`。
- 测得的是 `mask -> GPR -> vmseq.vx -> mask` 的组合 RAW 延迟。若已有 `vmseq.vx` 数据代表相同 SEW/LMUL/VLMAX 下的 GPR-to-mask 延迟，则可按斜率相减估计 `vcpop.m`/`vfirst.m`；否则只能报告组合延迟。
- 当前计划中的 `vmseq -> masked vadd -> vmseq` 测试包含 masked `vadd` 和向量源依赖，不能直接当作本节的纯 `vmseq.vx` 标定值。
- `vcpop.m` 和 `vfirst.m` 都必须使用非空、可重复的输入 mask；不使用空 mask 或短 VL 构造特殊情况。

### 10.4 读 mask、写向量：通过 `vmseq.vi` 闭环

`viota.m` 可以使用 `vmseq.vi` 把其向量结果重新转换为 mask，形成反向闭环：

```asm
viota.m  v1, v0
vmseq.vi v0, v1, 0
viota.m  v1, v0
vmseq.vi v0, v1, 0
```

- `v0/v1` 交替使用，避免 `viota.m` 的目的向量组与 mask 源重叠；`vmseq.vi` 读取前一条 `viota.m` 的向量结果并写回下一条 `viota.m` 的 mask 源。
- 稳定初始化：输入 `v0` 只有 bit 0 为 1。此时 `viota.m` 结果为 `[0,1,1,...]`，`vmseq.vi ... ,0` 重新产生只有 bit 0 为 1 的 mask。
- 全部使用当前配置的 VLMAX；初始化和 `vtype` 设置在计时区间外完成。
- `chain_len=n` 表示 n 条 `viota.m` 和 n 条 `vmseq.vi`，主体 `body_insns=2*n`，`mode=mask_to_vector_bridge`，`mediator=vmseq.vi`。
- 测得的是 `mask -> vector -> mask -> vector` 的组合 RAW 延迟。只有已有 `vmseq.vi` 数据与相同的 vector-to-mask、VLMAX 和 SEW/LMUL 路径同口径时，才允许做差分估计；否则报告组合延迟。
- `viota.m` 的目的寄存器组不能与 mask 源重叠，寄存器模板必须按 LMUL/寄存器组约束生成。

## 11. 覆盖盘点与剩余模型

日期：2026-10-11。此处“纳入”表示有明确的测量模型，不表示源码已实现或运行已验证。

盘点边界：当前 `instructions/rvv/gen.S` 经 m4 和当前 `-march=rv64gcv_zba_zbb_zbs_zvbb` 的 CPP 后，以 bench_names 中完整 opcode 去重。默认不启用 ENABLE_MASK；stride 参数变体不重复计数。不使用旧 census 的类别前缀直接判断指令语义。

| 模型/状态 | opcode 形式数 |
| --- | ---: |
| 普通向量结果直接链（包含 vi/vx/vf，特殊宽度/重叠模板仍需实现） | 261 |
| mask 到 mask 直接链（8 种逻辑 + 3 种单源前缀） | 11 |
| 向量到 mask 的 masked-vadd 中介链 | 42 |
| mask 到标量的 `vmseq.vx` 中介链 | 2 |
| mask 到向量的 `vmseq.vi` 中介链 | 1 |
| load-to-address-use 家族（各具体模板仍需实现） | 97 |
| 明确不测试的 move 形式 | 12 |
| 明确跳过的向量 store | 65 |
| 原有标量 add/mul，不纳入 RVV 目标 | 2 |
| 合计 | 493 |

### 11.1 明确不测试的 12 种 move 形式

| 类别 | 具体形式 | 处理 |
| --- | --- | --- |
| 向量/标量搬运 | `vmv.x.s`, `vfmv.f.s`, `vmv.v.x`, `vmv.s.x`, `vfmv.v.f`, `vfmv.s.f` | 明确不测试 |
| 向量寄存器搬运 | `vmv.v.v`, `vmv1r.v`, `vmv2r.v`, `vmv4r.v`, `vmv8r.v` | 明确不测试 |
| 立即数搬运 | `vmv.v.i` | 明确不测试 |
| 无输入索引生成 | `vid.v` | 明确不测试 |

move 指令不作为 latency 目标；`vmv.x.s` 仅可作为 load 模型的中介，`vmv.v.x` 不再作为控制链目标。`vcpop.v` 属于普通向量结果指令，`vcpop.m`/`vfirst.m` 属于 mask-to-scalar 模型。

### 11.2 生成器缺项与特殊模板

- indexed load（含 ordered/unordered 与 indexed segment）：base 中介模型已定义，但当前生成器仍是 TODO，需补指令枚举、数据/index 布局及 SEW/LMUL 模板；indexed store 全部排除。
- whole-register load（`vl1re*/vl2re*/vl4re*/vl8re*`）：当前生成器未枚举；实际传输量由寄存器数与 EEW 决定，需要专用模板，不能用配置 VL 代替传输量。
- `vsetvl/vsetvli/vsetivli`：当前仅作 setup，未纳入配置指令本身的延迟模型，不在上述 493 项内。
- 当前 Zvbb 枚举还缺 `vrev8.v`、`vror.vi`、`vwsll.vi`；这些可以归入第一阶段直接链方法，但必须先补枚举/合法模板，不能宣称当前指令清单已覆盖整个 Zvbb。
- widen/narrow、extend、permute、reduction 虽在第一阶段范围内，仍需按源宽度、寄存器重叠和依赖端口逐条实现；“纳入模型”不是已完成合法汇编/运行验证。

### 11.3 当前实施状态

- `instructions/rvv/latency_gen.py` 从当前 `gen.S`/CPP census 生成独立 latency 汇编；当前保守模板生成 **237** 个形式，**256** 个复杂/排除形式保留在 stderr 清单中。
- `instructions/rvv/latency_main.c` 固定 e32/m1、VLMAX、`tama/tumu`，依次运行 chain length `4/16/64`，输出 raw CSV。
- `make -B build/latency.elf QUICK=1` 已通过；ELF text/data/bss 为 `132602/11808/328705`。
- `make run-latency QUICK=1` 在现有 `run.sh` 的 QEMU Linux-user 裸机执行边界报 `Illegal instruction`；尚未形成运行时 PASS。

### 11.4 本轮已决约束

- 所有测试固定 VLMAX，链长默认 4/16/64 并可配置。
- 普通 vi/vx/vf 指令只串联向量结果，立即数/GPR/FPR 固定不变；纯 scalar/immediate-to-vector move 不误归类。
- 向量到 mask 采用 masked-vadd 中介，mask 到 mask 直接 RAW 串联。
- `vcpop.m`/`vfirst.m` 采用 `mask -> scalar -> vmseq.vx -> mask` 交替闭环；只有已有 `vmseq.vx` 值与 GPR-to-mask 路径同口径时才做差分估计。
- `viota.m` 采用 `mask -> vector -> vmseq.vi -> mask` 交替闭环；`vmv.v.i` 和 `vid.v` 明确不可构造 RAW 延迟链。
- 所有向量 store 跳过，不是待实现项目。
- 先输出分模式原始周期，严格区分直接链与带中介的组合延迟。
