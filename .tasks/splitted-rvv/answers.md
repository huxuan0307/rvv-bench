# answers

## A1. 分组粒度（Q1）

逐指令独立 ELF，不按类别。类别里任意一条未实现仍会让整包挂掉。

键规则见 `plan.md` §3：`vadd.vv/.vx/.vi` 与 mask 变体同属 `vadd`；load/store 保留 EEW/seg/ff；`vcpop.m` 与 `vcpop.v` 拆开。census：**338** 个 key。

## A2. 标量 `add/mul`（Q2）

不打进向量子集。各自独立目标 `rvv-add` / `rvv-mul`（可选，不作为其它 ELF 的依赖）。

## A3. `bench-all.sh`（Q3）

不改。默认仍编/跑完整 `instructions/rvv`。子集由 `make -C instructions/rvv insn` 或 `make build/rvv-vadd.elf` 显式调用。
