# 两 block、四 block 和混合 LMUL 设计论证

日期：2026-10-16。
本文是依据当前源码、实测基线和 RVV 语义作出的设计分析，不是新 kernel 的性能或零 spill 实测。

## 1. 首先澄清：gather 不天然要求 LMUL=4

一个 ChaCha20 state 有 16 个 32-bit word，但当前实现将其拆成四行：

```text
va = [x0,x1,x2,x3]
vb = [x4,x5,x6,x7]
vc = [x8,x9,x10,x11]
vd = [x12,x13,x14,x15]
```

`src/chacha20.c` 的 VL 固定为 4。对角轮分别旋转 vb/vc/vd，每条 gather 操作一个行向量，而不是把整个 16-word state 放进同一条 gather。
因此，数据配置取决于“每条行向量同时打包多少个 block”，不是由“算法总共有 16 个 word”直接决定。

当前 VLEN=128、SEW=32 时：

| 每批 block B | 每个行向量有效元素 4B | 数据配置 | VLMAX | 容量使用率 |
|---:|---:|---|---:|---:|
| 1，现有实现 | 4 | m4 | 16 | 25% |
| 2，但不改变数据类型 | 8 | m4 | 16 | 50% |
| 2，新增候选 | 8 | m2 | 8 | 100% |
| 4，长消息候选 | 16 | m4 | 16 | 100% |

这并不重新引入单 block / m1 优化路线。

## 2. 两 block 的状态与索引

每个向量打包两个 block 的同一行，VL=8：

```text
va = [B0.x0,B0.x1,B0.x2,B0.x3 | B1.x0,B1.x1,B1.x2,B1.x3]
vb = [B0.x4,B0.x5,B0.x6,B0.x7 | B1.x4,B1.x5,B1.x6,B1.x7]
vc = [两个 block 的 x8..x11]
vd = [c,n0,n1,n2 | c+1,n0,n1,n2]

idx1 = [1,2,3,0 | 5,6,7,4]
idx2 = [2,3,0,1 | 6,7,4,5]
idx3 = [3,0,1,2 | 7,4,5,6]
```

一般式为 `index_r[i] = (i & ~3) | ((i + r) & 3)`，r 为 1、2、3。
四 block 则扩展为 16 个元素，后两组索引添加 8 和 12 的 block 基址。
这不是对整个向量做循环旋转，也不能把 `{1,2,3,0}` 无偏移地重复。

在 e32/m2 下，`vrgather.vv` 数据和索引均为 m2；`vrgatherei16.vv` 数据为 m2，而索引 EEW=16、EMUL=1。
后一种情况不需要把整个 vtype 改成 e16。两种 gather 都应做对照，不能预设 ei16 的目标执行速度更快。

## 3. 寄存器预算

下表按“四个工作行 + 四个原始行 + 三个常驻索引 + 一个 gather 临时组”的同时活跃模型计算。
计数单位是架构向量寄存器，不是目标处理器 rename 后的物理寄存器总数。

| 配置 | 工作行 | 原始行 | 索引 | 临时组 | 合计 |
|---|---:|---:|---:|---:|---:|
| m4 + vv | 16 | 16 | 12 | 4 | 48 |
| m4 + ei16 | 16 | 16 | 6 | 4 | 42 |
| m2 + vv | 8 | 8 | 6 | 2 | 24 |
| m2 + ei16 | 8 | 8 | 3 | 2 | 21 |

结论：

- 仅把 m4 的每批 block 从 4 或 1 改成 2、把 VL 改为 8，不会自动缩小架构分组，因此不能依靠它避免 spill。
- 两 block 真正改用 m2 后，当前模型下即使保留原始 state 也有寄存器空间；这是“可能无 spill”的依据，不是编译器实际分配的保证。
- 四 block / m4 的工作 state 与原始 state 本身就占满 32 个寄存器，再加索引和非重叠 gather 目的组无法全部常驻。
- 四 block / m4 必须缩短原始 state 的活跃期、晚重载或重建；ei16 只压缩索引，不会单独消除 10 个寄存器的数目缺口。
- m4 还需按四寄存器对齐，缺口不能按零散十个寄存器直接分配。

两 block / m2 / ei16 的一个可行初始寄存器映射，所有 gather 均按 unmasked 路径说明：

| 内容 | 架构寄存器 |
|---|---|
| 工作 va/vb/vc/vd | v2-v3 / v4-v5 / v6-v7 / v8-v9 |
| 原始 va/vb/vc/vd | v10-v11 / v12-v13 / v14-v15 / v16-v17 |
| gather 临时组 | v18-v19 |
| idx1/idx2/idx3，EEW=16 | v20 / v21 / v22 |

这保留了 v0-v1，组对齐和源/目的非重叠可满足。旋转后可以交换逻辑角色复用已释放的旧行组，不要求机械插入整组 move。
完整实现仍需审计初始化、round、feed-forward、XOR 和尾部的峰值活跃值；C intrinsic 类型和编译器额外临时量可能提高实际压力。

## 4. “只有 gather 用 m4，其它地方不用 m4”

从 ISA 上，不同阶段可以通过 `vset*` 改变数据配置；特殊操作数的 EMUL 也可以与数据 LMUL 不同。
但切换配置不自动搬移、拼接、转置或压缩已有向量内容。

分情况看：

1. 两 block / 8 元素的行向量：ARX 和 gather 都可以直接使用 m2。把 gather 单独扩大到 m4 没有容量必要，还会增加分组对齐、目的组和尾部管理约束。
2. 四 block / 16 元素的行向量：m4 可一次覆盖整行；改用 m2 做 ARX 只覆盖其中 8 个元素，必须拆成上下两半运算或改变布局，不能保持一条指令处理全部元素。
3. 同样四 block 的有效 state 占用不会因改写 vtype 凭空减半。gather 阶段仍需合法、连续、非重叠的 m4 源/目的组，峰值压力仍要计算。
4. `ta` 下可能写坏后续需要的尾部值；不能把 m4 高半组中的寄存器借给其它活跃数据，再假定 VL 较小就一定不会碰它们。

因此第一版不采用 round 内频繁混切 m2/m4。先比较统一 m2 的两 block 与统一 m4 的四 block，只有证据显示值得才另立混合配置实验。
“其它地方不用 m4”的优先落点是更小 EMUL 的索引、需要时的尾部路径和单独的 I/O 阶段，而不是盲目给每条算术插入 vset。

## 5. 四 block / m4 的 state 保留策略

保持工作行和三个 ei16 索引、一个临时组时，round 活跃预算为 `16 + 6 + 4 = 26`。
原始行不跨 round 全部常驻，在结束后利用索引已死亡的寄存器按行重读或重建，再完成 feed-forward 和输出。

当前 C 源码在 round 后已经写了四个 `vle32` 表达式，但实际接入的生成汇编选择了初始值 spill/reload。
因此必须以最终汇编验证是否真的晚加载；必要时用明确的汇编寄存器分配或可审计的代码组织，不能只移动 intrinsic 语句后宣布成功。
晚重载/重建仍有指令和数据流量，只能与省去的保存、栈寻址等成本比较，不能宣称完全零访存。

## 6. 布局、I/O 和尾部风险

行打包布局与文件的 block-major 布局不同。直接按 va、vb、vc、vd 顺序连续 store 会把多 block 的行交错顺序写错。
初版可用显式 pack/unpack 建立正确性参考，但在端到端 benchmark 中真实发生的转换必须计时；后续融合路径应避免不必要的中间缓冲。

可评估按行的 indexed word load/store。对于批内第 i 个元素、行号 r：

```text
byte_offset(i,r) = 64 * (i >> 2) + 4 * (i & 3) + 16 * r
```

两 block 的最大批内 offset 为 124，四 block 为 252，16-bit offset 可表示。索引可在 round 索引释放后构造并复用寄存器。
这只是合法候选，indexed memory 的实际代价必须测量，不能只因架构指令少就默认采用。
也不能直接拼接非法的 segment 方案；对每一种 segment 操作须核对 `EMUL * NFIELDS <= 8`，例如 EMUL=4、NFIELDS=4 不合法。

尾部按完整 block 数和剩余字节分开：完整两 block / 四 block 快路径后，剩余一个 block 或不足 64 B 的部分使用合法收尾。
如果采用缩短批次 VL，必须保证全部索引指向本批已初始化元素；不得通过放大 VL 越界读取 state、索引或文件数据。

## 7. 预期收益与不能预设的结论

沿用当前四行 round 结构，240 条 ARX 和 60 条 gather 可共同服务一批数据，理论摊薄为：

| 每批 block | 轮运算和 gather 的架构指令数/block |
|---:|---:|
| 1 | 300 |
| 2 | 150 |
| 4 | 75 |

这不包含 feed-forward、state 构造、布局转换、XOR、访存、vset 和 spill，也不是测量值。
两 block 可能通过无 spill、较低寄存器压力和更轻的排列执行胜过四 block；四 block 可能通过更低的前端指令成本胜出。
两者都是待验证假设，不预先宣布赢家。

## 8. 规范和源码依据

访问日期：2026-10-16。规范依据为 RVV 的寄存器分组、gather、whole-register memory、segment EMUL 约束；上述具体预算和布局是本任务的推导。

- 官方 ISA 手册：`https://github.com/riscv/riscv-isa-manual/blob/main/src/unpriv/vector-common.adoc`
- V 扩展章节：`https://docs.riscv.org/reference/isa/v20260120/unpriv/v-st-ext.html`
- 独立算法测试向量：`https://www.rfc-editor.org/rfc/rfc8439`
- 当前源码：`thirdparty/chacha20-riscv/src/chacha20.c`，重点为 VL、四个行加载、三个索引、六次 gather 及末尾 feed-forward。
- 实际接入：`bench/chacha20.S` 包含 `thirdparty/chacha20-riscv/src/chacha20.s`。
- 基线证据：`logs/chacha20-insn-20261016-181824/`；该目录的历史建议不是当前任务约束，以 `plan.md` 为准。
