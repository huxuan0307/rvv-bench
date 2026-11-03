# LMUL=4 首版实现与验证

日期：2026-10-16。运行 ID：`20261016-193418-m4`。
状态：NEMU 功能与架构指令计数通过；真实 RTL/硬件周期未测。两 block / m2 未实施。

## 1. 实现

- 保留 `riscv` 原始适配层和第三方 `chacha20` 单块 ABI，不覆盖第三方汇编或旧 bin。
- `riscv_xor`：单块 core 不变，显式 e8/m4 加载明文和 keystream 后 XOR，支持 in-place。
- `riscv_m4`：word-aligned 完整 256 B 批次调用独立汇编 kernel，每批四 block；剩余不足 256 B 或未对齐请求使用 `riscv_xor`。
- 数据 round 为 e32/m4、VL=16；ei16 索引按 EMUL=m2 保存。状态/nonce 初始化、XOR、feed-forward 及真实 I/O 均在被测调用内部。
- 两个 double-round 成对交换工作组和临时组的逻辑角色，避免为每次 gather 插入整组 copy；五对完全展开完成 20 轮。
- 原始 state 不跨 round 保留：三个常量/key 行末尾从只读输入重载并重复展开，counter/nonce 行重建，然后按行 XOR、indexed store 回文件布局。

文件：`bench/chacha20-riscv-m4.S`、`bench/chacha20-riscv-opt.h`，入口注册与依赖更新在 `bench/chacha20.c`、`bench/chacha20.S`、`bench/Makefile`。

## 2. 基线和构建来源

- 主仓库 HEAD：`5155827a75a22ec0280a44e7225cd96f75f3480f`，包含既存 dirty 修改，不能单凭 HEAD 复现。
- 第三方 HEAD：`dab9b41c0e78296f6984cc1879ea82134571f915`，本轮保持 clean。
- GCC：`riscv64-unknown-linux-gnu-gcc (g6afcc4f6d) 16.1.0`。
- 主工程选项：`-march=rv64gcv_zba_zbb_zbs_zvbb -mcmodel=medany -fno-builtin -nostdlib -ffreestanding -D__BYTE_ORDER=__LITTLE_ENDIAN -O2 -g -Wl,--wrap=_trm_init`，warning 选项沿用 `config.mk`。
- vendor core 保持预生成 GCC 14.2 / O3 汇编。新增 core 为手写汇编，因此本次比较是实现整体对照，不把收益归因于单一编译选项或仅 LMUL 改变。
- 日志根：`logs/chacha20-opt/20261016-193418-m4/`。
- 基线源码快照：`baseline/source/bench/`；既存 dirty diff：`baseline/worktree.patch`；最初源文件哈希：`baseline/source.sha256`。
- 当前源码、链接输入以及基线/优化 bin 哈希：`manifest.sha256`。

隔离构建根为 `bench/build/chacha20-opt/20261016-193418-m4/`，原始 `bench/build/run` 未覆盖。

## 3. 功能验证

| 检查 | 结果 |
|---|---|
| 冻结基线原有 API | 552 组通过 |
| 扩展基础边界、key/nonce、counter、两类明文 | 1,160 组通过，五实现 |
| 两个 RFC 8439 block 向量：64 B 单块与 256 B 批内对应位置 | 20 组通过 |
| 最后合法 counter、完整批次及 tail | 25 组通过 |
| 新增两种实现的 in-place、未对齐数据、未对齐 key/nonce 回退 | 66 组通过 |
| 64 KiB、256 KiB、1 MiB 差分与保护区 | 15 组通过 |
| 扩展 API 合计 | 1,286 组通过 |
| 五实现的 check/run bin | 10/10 通过 |

原始终态要求均已检查：rc=0、HIT GOOD TRAP，无 ERROR/bad/critical trap；check 有 `OK: "chacha20 aligned"`。
RFC 向量验证的是已知 block 输出和其在批中的位置，不宣称已经加入 RFC 的全部消息加密测试向量。
counter 测试均处于合法范围；没有新增跨 counter 域的拒绝接口，也没有把 wrap 当成安全支持。

API 日志：`api-validation.log`；逐 bin 日志：`check-*.log`、`run-*.log`。

## 4. 等工作量单次调用指令数

`bench/chacha20-profile.c` 每个长度、每个实现调用一次，用相同调用点的空函数校准，函数体为 `raw - empty + 1`，包含自己的 return。
80 条样本覆盖 16 个长度，全部输出与 boring 逐字节一致。运行时 VLEN=128。这里不是流水线周期或吞吐倍数。

| 长度 | 原始 riscv | riscv_xor | riscv_m4 | rvv |
|---:|---:|---:|---:|---:|
| 256 B | 3,795 | 1,754 | 448 | 1,099 |
| 64 KiB | 934,035 | 410,774 | 101,173 | 279,304 |
| 1 MiB | 14,942,355 | 6,570,134 | 1,617,973 | 4,468,744 |

64 KiB 下新四块实现比原始 riscv 少 89.17% 架构指令，比 rvv 少 63.78%。短消息的 C 分派会增加固定开销，应从完整原始样本判断，不宣称所有长度都更快。
对于当前 aligned 完整批次样本，`riscv_m4` 函数体满足 `53 + 395 * (n / 256)`；单个 256 B 批次稳态 395 条，即 98.75 条/block。
这些公式经测量样本核对，不是目标处理器周期模型。

原始样本：`bench/build/chacha20-opt/20261016-193418-m4/optimized/check/chacha20-profile-d262144-mi1.log`。
typed JSON 和比较结果：日志根的 `summary.json`，其中 `cycles` 为 `null`。

## 5. Spill 与栈审计

`riscv_chacha20_m4_blocks` 的完整函数反汇编在日志根 `kernel.dasm`：

- 静态 409 条指令，13 条循环外设置 + 395 条批次主体 + 1 条 return。
- 不访问 sp，没有 stack frame，没有 whole-register 或普通向量 spill/reload。
- 数据配置只有 e32/m4；e16/m2 用于 16-bit 索引。
- 60 条 round gather，加六条常量/key 重复展开 gather；满批的数据 round VL=16。
- v20 索引组在 round 后复用为 I/O offsets，下批重新生成 idx1；idx2/idx3 和列索引跨批保留。
- v0 在初始 state 尚未建立、或对应输出行已写出后才复用为 nonce merge mask。

C wrapper 另有 80 B 的标量调用保存区，见 `wrapper.dasm`；这是每消息调用的固定开销，不是 hot loop 向量 spill。
短消息、残余 1-3 个 block 和未对齐 fallback 仍调用原始单块 core，其既有 spill 仍然存在。因此仅对 full-batch 汇编 leaf 声明零 spill，不能声称整个 API 的所有路径都不访栈。
此实现支持四个 block 的批次，不因更大 VLEN 自动扩到八块；本轮也未在其它 VLEN 上运行验证。

## 6. 复现

从仓库根执行，现有 Makefile 目标可复用；新 run 应另选独立 BUILD 目录：

```sh
make -C bench BUILD=build/chacha20-opt/20261016-193418-m4/optimized BENCHES=chacha20 -j4 check run
make -C bench BUILD=build/chacha20-opt/20261016-193418-m4/optimized test-chacha20
make -C bench BUILD=build/chacha20-opt/20261016-193418-m4/optimized profile-chacha20
```

优化版本运行镜像：

```text
bench/build/chacha20-opt/20261016-193418-m4/optimized/run/chacha20-riscv_m4-d262144-mi1.bin
```

记录的逐 bin 运行均采用 `timeout 120s riscv64-nemu-interpreter -b <bin>`。
`test-chacha20`、`profile-chacha20` 自身运行 NEMU；`check`、`run` 仅构建，仍需逐 bin 执行。

分析命令：

```sh
python3 .tasks/chacha20-opt/analyze-profile.py \
  --log bench/build/chacha20-opt/20261016-193418-m4/optimized/check/chacha20-profile-d262144-mi1.log \
  --kernel-dasm logs/chacha20-opt/20261016-193418-m4/kernel.dasm \
  --out logs/chacha20-opt/20261016-193418-m4/summary.json
sha256sum -c logs/chacha20-opt/20261016-193418-m4/manifest.sha256
```

目标 RTL/硬件的 cycles/byte、indexed memory 吞吐和最终默认分派尚未验证；下一步先做这些测量，不从指令数比例直接选择最终赢家。
