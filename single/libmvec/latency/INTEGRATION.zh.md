# 集成方法：把新函数加入 libmvec 函数级延迟框架

状态：**已实现（2026-11-17，commit 1487bc8 起）**。exp/log/sin/cos/pow 五函数的 8 变体、吞吐模式与标量 libm 对照已落地；本文记录接入方法与已知缺口。

## 1. 前置
- 工具链/库前缀：`--prefix` 指向同时含 `libmvec.a`、`libm.a`、头文件的 sysroot（如 `build-20261115-release/combined-opt-baseline-8a`）。
- emu：`--kind gsim|verilator`，gsim 快模型用于全量，verilator 用于绝对值抽查。
- 依赖链模型与校准：见 `README.zh.md` §1/§6/§10（`sfence.vma` 串行化、缓冲打印、K>0 拟合、K=0 单列）。

## 2. 变体与签名
每个函数 8 个 ABI 变体：`_ZGV{1,2}{N,M}{2,4,x}v_<fn>`：
- m1：`N2`/`M2`（masked 首参 `vbool64_t`）、`Nx`/`Mx`；
- m2：`N4`/`M4`（`vbool32_t`）、`Nx`/`Mx`。
一元的 exp/log/sin/cos：尾参数 `vfloat64m1_t`/`m2_t`；pow 为二元（两个向量参数，符号多一个 `v`，如 `_ZGV1N2vv_pow`）。入口声明需加 `__attribute__((riscv_vector_cc))`；masked 变体传全 1 谓词。
固定尺寸 `N2/N4/M2/M4` 与变长 `Nx/Mx` 都不接收 `vl` 参数，入口内部自行设定；实测在 VLEN=128 下 `N2≡Nx`、`N4≡Nx`，但仍是不同符号，必须分别声明/调用。

## 3. 依赖链 map（保证串行化）
| 函数 | 输出 y | map：x' = |
|---|---|---|
| exp | >0 | `clamp(y,0.1,2.0)-1.0` |
| log | 任意 | `clamp(y,0.1,2.0)` |
| sin/cos | [-1,1] | `y` |
| pow | >0 | `x'=clamp(y,0.5,2.0)`, `y'=x'+0.5` |

map 用 0–3 条向量 intrinsic（sin/cos 恒等映射为 0 条；log 为 max+min 两条；exp 为 max+min+sub 三条；pow 为 max+min+add 三条），`-ffp-contract=off`；K=0/4/8/16/32，每点 1 first+2 warmup+7 steady，`rdcycle` 只包整窗口；斜率=每次调用延迟，截距=循环/测量开销。

除依赖链延迟外，每个变体还有一个**独立调用吞吐**探针（entry 名后缀 `_tput`）：窗口内 4 个 loop-invariant 输入向量放在 4 组寄存器，每次迭代连续发 4 个互不依赖的调用，结果只进 4 条独立累加链（不回喂下一次调用）。`LAT` 行的 `calls=4*K`，`latency_parse.py` 按 `calls` 拟合，因此两种模式都输出 cycle/call。

## 4. 校验
- 功能：同库标量 `libm` 结果作参考，逐元素 ≤2 ULP；失败即判该变体 fail，不可静默跳过。
  实现在 `latency_driver.c` 的 `LAT_ORACLE_SCALAR`：窗口外对同一冻结输入调用标量 `exp/log/sin/cos/pow`，把输出位模式按有序编码比较，`>LAT_SCALAR_ULP`（默认 2）记 `LAT_ERROR`。atan/acos 继续用冻结 MPFR 头（`LAT_DATA_HEADER`）。
- 标量对照链：每个函数还有一个 `<fn>_scalar` 探针，标量 libm 调用 + 同一闭环 map，斜率给出标量库的 cycle/call 基线。
- 构建审计：objdump 检查调用点/map/回边/空函数体，依赖被优化掉即 `AUDIT_FAILED`。
- 门禁：无 cause=2/BAD TRAP、`LAT_END status=ok`、控制链（nop/空调用/map）校准通过；依赖链 R²≥0.999（斜率可分辨时）。

## 5. 操作步骤（以 exp 为例）
1. 在 driver 中声明 8 个入口 + 续写 8 个 `probe_exp_*()`（照抄 atan 探针，改符号/map/校验）；
2. 每个探针打印 `LAT,entry=exp_m2_Nx,K=...,phase=...,cycles=...,checksum=...` 等（保持现有输出协议，解析器无需改）；
3. 独立调用吞吐模式：entry 名后缀 `_tput`，与延迟探针同表，`LAT` 的 `calls=4*K`；
4. 构建并 smoke（每函数一个 BIN，含 8 变体 + 8 吞吐 + 标量对照）：
   ```sh
   python3 -B single/libmvec/latency/build_latency.py \
     --prefix <prefix> --output <new-dir>/exp --tag exp --groups exp \
     --k-list 0,4,8,16,32 --rounds 10 --serial 1
   python3 -B single/libmvec/latency/run_latency.py --kind gsim \
     --bin <new-dir>/exp/latency-exp.bin --output <run-dir-with-timestamp>
   ```
   只想 smoke 2 个变体时，构建加 `--entries exp_m1_N2,exp_m1_M2,exp_scalar --k-list 4,32 --rounds 3`；
5. 解析：`latency_parse.py --run <run-dir> --output-dir <parse-dir>` 输出 samples/windows/fit CSV（fit 的 x 轴是 `calls`）；校准：`latency_calibrate.py` 判控制链门禁；
6. `--groups` 已支持 `calib,atan,acos_nx,acos_masked,exp,log,sin,cos,pow`；`--entries` 是编译期 allow list。

## 6. 常见坑
- UART 打印极贵（约 1.3e4 cycle/行）：必须缓冲后统一打印，不能把整程序 cycle 当测量值；
- `rdcycle` 不串行化：必须 `--serial 1`（窗口两端 `sfence.vma`）+ nop 长链对照；
- K=0 是不同构型：拟合只用 K>0，K=0 单列；
- masked 变体 inactive 语义：全 1 mask 下与 unmasked 结果一致，但异常/flags 仍需检查；
- verilator 与 gsim 绝对周期可能不同（尤其含 `vfsqrt` 的内核），A/B 只在同模型内比较。

## 7. 落地状态
- 已实现：exp/log/sin/cos/pow，每函数 8 ABI 变体 + 8 吞吐探针 + 1 标量 libm 对照；`--groups` 可直接选。
- 功能校验：同库标量 libm，逐元素 ≤2 ULP，失败走 `LAT_ERROR`/`LAT_END status=fail`，不跳过。
- 构建审计：`AUDIT` 表覆盖 17×5=85 个探针（map 指令数、call site、回边、符号拉取）。
- 缺口：尚未跑正式 45 组测量矩阵；verilator 未抽查新函数；`latency_calibrate.py` 的派生延迟检查仍默认只认
  `atan_`/`acos_` 前缀（新函数需要显式 `--entry-prefixes`/`--strict-entries`）；吞吐探针的累加器会引入
  每调用固定的几条 `vfadd` 开销，且 vector ABI 的 v8/v8-v9 参数返回寄存器会带来调用间搬运串行化，
  只能与同模式数字比较，不能直接当纯吞吐。
- 已修 bug：吞吐探针最初把累加器当函数输出做 oracle 校验，K=0 时输出为 0、K>0 时为求和，导致
  `exp_m1_N2_tput` 40/40 校验失败（`LAT_END status=fail`）。现在累加器只做 chain checksum，
  功能校验改为在窗口外对冻结输入做一次真实调用。
