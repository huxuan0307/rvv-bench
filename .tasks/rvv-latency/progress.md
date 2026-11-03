# RVV latency 实施记录

日期：2026-10-11

## 已完成

- 新增独立 `instructions/rvv/latency_gen.py`，从当前 `gen.S`/CPP 指令 census 生成 latency 汇编。
- 新增 `instructions/rvv/latency_main.c`，固定 e32/m1、VLMAX、`tama/tumu`，依次运行链长 4/16/64 并输出 raw CSV。
- 新增 Makefile 目标：`latency`、`run-latency` 和 `build/latency.elf`。
- `run-latency` 使用 `riscv64-nemu-interpreter -b ./build/latency.bin`，输出重定向到 `./build/latency.log`；`NEMU`、`LATENCY_CASE`、`LATENCY_LOG` 均可覆盖。
- 实现并编译通过的模板：基础普通向量直接链、向量到 mask 的 masked-vadd 链、`vcpop.m`/`vfirst.m`、`viota.m`、基础 unit/strided/2-field segment load 链、mask-to-mask 链。
- move 类不作为 latency 目标：`vmv*`、`vfmv*`；`vmv.x.s` 只在 load 模型中作为中介。

## 当前证据

- `make -B build/latency.elf QUICK=1`：通过。
- 当前保守模板生成 237 个形式，256 个复杂/排除形式由生成器输出到 stderr 清单。
- ELF 规模：text=132602，data=11808，bss=328705。
- 反汇编确认链长 4/16/64 函数和 `rdcycle` 起止边界存在。
- `run-latency` 按约定使用 NEMU 的 `.bin` 镜像；QEMU/Linux-user 的 `run.sh` 不作为该裸机 latency 用例的验收入口。
- 修复前 NEMU 使用 `.bin` 运行时实际执行约 1,083,258 条 guest 指令，并在 `vfslide1up.vf` 处进入 critical trap；该结果已由下方的前缀二分和最小复现解释。

## 2026-10-11 错误定位

- 前缀二分定位到第 78 个 form：加入 `vfslide1up.vf` 后复现；第 77 个 form 前缀通过，单独的 `vfslide1down.vf` 通过，单独的 `vfslide1up.vf` 触发 critical trap。
- 根因是通用模板生成了 `vfslide1up.vf v8, v8, ft0`。`vslideup`/`vslide1up` 的目的向量寄存器组不能与源向量寄存器组重叠；同类 `vrgather` 也不能机械生成 `vd=vs2`。
- 已在 `latency_gen.py` 增加非重叠专用模板：目的/源在 `v8` 与 `v16` 间交替，保持真实 RAW 依赖；`vslideup.vi`/`vrgather.vi` 使用立即数 1，避免 e32/m1 的 VLMAX 下索引/位移超出有效元素而失去依赖。
- 寄存器重叠修复后、latency 包装前，`make -B build/latency.elf QUICK=1 && make run-latency QUICK=1` 通过；NEMU 执行 `20,403,398` 条 guest 指令并报告 `nemu_trap case 0`、`HIT GOOD TRAP`，无 critical trap。
- 上述包装前 `build/latency.log` 解析得到 `11,376 = 237 forms * 2 policies * 3 chain lengths * 8 samples` 条完整 CSV 行；链长覆盖 `4/16/64`，策略覆盖 `tama/tumu`，无 malformed 行。

## 2026-10-11 latency 包装

- 每个 `latency_<form>_c<chain>` 现在与 `gen.S` 的 throughput 函数一致：先执行 `WARMUP` 轮完整依赖链，再执行 `LOOP` 轮、每轮展开 `UNROLL` 个完整依赖链，最后用 `rdcycle` 包住计时区间。
- `latency_main.c` 输出 `warmup/unroll/loop`，并将 `body_insns` 改为计时区间内的实际展开指令数；预热不计入分母。
- Makefile 支持 `LATENCY_WARMUP`、`LATENCY_UNROLL`、`LATENCY_LOOP`、`LATENCY_RUNS`；未显式设置时分别采用默认 `1000/8/64/8`，`QUICK=1` 采用 `16/8/16/2`。同时兼容用 `WARMUP/UNROLL/LOOP/RUNS` 作为覆盖变量。
- `QUICK=1` 运行通过：`2844 = 237 forms * 2 policies * 3 chain lengths * 2 samples`，NEMU 报告 `HIT GOOD TRAP`，无 critical trap；CSV 参数为 `warmup=16, unroll=8, loop=16`，所有 `body_insns` 算术校验通过。
- 自定义参数 `LATENCY_WARMUP=2 LATENCY_UNROLL=3 LATENCY_LOOP=4 LATENCY_RUNS=1` 已通过构建和反汇编确认，`latency_0_c4` 展开为 2 轮预热和每轮 3 个 4 指令链。

## 下一步

- 为当前 237 个形式建立分类清单和反汇编抽检，优先确认 bridge/load 链的寄存器依赖。
- 补齐普通宽窄、reduction、permute、segment 多字段和 carry-in 的专用合法模板。
- 在可运行的裸机/emulator 环境验证 CSV 样本、VLMAX 和链长线性关系。
