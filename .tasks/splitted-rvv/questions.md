# questions

## Q1. 分组粒度是否按 plan §3.1 落地？

**已决 → A1**：逐指令，不按类别。

默认 21 个细目标 + 4 个 bundle（`int/fp/ls/ext`）+ 原 `rvv` 全量。

备选：只做 4 个 bundle（实现更少，但「一类」偏粗，例如整数 ALU 和乘除无法分开）。

## Q2. 标量 `add/mul` 是否打进每个子集？

**已决 → A2**：不打进子集。

计划默认：是（2 条，作 sanity）。若 DUT 连标量都要从向量 ELF 里去掉，再说。

## Q3. `bench-all.sh` 要不要改成跑子集？

**已决 → A3**：不改。

计划默认：不改，避免 2h 脚本行为变化。子集由 `make -C instructions/rvv groups` 显式调用。
