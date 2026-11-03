# `instructions/rvv` 生成物 census（2026-09-20）

命令：

```
m4 instructions/rvv/gen.S > .tasks/splitted-rvv/generated/main.S
riscv64-unknown-elf-gcc -I instructions/rvv $CFLAGS \
  generated/main.S instructions/rvv/main.c -o generated/rvv \
  -T thirdparty/link.ld thirdparty/am-riscv64-xs.a thirdparty/klib-riscv64-xs.a
```

| 项 | 值 |
|---|---|
| `main.S` | 355308 行，5.7 MB |
| ELF | 4.3 MB |
| `size` | text=1665824 data=105664 bss=328705 |
| `bench_names` | 1222 |
| `bench_m1` defptr | 1222（与 names 对齐） |

分组（按助记符语义，已避开 `vsmul`/`vlseg`/`vfirst` 前缀误判）：

```
scalar                  2
int-alu               118
int-muldiv             48
int-widen              88
int-red                20
permute                25
cmp-merge              63
mask                   22
fp-arith               96
fp-cmp                 24
fp-move                 4
fp-red                  8
fp-widen               70
ls-unit                22 + ls-mask 2 = 24（计划合成 rvv-ls-unit）
ls-ff                   8
ls-strided             48
ls-seg-unit            56
ls-seg-ff              56
ls-seg-strided        392
zvbb                   26
zvbc                    8
zvkg                    2
zvkned                 11
zvknh                   3
---------------------------
sum                  1222

bundle int  386
bundle fp   202
bundle ls   584
bundle ext   50
```

逐指令 key（点号前助记符；load/store 保留 EEW/seg/ff）：**338**
每 key 测项数 min=1 / median=4 / max=16（`vfncvt`）。
`vcpop.m` 与 `vcpop.v` 在 stem 上碰撞，计划拆成 `vcpopm` / `vcpopv`。

明细：`generated/groups.tsv`、`generated/insn_keys.tsv`、`generated/bench_names.txt`
