# 集成方法：把新函数加入 libmvec 函数级延迟框架

状态：**WIP（2026-11-17）**。目标：说明如何把 libmvec ABI 函数接入本框架的依赖链延迟测量；本次以 exp/log/sin/cos/pow 为例。

## 1. 前置
- 工具链/库前缀：`--prefix` 指向同时含 `libmvec.a`、`libm.a`、头文件的 sysroot（如 `build-20261115-release/combined-opt-baseline-8a`）。
- emu：`--kind gsim|verilator`，gsim 快模型用于全量，verilator 用于绝对值抽查。
- 依赖链模型与校准：见 `README.zh.md` §1/§6/§10（`sfence.vma` 串行化、缓冲打印、K>0 拟合、K=0 单列）。

## 2. 变体与签名
每个函数 8 个 ABI 变体：`_ZGV{1,2}{N,M}{2,4,x}v_<fn>`：
- m1：`N2`/`M2`（masked 首参 `vbool64_t`）、`Nx`/`Mx`；
- m2：`N4`/`M4`（`vbool32_t`）、`Nx`/`Mx`。
一元的 exp/log/sin/cos：尾参数 `vfloat64m1_t`/`m2_t`；pow 为二元（两个向量参数）。入口声明需加 `__attribute__((riscv_vector_cc))`；masked 变体传全 1 谓词。

## 3. 依赖链 map（保证串行化）
| 函数 | 输出 y | map：x' = |
|---|---|---|
| exp | >0 | `clamp(y,0.1,2.0)-1.0` |
| log | 任意 | `clamp(y,0.1,2.0)` |
| sin/cos | [-1,1] | `y` |
| pow | >0 | `x'=clamp(y,0.5,2.0)`, `y'=x'+0.5` |

map 用 1–2 条向量 intrinsic，`-ffp-contract=off`；K=0/4/8/16/32，每点 1 first+2 warmup+7 steady，`rdcycle` 只包整窗口；斜率=每次调用延迟，截距=循环/测量开销。

## 4. 校验
- 功能：同库标量 `libm` 结果作参考，逐元素 ≤2 ULP（或框架 ORACLE 机制）；失败即判该变体 fail，不可静默跳过。
- 构建审计：objdump 检查调用点/map/回边/空函数体，依赖被优化掉即 `AUDIT_FAILED`。
- 门禁：无 cause=2/BAD TRAP、`LAT_END status=ok`、控制链（nop/空调用/map）校准通过；依赖链 R²≥0.999（斜率可分辨时）。

## 5. 操作步骤（以 exp 为例）
1. 在 driver 中声明 8 个入口 + 续写 8 个 `probe_exp_*()`（照抄 atan 探针，改符号/map/校验）；
2. 每个探针打印 `LAT,entry=exp_m2_Nx,K=...,phase=...,cycles=...,checksum=...` 等（保持现有输出协议，解析器无需改）；
3. 独立调用吞吐模式：同组入口无数据依赖、不同寄存器并行，输出 `LAT,entry=...,mode=throughput`；
4. 构建并 smoke：`build_latency.py --prefix <prefix> --fn exp ...`（按现有脚本参数），再 `run_latency.py --kind gsim`；
5. 解析：`latency_parse.py` 输出 fit/summary CSV；校准：`latency_calibrate.py` 判门禁；
6. 五函数全部通过后，扩展 `--entries`/`--fn` 选择与文档索引。

## 6. 常见坑
- UART 打印极贵（约 1.3e4 cycle/行）：必须缓冲后统一打印，不能把整程序 cycle 当测量值；
- `rdcycle` 不串行化：必须 `--serial 1`（窗口两端 `sfence.vma`）+ nop 长链对照；
- K=0 是不同构型：拟合只用 K>0，K=0 单列；
- masked 变体 inactive 语义：全 1 mask 下与 unmasked 结果一致，但异常/flags 仍需检查；
- verilator 与 gsim 绝对周期可能不同（尤其含 `vfsqrt` 的内核），A/B 只在同模型内比较。

## 7. 待完成（WIP）
- exp/log/sin/cos/pow 的 8 变体探针与 5 个 gsim BIN 尚未落地（前序 agent 多次中断）；后续按 §5 逐函数补齐，再交测量任务跑 45 组。
