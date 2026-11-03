# 按指令拆分 `instructions/rvv` 独立构建

状态：已实施（`gen.S` `m_emit` + `insns.list` + Makefile `build/rvv-%.elf`）
日期：2026-09-20

已决（见 `answers.md`）：

- **逐指令 ELF**，不按类别（类别里一条未实现仍会整包挂）
- 标量 `add`/`mul` **不**打进向量子集
- **`bench-all.sh` 不改**

## 0. 为什么拆

当前 `make -C instructions/rvv` 把 **全部** RVV 测项打进一个 ELF。`main.c` 按 `bench_names` 顺序逐条跑；DUT 缺任何一条就会 illegal insn / 挂死。

按类别拆不够：`rvv-int-alu` 仍含 `vadd`+`vdiv`+…，缺一条同样废掉整包。需要 **一条助记符一个 ELF**，例如只跑 `vadd` 的 `.vv/.vx/.vi` 及 mask 变体。

## 1. 先构建看到了什么

在本机用现有 `m4 gen.S` + 交叉编译（未改 Makefile）：

| 产物 | 规模 |
|---|---|
| `m4 gen.S` → `main.S` | **355 308 行 / 5.7 MB** |
| 链接后 ELF | **4.3 MB**；`text=1 665 824` `data=105 664` `bss=328 705` |
| `bench_names` 条目 | **1222**（与 `bench_m1` 函数指针数一致） |
| 函数体 | 每条测项 `_m1/_m2/_m4/_m8` 四份（`mf*` 表指向 `_m1`） |
| **独立指令 key** | **338**（`generated/insn_keys.tsv`） |
| 每 key 测项数 | min=1，median=4，max=16（`vfncvt`） |

`m_bench_all` 展开 13 次：7 张指针表 + `bench_types` + `bench_names` + 4 份 LMUL 函数体。C 侧再扫 `SEW×LMUL×ta/ma×vl`，binary 里多一条测项，运行时间近似线性增加。

生成物（不入库）在 `.tasks/splitted-rvv/generated/`。

## 2. 必须守住的不变量

`main.c` 用 **下标** 对齐，不是符号名：

```
bench_names[i]
bench_types[i]
bench_m{f8,f4,f2,1,2,4,8}[i]
```

子集必须让 **所有表和函数体走同一份选择宏**。只裁函数体或只裁 names 会错位。

`randomize` / `run_bench` 是公共运行时，任何子集都留下。`randomize` 本身会执行 `vid/vmul/vadd/vxor/vsrl/vand`——这是原设计，逐指令 ELF 也避免不了（见 §5）。

## 3. 指令 key 规则

一条 Make 目标 = 一个 key = 一个 ELF。key 不是「1222 条 bench 各一个」，而是 **同一助记符的寻址/mask 变体打在一起**（DUT 支持 `vadd` 就会有 `.vv/.vx/.vi` 和 `v0.t`）。

| 规则 | 例子 | 一个 ELF 里有什么 |
|---|---|---|
| 整数/FP：点号前的助记符 | `vadd` | `vadd.vv/.vx/.vi` × {无 mask, `v0.t`} → 6 条 |
| 同上 | `vfadd` | `vfadd.vv/.vf` × mask → 4 条 |
| 同上 | `vfncvt` | 8 种 cvt × mask → 16 条（最大） |
| mask 逻辑 | `vmand` | `vmand.mm` 1 条 |
| 整段搬移 | `vmv1r` | `vmv1r.v` |
| `vcpop` 拆开 | `vcpopm` vs `vcpopv` | `vcpop.m`（mask）与 `vcpop.v`（zvbb）不是同一条指令 |
| load/store 保留 EEW / seg / ff / stride | `vle8`, `vle8ff`, `vlse8`, `vlseg2e8`, `vlseg2e8ff`, `vlsseg2e8` | 各 key 独立；strided 的 `{t3,x0,t1=-1}` 仍同属一个 key |
| 标量基线 | `add`, `mul` | 各自一个 ELF，**不**链进任何向量 ELF |

完整 key 表：`generated/insn_keys.tsv`（338 行：`key \t n_benches \t group \t ops`）。

Make 目标名：`build/rvv-<key>.elf`，例如 `build/rvv-vadd.elf`、`build/rvv-vle8ff.elf`、`build/rvv-vcpopm.elf`。

## 4. 实现方案

不在 C 里按名字 skip：未实现指令仍编进 ELF，跑起来还是会踩到。

### 4.1 `gen.S`：给每条 `m_*` 调用加 insn 标签

现状：`m_bench_all` 里是一长串

```
m_bench_vxi($1, T_A, vadd)
m_bench_vx($1,  T_A, vsub)
...
m_mask($1, bench_vle8v, T_E8, m_mem8, vle8.v, ...)
```

改法（保持全量顺序 bit-identical）：

```
define(`m_emit', `ifelse(BENCH_INSN,`',`m_$2($*)',
                 BENCH_INSN,`$1',`m_$2(shift($*))',
                 `')')

define(`m_bench_all',`
    m_emit(add,  $1, bench_add, T_A, m_nop, add, t0, t1, t2)
    m_emit(mul,  $1, bench_mul, T_A, m_nop, mul, t0, t1, t2)
    m_emit(vadd, bench_vxi, $1, T_A, vadd)
    m_emit(vsub, bench_vx,  $1, T_A, vsub)
    ...
')
```

- 未定义 `BENCH_INSN`（空）：`m_emit` 全部放行 → 与今天的 `m_bench_all` 等价。
- `-DBENCH_INSN=vadd`：只有 `vadd` 那一组进入指针表 / types / names / 函数体。
- 现有 13 处 `m_bench_all(`...`)` **不用改调用点**，过滤发生在 `m_bench_all` 内部。

`m_loadstore` / `m_loadff` / `m_stride` 现在一次吐 e8/e16/e32/e64。逐指令需要 **按 EEW 拆开 emit**（`vle8` 不要带上 `vle16`）。这是 `gen.S` 里唯一非机械的改动。

`vcpop`：两处调用分别 `m_emit(vcpopm, … vcpop.m)` 和 `m_emit(vcpopv, … vcpop.v)`。

### 4.2 `Makefile`

```
# 338 keys，落地为仓库内 instructions/rvv/insns.list（提交），不要依赖 .tasks
INSNS := $(shell cat insns.list)

build/rvv-%.elf: gen.S main.c config.h
	mkdir -p build
	m4 -DBENCH_INSN=$* gen.S > build/$*.S
	${CC} ${CFLAGS} -Wl,--relax build/$*.S main.c -o $@ \
		-T ../../thirdparty/link.ld \
		../../thirdparty/am-riscv64-xs.a \
		../../thirdparty/klib-riscv64-xs.a

insn: $(INSNS:%=build/rvv-%.elf)

run-%: build/rvv-%.elf
	../../run.sh ./build/rvv-$*.elf
```

- `all` / `run` **仍只编完整 `rvv`**（`m4 gen.S`，不定义 `BENCH_INSN`）→ `bench-all.sh` 行为不变。
- 中间 `.S` 放 `instructions/rvv/build/`，不要再 `rm main.S`。
- 338 个 ELF 全编可以，但默认不作为 `all`。需要时 `make insn -j`。

### 4.3 验证

1. 无 `BENCH_INSN` 的 `bench_names` 与 `generated/bench_names.txt` **逐行相同**。
2. `m4 -DBENCH_INSN=vadd`：names 恰好 6 条，全是 `vadd.*`；`bench_m1` 指针数 = 6。
3. `m4 -DBENCH_INSN=vle8`：只有 `vle8.v` ± mask，不含 `vle16` / `vle8ff`。
4. `m4 -DBENCH_INSN=vcpopm` 不含 `vcpop.v`。
5. QEMU 跑 `build/rvv-vadd.elf`，HTML 只有 vadd 行。
6. `add`/`mul` ELF 可单独编；`vadd` ELF 的 names 里 **没有** `add t0,t1,t2`。

## 5. 风险与约束

1. **`randomize` 仍用整数向量指令**。只测 `vfadd` 的 ELF 在进入测项前就会执行 `vadd/vmul/…`。若 DUT 整数向量也没有，该 ELF 同样起不来。本次不改 `randomize`（可列为后续：标量 PRNG）。
2. **setup 宏**：`vrgather` 的 `m_mod_v24_e8_vl` 用 `vremu`；mask 的 `m_1bit` 用 `vid/vmseq`。这些只在对应测项实例化。
3. **`m_loadstore` 拆 EEW** 容易漏 `IF64` / mask / stride 三套。用 §4.3.3 卡住。
4. **空组**：`-DBENCH_INSN=nope` 或 march 关掉 zvbb 时 `vbrev` 可能 0 条。`main.c` 对空 `bench_names` 会直接结束，可接受。
5. **338 ELF 磁盘**：每个远小于全量 4.3 MB，全编可接受；不要提交 `build/`。
6. **不要提交** `generated/main.S`（5.7 MB）。

## 6. 实施步骤

- [x] 1. 提交 `instructions/rvv/insns.list`（339 key：`vcpop` → `vcpopm`/`vcpopv`）
- [x] 2. `gen.S`：加 `m_emit` + `BENCH_INSN`；`m_bench_all` 每条调用套上 key
- [x] 3. 把 `m_loadstore` / `m_loadff` 改成按 EEW（及 seg 宽度）分别 `m_emit`
- [x] 4. `vcpop.m` / `vcpop.v` 分成 `vcpopm` / `vcpopv`
- [x] 5. 无 `BENCH_INSN` 时 names 与 `generated/bench_names.txt` bit-identical（1222）
- [x] 6. Makefile：`build/rvv-%.elf`、`insn`、`run-%`；`all` 仍是完整 `rvv`
- [x] 7. 抽检：`vadd`（6）、`vle8`、`vle8ff`、`vcpopm`、`add` 的 names 集合
- [ ] 8. QEMU 跑 `rvv-vadd`，确认只有 vadd 行且无标量 add（ELF 已编出，text=16 KB；未在本次跑 QEMU）
- [x] 9. 更新 `summary/notes/test-composition.md` §3（子集目标；bench-all 仍全量）

## 7. 非目标

- 改 `bench-all.sh`
- 把 `add`/`mul` 链进向量子集
- 按类别 bundle（`rvv-int-alu` 等）——需要时后续再加
- C 侧 `--only` 运行时过滤
- 补 indexed load/store
- 改 `randomize` / 计时公式 / UNROLL/LOOP

## 8. 工作量

主改 `instructions/rvv/gen.S`（`m_emit` 包装 + load/store 宏拆 EEW）和 `Makefile`。`main.c` / `config.h` 不动。回归锚点是全量 names 的 bit-identical。
