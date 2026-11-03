# ChaCha20 riscv 优化计划

状态：LMUL=4 首版已实现并通过 NEMU 功能/指令验证；P5 目标周期验证待执行。
日期：2026-10-16，Asia/Shanghai。
设计论证见 `design.md`，执行记录见 `progress.md`。

## 1. 目标和边界

优化 `bench/chacha20.c` 接入的 `riscv` stream 实现，保持与 `boring`、`rvv` 相同的输入、输出和端到端计时口径。
先恢复 XOR 向量化，再比较两 block / m2 与四 block / m4，最后融合 core、明文 XOR 和输出。
最新范围：先实现 LMUL=4 的 XOR 修复与四 block 融合路径，暂不实施两 block / m2。
下面的两 block 设计保留为后续候选，不把设计预算当作实测结果。

约束：

- 不移动 state 构造、必要的布局转换、XOR 或尾块处理到计时区间之外。
- 保持六参数 stream API；保留第三方 `chacha20(chacha_buf *, const u32[16])` 的单 block ABI 和原始基线。
- 不实施此前已排除的单 block / 数据 LMUL=1 方案。新增两 block / 数据 m2 作为本轮讨论提出的候选；索引 EMUL=1 不等于数据 LMUL=1。
- `boring`、`rvv` 不参与算法改写；不得覆盖用户正在使用的 `bench/build/run` 或旧测量产物。
- 指令数、周期、访存量分别评价，不用 NEMU 整程序计数代替等工作量 kernel 性能。
- 所有候选独立命名、独立输出目录；最终选择由正确性、实际 spill 和目标处理器周期共同决定。

## 2. 当前现场和已确认的证据

- 仓库：`/nfs/home/huxuan/repos/rvv-bench`，分支 `main`。
- HEAD：`5155827a75a22ec0280a44e7225cd96f75f3480f`；工作区有既存修改，HEAD 不能独立代表被测源码。
- 第三方 `chacha20-riscv` HEAD：`dab9b41c0e78296f6984cc1879ea82134571f915`。
- 已有证据目录：`logs/chacha20-insn-20261016-181824/`。
- 入口说明、原始日志、单次调用数据、计数模型和哈希分别在 `report.md`、`whole-*.log`、`profile.log`、`summary.json`、`manifest.sha256`。
- 本轮已重新执行该目录的 `sha256sum -c manifest.sha256`，记录的源码和 bin 均匹配。

已测结果，限当前 NEMU / VLEN=128 和记录的构建配置：

| 项目 | 已有结果 | 边界 |
|---|---:|---|
| 当前 riscv 的 XOR | 512 条/block | GCC 16 / O2 下为标量循环 |
| 仅恢复 restrict 的隔离对照 XOR | 40 条/block | 尚未应用到业务源码 |
| 当前单 block core | 385 条 | 240 ARX + 60 gather + 85 其他 |
| 当前 core 的 spill/reload | 4 次保存 + 4 次恢复 | 512 B/block，VLEN=128 |
| 64 KiB 的 rvv 函数体 | 279,304 条 | 单次调用，已校准调用点 |
| 64 KiB 的当前 riscv 函数体 | 934,035 条 | 不是整程序计数 |
| 64 KiB 的 restrict 对照函数体 | 450,707 条 | 输出已逐字节验证 |

当前 `bench/chacha20.S` 实际包含第三方 `src/chacha20.s`，不是直接编译其 `src/chacha20.c`。
该 `.s` 带有 GCC 14.2 / O3 的生成横幅，而主工程 `config.mk` 当前为 GCC 16 / O2。
涉及 core 的 A/B 必须增加同工具链重编的控制组，不能把编译器、优化级别变化归因于 LMUL 或布局。

## 3. 候选矩阵

候选名是拟定的独立符号/标签，不代表当前已有 Makefile 目标。
VL 和容量以当前 VLEN=128 为基准，运行时仍须读取并记录 `vlenb`。

| 标识 | 每批 block | 数据配置 | gather 索引 | 原始 state 策略 | XOR |
|---|---:|---|---|---|---|
| A0 `riscv_orig` | 1 | m4 / VL=4 | vv，EMUL=4 | 原始汇编 | 当前标量基线 |
| A1 `riscv_xor` | 1 | m4 / VL=4 | vv，EMUL=4 | 原始汇编 | 恢复向量化 |
| C0 `riscv_recompiled` | 1 | m4 / VL=4 | vv，EMUL=4 | 同工具链控制 | 向量化 |
| B2V `riscv_b2_m2_vv` | 2 | m2 / VL=8 | vv，EMUL=2 | 尝试寄存器常驻 | 向量化，未融合 |
| B2E `riscv_b2_m2_ei16` | 2 | m2 / VL=8 | ei16，EMUL=1 | 尝试寄存器常驻 | 向量化，未融合 |
| B4E `riscv_b4_m4_ei16` | 4 | m4 / VL=16 | ei16，EMUL=2 | 末尾重读或重建 | 向量化，未融合 |
| F2 / F4 | 2 / 4 | 对应 B2 / B4 | 对应候选 | 对应候选 | 融合到输出 |

“两 block 但数据仍为 m4 / VL=8”只作预算说明，默认不作为优化候选：它不会自动缩小分配的寄存器组。
“仅 gather 用 m4，其它 round 用 m2”列为低优先级设计问题，先证明布局和切换成本，不作为默认快路径。

## 4. 实施阶段

### P0：冻结和复现基线

- [ ] 保存实施开始时的 dirty diff、主仓库/第三方 SHA、编译器版本、完整命令、ISA 和源文件哈希。
- [ ] 在唯一 `bench/build/chacha20-opt/<run-id>/` 中重建三个现有实现及 API 测试，不覆盖旧 bin。
- [ ] 复跑三个 check/run 和现有 552 个 API 组合测试，逐项记录 rc、完成标记和错误扫描。
- [ ] 复现固定长度、固定调用次数的 `rdinstret` 结果及实际 VLEN，保存完整反汇编。

验收：可以从 manifest 将源码、ELF/bin、日志和结果表一一对应；旧产物保持不变。

### P1：恢复 XOR 向量化

本轮采用独立 `riscv_xor` 标签及显式 e8/m4 XOR，避免依赖 alias 推断，并保留 in-place；旧 restrict 对照仍作为历史参考。

- [ ] 明确外层 API 的 alias 约束。基准 src/dst 不重叠，可在内部适配层恢复 `restrict`。
- [ ] 若保留 in-place 支持，则使用合法的独立路径或显式向量 XOR，不能给重叠指针错误添加 `restrict`。
- [ ] 保持第三方 core 不变，只比较 A0/A1，确认反汇编不再是每字节 8 条的标量循环。
- [ ] 对相同刺激验证正确性和指令数；把该回退纳入性能回归检查。

验收：当前配置的参考值为 XOR 40 条/block、适配层稳态 440 条/block。新结果必须重新实测，不能直接沿用旧对照的 PASS。

### P2：建立两 block / m2 内核

本阶段按最新用户要求延期；本轮未实施。

- [ ] 同工具链构建 C0 控制组，并确认与原始 core 输出一致。
- [ ] 按 `design.md` 的两 block 行打包构造 state、counter 和三个索引。
- [ ] 先实现 B2V，再单独替换为 B2E，区分数据 LMUL 降低与索引压缩的收益。
- [ ] 先比较两个独立单 block core 的 keystream，再验证统一 stream API 的密文和尾部。
- [ ] 分开测布局准备、core、输出转换及完整调用；所有真实工作都要计入完整调用。

验收：寄存器预算上 B2V 为 24、B2E 为 21，均有可行空间；实际“无 spill”必须由编译后的数据流和栈访问证明。
不能把这些预算写成已实现的零 spill 结果。

### P3：建立四 block / m4 内核

- [ ] 数据保持 m4，VL=16；索引带 block 偏移，不能重复单 block 的 `{1,2,3,0}`。
- [ ] 不让四份原始行向量跨越整个 round 全部常驻：比较从只读 state 晚重载与从 key/nonce/counter 重建。
- [ ] 缩短索引、feed-forward 和临时向量的活跃期，必要时显式组织汇编寄存器。
- [ ] 记录重新加载/重建和索引准备成本，避免把“减少编译器 spill”误报成“没有额外访存”。

验收：满批正确，实际 spill 和内存流量明确；与 B2 使用相同编译选项及同等 XOR/融合配置比较。

### P4：融合 core、明文 XOR 和输出

- [ ] 在 F2/F4 的完整 block 路径中，feed-forward 结果直接与明文 XOR 后写出。
- [ ] 消除不必要的 keystream 临时写入/重新读取，摊薄每 block 函数调用和固定准备。
- [ ] 处理行打包与文件 block-major 顺序的转换，不把排列开销藏到计时外。
- [ ] 索引访存、简单加载加排列等方案按实际合法性和周期比较；不默认 gather 或 indexed memory 更快。
- [ ] 最后不足一个 block 的部分单独处理，不进行越界 word load/store。

验收：F2 对 B2、F4 对 B4 分别作 A/B；指令数、周期和总访存量均独立报告。

### P5：目标处理器评估与分派

截至本轮，仅 NEMU 功能、指令数和反汇编审计完成，完整结果见 `results-m4.md`；没有将指令数改善提升为目标吞吐验收。

- [ ] 在同一目标 RTL/硬件配置上测 `cycles/byte`，记录配置、预热、样本数及原始样本。
- [ ] 分别看短消息、128 B、256 B 边界和长消息；不能只按 NEMU 指令条数选赢家。
- [ ] 确定是否采用四 block 主循环、两 block 收尾和单 block/tail 路径，阈值由测量决定。
- [ ] 保留原始和候选标签用于复核，最终默认选择单独记录原因和证据。

没有目标周期数据时，只能报告功能通过或 NEMU 指令数改善，不能宣布吞吐优化验收完成。

## 5. 验证矩阵

- 原有 552 个组合继续覆盖，所有新增候选都加入逐字节比较和输入/输出保护区检查。
- 边界长度至少为 `0,1,2,3,15,16,31,32,63,64,65,127,128,129,191,192,193,255,256,257,383,384,385,511,512,513,1023,1024,1025`。
- 补充 64 KiB、256 KiB、1 MiB 长消息；独立扩大数据、参考输出和 heap，避免套用现有小 profile 的地址布局造成重叠。
- 使用零明文、递增/固定模式及确定性随机数据，至少两组 key/nonce 和多个合法非零 counter。
- 新增 RFC 8439 的独立 block/encryption 向量，不能仅依赖 `boring` 差分或 checksum。
- counter 合法域单列：正长度满足 `counter + ceil(n/64) - 1 <= UINT32_MAX`。末尾恰好使用最后一个 counter 与实际跨域分开，不能用 REF/DUT 同时 wrap 证明安全。
- 当前 aligned benchmark 是必测项。unaligned、in-place 或拒绝非法 counter 的行为，需先明确 API 契约，再添加实现和验收。
- VLEN=128 为已有环境；其它 VLEN 仅在实际可用配置上补测，记录运行时 CSR，不把当前 NEMU 宣称为多 VLEN 覆盖。

## 6. 寄存器和反汇编验收

- 统计所有 `vs*r`、`vl*re*` 以及普通向量/标量栈访存；不能仅凭“不再出现 vs4r”判定无 spill。
- 区分编译器 spill、必要的 state staging、输入/输出转换、ABI 保存及尾部临时区。
- 输出实际 live-range/register map，检查 gather 的目的组与数据/索引源组不重叠、组对齐及 v0/mask 生命周期。
- 记录 stack frame、保存/恢复字节数、每批和每有效 block 的流量、round 中 `vset*` 次数。
- 对混合 LMUL，检查所有跨配置活跃值和 tail-agnostic 写回；禁止把后续需要的值藏在可能被覆盖的尾部寄存器。
- 对有“无 spill”目标的候选，要求 hot full-block 路径没有为保存/恢复中间向量而发生的栈搬运；尾块或明确的 state staging 不能冒充零开销。

## 7. 文件和结果约定

实施预计只触及 `bench/chacha20.c`、`bench/Makefile`、`bench/chacha20-test.c`，按需新增独立优化源码/汇编。
优先把新 kernel 放在独立文件/符号中，不覆盖第三方已冻结 `.s`；如果确需改第三方仓库，先保存对应 patch 和生成命令。
每个逻辑阶段独立记录，不合入无关配置、其他 benchmark 或历史脏修改。

结果保存在 `logs/chacha20-opt/<run-id>/`，至少包含 manifest、构建/运行命令、rc、终态、完整反汇编、raw samples 和比较表。
原始样本字段至少区分 `variant,bytes,calls,vlen_bits,data_lmul,index_eew,vl,core_insns,stream_insns,cycles,stack_bytes,spill_store_bytes,spill_load_bytes,xor_mode,compiler,source_hash`。
缺少的周期等字段使用 `null`，不填 0；静态预算、NEMU 动态计数和目标周期分开存放。

实施阶段可用的现有入口示例，本文创建时未执行：

```sh
RUN_ID="$(date +%Y%m%d-%H%M%S)-baseline-$$"
make -C bench BUILD="build/chacha20-opt/$RUN_ID" BENCHES=chacha20 -j4 check run test-chacha20
```

此命令编译 check/run 并运行 API 测试；三个 check/run bin 仍需分别运行并检查完成标记。
任务只有在候选比较和选择报告完成后才算验收，生成 plan 或编译成功不算优化完成。
