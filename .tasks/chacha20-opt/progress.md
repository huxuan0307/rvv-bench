# ChaCha20 优化进度

日期：2026-10-16，Asia/Shanghai。
状态：M4_NEMU_VALIDATED；目标 RTL/硬件周期验证待执行。本轮只实现数据 LMUL=4，m2 候选延期。

当前运行 ID：`20261016-193418-m4`。
日志：`logs/chacha20-opt/20261016-193418-m4/`。
隔离构建：`bench/build/chacha20-opt/20261016-193418-m4/`。

## 已完成

- [x] 阅读当前 benchmark、API 测试、构建入口和既有指令计数报告。
- [x] 重新校验 `logs/chacha20-insn-20261016-181824/manifest.sha256`，记录的源码和 bin 均匹配。
- [x] 记录主仓库和第三方 SHA，并明确 dirty worktree 不等于 HEAD 源码。
- [x] 梳理两 block / m2 的 24 或 21 个寄存器预算，以及四 block / m4 的 48 或 42 个寄存器压力。
- [x] 澄清 gather 并非因为一个 state 有 16 个 word 就必须使用 m4；现有 gather 是行内置换。
- [x] 将两 block / m2 与四 block / m4 列为独立候选，未重新引入单 block / 数据 m1 方案。
- [x] 写入阶段顺序、反汇编/正确性/周期验收和混合 LMUL、布局转换的风险。
- [x] 静态脚本核验两块/四块 gather 索引不跨 block、行到文件地址一一对应，以及两块 m2/ei16 示例的 21 个寄存器互不冲突。

## 待执行

- [x] P0：已冻结源码、dirty diff，原始 552 个 API 用例及三个 check/run 基线通过。
- [x] P1：新增 `riscv_xor`，使用显式 e8/m4 XOR，保持 in-place 合法，不依赖 restrict 推断。
- [ ] P2：两 block / m2 延期，本轮没有实现。
- [x] P3：新增 `riscv_m4`，四块 e32/m4 + ei16 索引；汇编 full-batch leaf 无栈访问或向量 spill。
- [x] P4：四块路径已融合 XOR 和输出；两块路径延期。
- [ ] P5：目标 RTL/硬件周期比较、尾部策略和最终分派。

## 本轮结果

- 新增独立文件 `bench/chacha20-riscv-m4.S`、`bench/chacha20-riscv-opt.h`，未改第三方 core，也保留原始 `riscv` 标签。
- 扩展 API 测试 1,286 组通过，覆盖两组 RFC block 向量的单块和批内位置、counter 最后合法值、边界、in-place、未对齐回退及最长 1 MiB。
- 五种实现的 check/run 共 10 个 bin 全部 rc=0、HIT GOOD TRAP，check 有 OK 标记，无 ERROR/critical/bad trap。
- `profile-chacha20` 的 16 个长度、五种实现共 80 条单次调用样本通过，输出已逐字节验证。
- `analyze-profile.py` 已核对空调用校准、完整批次计数模型、配置及汇编 leaf 无栈访问。
- 64 KiB 函数体指令数：原始 riscv 934,035；显式 XOR 410,774；四块 m4 101,173；rvv 279,304。
- 结果和复现命令见 `results-m4.md`。源文件和 bin 哈希见当前日志目录的 `manifest.sha256`。

## 当前边界

旧 `restrict` 对照只作历史参考。本轮新增的是显式 e8/m4 XOR，不是把旧对照结果直接标记为新实现 PASS。
两 block 无 spill 只有容量和分配可行性论证，没有生成汇编或新 kernel 的动态证据。
上述静态脚本只检查设计公式和示例映射，不是新 ChaCha20 实现的功能或性能测试。
四块 m4 已有上述功能和指令证据；round 内混切数据 LMUL 的方案没有实现。
无 spill 仅指 `riscv_chacha20_m4_blocks` 汇编 full-batch leaf：C wrapper 有 80 B 标量调用保存区，尾部/未对齐路径仍使用原始单块 core，不能宣称所有长度都没有栈访问。
目标 cycles/byte 未测，不能把 NEMU 指令数下降称作相同比例的硬件加速。旧 `bench/build/run` 未覆盖。
