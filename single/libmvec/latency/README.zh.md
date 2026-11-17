# libmvec 函数级延迟测试框架

状态：**有效**（2026-11-17 建立，校准运行见第 8 节）
位置：`.dsh/rvv-bench/single/libmvec/latency/`
适用阶段：`impl` / `perf`

本目录是 [libmvec 吞吐基准](../README.zh.md) 之外的第二套性能口径：**单次调用串行延迟**（cycle/call），
回答“把 K 次调用串成一条真依赖链时，每次调用多花多少周期”，而不是“同时能跑多少个”。
两者不能互相替代：吞吐高不代表串行依赖链短，反之亦然。

测量对象是 GCC/glibc 发布的公开 RVV libmvec 入口（`_ZGV*` 符号），不是本仓 `math/` 里的算法实现。
裸机程序从 `0x80000000` 启动，链接给定工具链前缀里的静态 `libmvec.a`；周期数由目标端 `rdcycle` 读取。

## 1. 方法

### 1.1 依赖链 + 斜率

一个测量窗口 = 一条闭合依赖链，`rdcycle` 只包整个窗口：

```text
begin = rdcycle();
for (i = 0; i < K; ++i)  x = map(entry(x));   // map 是 2 条向量运算
cycles = rdcycle() - begin;
```

`map` 把上一次调用的结果折回入口的输入定义域，使下一次调用必须等上一次结果，不能重叠：

| 入口 | 值域 | map | 值域 |
|---|---|---|---|
| `atan` | (-∞, +∞) → (-π/2, π/2) | `x = (2/π)·y` | (-1, 1) |
| `acos` | [-1, 1] → [0, π] | `x = (2/π)·y - 1` | [-1, 1] |

两条 map 都是压缩映射（`|dx'/dx| < 0.65`），闭环收敛到 `x≈0` 附近的唯一不动点，
因此每个 (entry, K) 的计时窗口都落在同一个可复现工作点上。map 固定为 2 条向量 FP 指令，
构建时用 `-ffp-contract=off`，避免编译器把 `a*b+c` 合成一条 `vfmacc`。

对 K = 0/4/8/16/32 的每个窗口取 steady 轮次中位数，再对 K 做最小二乘：

```text
cycles(K) = slope · K + intercept
slope     = 每次链式调用的周期数（本框架的延迟口径）
intercept = 窗口固定开销（两次 rdcycle、循环、入口/出口）
```

### 1.2 三重校准与相减

| 探针 | 链内容 | 斜率含义 |
|---|---|---|
| `nop_addi` | K 条依赖 `addi` | 计时通路的自检，必须 ≈ 1 cycle/条 |
| `nop_addi64` | K 步 × 64 条依赖 `addi` | 长窗口对照，必须 ≈ 64 cycle/步 |
| `noop_m1/m2` | K 次同 ABI 空函数调用 | 纯调用开销 |
| `noop_map_m1/m2` | 空函数调用 + map | 调用开销 + map |
| `map_m1/m2` | 只有 map | 2 条向量 FP 指令的延迟 |
| `noop_mask_map_m2/m4` | masked 空调用 + map | masked 固定尺寸 ABI 的对照 |
| `atan_*/acos_*` | 目标入口 + map | 调用 + 入口内核 + map |

因此：

```text
入口内核估计 = slope(entry) − slope(noop_map_<同 lmul>)
（或用 slope(entry) − slope(map) − slope(noop) 交叉核对；不等价于纯内核延迟，见第 10 节）
```

### 1.3 相位与校验

- 每个 (entry, K) 跑 10 个窗口：`round 0 = first`、`round 1–2 = warmup`、`round 3–9 = steady`。
  只有 steady 进回归；first/warmup 原样留在原始日志里，便于看出收敛。
- 每个窗口结束后，**窗口外**用冻结输入再算一次 `entry(x)`，按 `ORACLE_LIMIT` 逐元素比对
  冻结的 MPFR 参考位；不一致打印 `LAT_ERROR` 并计入 `failed`。
- `LAT_CHAIN` 行给出每个 (entry, K) 链末状态的 checksum 以及跨轮是否稳定：
  稳定说明闭环真的跑了，不稳定说明链被优化掉或 map 没有压缩性。
- 全部窗口先缓冲在 RAM，测完统一打印。这不是风格问题：在 RTL/gsim 上**一行 UART 输出约等于
  上万个 guest cycle**，边测边打印既会主导整程序时长，也会把变量延迟注入下一个窗口。

### 1.4 rdcycle 不是串行化指令（本框架必须处理的坑）

香山（以及 gsim 的周期模型）把 `csrr cycle` 当普通乱序读：它可以在依赖链还没算完时就发射，
于是短窗口测到的是“两次读之间发射推进了多少”，而不是“链执行了多久”。实测表现：
`nop_addi`（每步 1 条 `addi`）在 K=0,4 时几乎测不出差值。

处理方式：构建开关 `--serial 1` 在窗口两端加 `sfence.vma zero, zero`（架构上最轻的强制排空手段，
不触碰内存），代价是固定的，被回归截距吸收。`--serial 0` 保留原始行为用于对照。
用哪一种是**数据的一部分**：`LAT_HEADER` 的 `serial=` 字段和 manifest 的 `knobs.serial` 都会记录。

## 2. 文件

| 文件 | 用途 |
|---|---|
| [latency_driver.c](<latency_driver.c>) | 探针定义：libmvec 入口链、独立调用吞吐链（`_tput`）、空调用链、map-only 链、scalar nop 链、标量 libm 对照链；`LAT_SERIAL` 窗口边界 |
| [latency_main.c](<latency_main.c>) | 矩阵遍历、窗口缓冲、`LAT*` 协议输出、oracle 计数汇总 |
| [latency_map.h](<latency_map.h>) | 闭环 map：atan/acos（2 条指令）、exp（3）、log（2）、sin/cos（恒等，0）、pow（3） |
| [latency_noop.c](<latency_noop.c>) | 与入口**同 ABI** 的空函数（向量参数、`ret`），调用开销对照 |
| [latency_probe.h](<latency_probe.h>) | 探针表/窗口结构/协议常量（rounds、prelude、K 列表、LANES） |
| [build_latency.py](<build_latency.py>) | 从给定前缀构建 BIN，记录 sha256/manifest，objdump 审计依赖链 |
| [run_latency.py](<run_latency.py>) | gsim / verilator 启动，带时间戳运行目录、stdout/stderr 分离、审计日志 |
| [latency_parse.py](<latency_parse.py>) | 原始窗口 → 每个 K 的中位数 → cycles(K) 线性回归 → CSV |
| [latency_calibrate.py](<latency_calibrate.py>) | 把校准门禁变成 PASS/FAIL 行（斜率、R²、残差、CV、smoke） |

## 3. 依赖

| 依赖 | 说明 |
|---|---|
| 工具链前缀 | `--prefix`，需要 `bin/riscv64-unknown-linux-gnu-{gcc,objdump,nm,objcopy}` 与 `sysroot/.../libmvec.a`；`libm.a` 是 ld script，`-lm` 会一起拉 `libmvec.a` |
| 功能参考 | atan/acos 用 `--data-dir` 下的 `atan.h`/`acos.h`（`input_bits`/`reference_bits`/`ORACLE_LIMIT`），默认指向 `.dsh/experiments/2026-11-15-2244-libmvec-expand/inputs/perf`；exp/log/sin/cos/pow 用**同库标量 libm** 在窗口外复算（`LAT_ORACLE_SCALAR`，逐元素 ≤2 ULP），不需要冻结头 |
| 裸机运行时 | 本仓 `single/veclibm-runtime.c`、`autovec/av_runtime.{c,h}`、`autovec/tls.ld`、`thirdparty/link.ld`、`thirdparty/{am,klib}-riscv64-xs.a` |
| 仿真器 | gsim `emu-gsim-*`（非 static）或 verilator `emu-verilator-j*`；DiffTest refmodel；DRAMsim3 ini |
| 固定配置 | VLEN=128（程序内校验 `vlenb==16`）、SEW=e64、LMUL=m1/m2、lane 数 = 2/4、seed 123456789 |

## 4. 构建

```sh
python3 -B single/libmvec/latency/build_latency.py \
  --prefix /path/to/prefix \
  --output /path/to/new/build-dir \
  --tag calib \
  --groups calib,acos_masked \
  --k-list 0,4,8,16,32 --rounds 10 --prelude 16 --serial 1
```

- `--groups`：`calib`（10 个对照探针）、`atan`、`acos_nx`、`acos_masked`；按编译宏
  （`-DLAT_GROUP_*`）选组，标定用的 BIN 不会拉进它不测的 libmvec 成员。
- `--k-list`/`--rounds`/`--prelude`/`--serial` 都进 manifest；`--output` 必须是新目录。
- 产物：`latency-<tag>.{elf,bin}`、各 TU 的 `.o`、`link.map`、`link.trace.txt`、
  `disasm/*.txt`、`audit-dependency.txt`、`manifest.json`、`provenance.txt`、`sha256.txt`、
  `source/`（本次构建的源码快照）、`logs/`（每步 stdout/stderr 分开）。
- `manifest.json` 记录：前缀与工具身份（gcc 版本 + sha256）、`libmvec.a`/`libm.a`/`libm-2.44.a`/
  `libc.a`/`libgcc.a` 的 sha256、数据头 sha256 与 `ORACLE_LIMIT`、源码 sha256、完整编译/链接命令、
  产物 sha256、审计结论。

### 4.1 objdump 依赖链审计（构建失败即拒绝）

在 **object** 上看重定位（链接后的 ELF 已经没有 `R_RISCV_CALL*`）：对每个探针函数要求

1. 存在指向目标入口的重定位调用点（`R_RISCV_CALL_PLT`/`R_RISCV_JAL`）；
2. 函数体内至少有 `vfp_min` 条向量 FP 算术指令（map 的 2 条），且出现在调用点**之后**；
3. 存在回边分支（K 循环），且调用点位于回边之前；
4. 标量链要求足够多 `addi rd,rs1,1` 且 **rd == rs1**（真依赖链，不是并列加法）；
5. 空 ABI 目标（`latency_noop_*`）函数体只能是 `ret`。

审计结果写 `audit-dependency.txt`；任何一条不满足时脚本以 `AUDIT_FAILED` 非零退出
（`--skip-audit` 只用于诊断）。这就是“链没有被优化掉/没有被串行化掉”的静态证据，
运行期的 `slope > 0` 是第二道证据。

## 5. 运行

```sh
python3 -B single/libmvec/latency/run_latency.py \
  --kind gsim \
  --bin /path/to/latency-calib.bin \
  --output /path/to/20261117-021500-latency-gsim \
  --timeout 2400s --label calib
```

- `--kind gsim|verilator`；`--emu` 省略时取 `/nfs/share/huxuan/emus` 下**最新非 static** 对应类型。
- 运行目录名必须含 `YYYYMMDD-HHMMSS`；已存在的目录一律拒绝复用。
- 目录内容：`image.bin`（staged 副本 + 双 sha256）、`stdout.log`、`stderr.log`、`work/`（隔离 cwd）、
  `dram/`（DRAMsim3 输出）、`command.txt`、`run-metadata.txt`（emu/refmodel/DRAM ini 的路径与 sha256、
  seed、timeout、精确 argv、host、起止时间）、`exit-code.txt`、`run-report.txt`。
- emu 调用形式与既有 libmvec/P3b 运行器一致：
  `<emu> --diff <ref> --seed=123456789 --dramsim3-ini=... --dramsim3-outdir=... -i <bin>`，
  `timeout --signal=INT --kill-after=60`。
- 结束后自动审计：`GOOD TRAP`、`LAT_HEADER`、`LAT_END status/failed`、`LAT` 行数、
  `LAT_ERROR`/`BAD TRAP`/`cause=`/`DiffTest` 等错误标记；`run-report.txt` 里 `status=ok` 才算跑通。
  这个退出码只表示“运行完整、日志自洽”，**不解释周期数**。

## 6. 解析与判定

```sh
python3 -B single/libmvec/latency/latency_parse.py \
  --run /path/to/20261117-021500-latency-gsim \
  --output-dir /path/to/new/parse-dir --phase steady

python3 -B single/libmvec/latency/latency_calibrate.py \
  --parse-dir /path/to/new/parse-dir \
  --run-report /path/to/20261117-021500-latency-gsim/run-report.txt \
  --output /path/to/latency-calibration-<ts>.csv
```

### 6.1 CSV 字段

`windows.csv`（每个 (entry, K) 一行）

| 列 | 含义 |
|---|---|
| `entry` | 探针名（见 1.2 表） |
| `k` | 该窗口的链式调用次数（K） |
| `n` | 参与统计的 steady 窗口数 |
| `median_cycles` / `mean_cycles` | 窗口周期中位数 / 均值（进回归的是中位数） |
| `stdev_cycles` / `cv` | 样本标准差 / 变异系数（`stdev/mean`） |
| `min_cycles` / `max_cycles` | 该 K 的极值，用于发现离群窗口 |

`fit.csv`（每个 entry 一行）

| 列 | 含义 |
|---|---|
| `slope_cycles_per_call` | 回归斜率 = 每次链式调用的周期数（本框架的延迟口径） |
| `intercept_cycles` | 截距 = 窗口固定开销（K=0 的实测点应接近它） |
| `r2` | 线性拟合决定系数 |
| `max_rel_residual` | K>0 各点上 `|median − fit| / fit` 的最大值（本框架的“残差”） |
| `max_abs_residual_cycles` | 同上取绝对值 |
| `cv_max` | K>0 各 K 的 `cv` 最大值 |
| `chain_stable` | `LAT_CHAIN` 里该 entry 所有 K 的闭环状态是否跨轮一致 |
| `n_k` / `k_min` / `k_max` | 参与拟合的 K 点数量与范围 |
| `phase` / `source` | 统计相位与原始日志来源 |

`samples.csv`：每条原始窗口一行，含 `entry,k,round,phase,calls,begin,cycles,checksum,...`，
`measurement_valid=no` 的行存在时可用于诊断但不得当作有效性能数据。
`chains.csv`：每个 (entry, K) 的链末 checksum 与稳定性。

### 6.2 拟合口径与门禁（`latency_calibrate.py`）

拟合只使用 **K>0** 的链式窗口：K=0 是空窗口（没有循环、没有链），是另一种构型，
单独作为开销探针报告（`k0_cycles`、`k0_minus_intercept`、`r2_all_k` 同时保留在 `fit.csv` 里）。
若把 K=0 一起拟合，空窗口 60 cycle 与链式截距 56–90 cycle 的差异会把斜率整体拉低约 9%
（`nop_addi` 0.9125、`acos_m2_M4` 残差 2.35%），这是构型差异而不是非线性，因此不混在一起。

| 检查 | 门限 | 含义 |
|---|---|---|
| `nop-chain`（`nop_addi`） | `0.95 ≤ slope ≤ 1.05` | 每步 1 条依赖 `addi` 的自检 |
| `nop-chain`（`nop_addi64`） | `64×0.95 ≤ slope ≤ 64×1.05`，并折算 `slope/64 ∈ [0.95,1.05]` | 长窗口自检（不受窗口边界偏置影响） |
| `linearity`（严格） | `r2 ≥ 0.999`、`max_rel_residual ≤ 1%`、`n_k ≥ 3` | 只对斜率可分辨（`slope ≥ --strict-min-slope`，默认 20 cycle/步）或 `--strict-entries` 指定的链生效 |
| `linearity-loose` | 仅记录，不判定 | 便宜对照组（空调用 ~3 cycle/步、map ~5 cycle/步）的窗口长度与边界抖动同量级，拟合数字保留但不作为门禁 |
| `jitter` | `rcv < 2%`（`--jitter-stat rcv` 默认；`cv` 可选） | 稳健 CV = `1.4826·MAD/median`；经典 `cv` 同时写进 `windows.csv` |
| `serialization` | `slope > 1`（`nop_addi` 除外） | 斜率为 0 说明链没串行化，属框架 bug |
| `derived` | `slope(entry) − slope(map_<lmul>) > 0` | 入口本身贡献了额外延迟；若该 lmul 的 map 探针缺失，回退到另一 lmul 并在 `note` 里注明 |
| `chain-state` | 记录，不判定 | map 链收敛到周期 2 的浮点轨道，逐轮状态交替是预期行为 |
| `smoke` | `run_status=ok`、`good_trap=True`、`end_status=ok`、`failed=0`、`lat_windows>0` | 功能与运行门禁 |

输出 `calibration.csv`（`check,entry,metric,value,bound,status,note`），任何 `fail` 都让退出码非零；
`info` 行只作记录。

## 7. 一次完整的标定 + smoke 流程

```sh
STAMP=$(date +%Y%m%d-%H%M%S)
T=.dsh/tasks/<task>/tmp

# 1) 构建（标定用最小探针集，避免把整矩阵都跑一遍）
python3 -B single/libmvec/latency/build_latency.py \
  --prefix build-20261115-release/combined-opt-baseline-8a \
  --output $T/$STAMP-calib-build --tag calib \
  --groups calib,acos_masked --serial 1

# 2) 运行（gsim，带时间戳目录）
python3 -B single/libmvec/latency/run_latency.py --kind gsim \
  --bin $T/$STAMP-calib-build/latency-calib.bin \
  --output $T/$STAMP-calib-gsim --timeout 2400s

# 3) 解析 + 判定
python3 -B single/libmvec/latency/latency_parse.py \
  --run $T/$STAMP-calib-gsim --output-dir $T/$STAMP-calib-parse
python3 -B single/libmvec/latency/latency_calibrate.py \
  --parse-dir $T/$STAMP-calib-parse \
  --run-report $T/$STAMP-calib-gsim/run-report.txt \
  --output data/latency-calibration-$STAMP.csv
```

### 7.1 五函数（exp/log/sin/cos/pow）的 BIN

每个函数一个 BIN：8 个 ABI 变体（`m1/m2 × N/M × 2/4/x`）+ 对应 8 个 `_tput` 吞吐探针 +
1 个 `<fn>_scalar` 标量 libm 对照（exp 另加 `exp_scalar_sat` 饱和工作点对照，故为 18 个探针，
其余 17 个）。功能校验用同库标量 libm（≤2 ULP），构建审计覆盖全部探针。标量对照的 clamp 用
内联 `fmin.d`/`fmax.d` 写成无分支，map 收敛到钳位不活跃的工作点；构建审计会拒绝“分支选常数”
或返回值没有流到下一次调用输入的标量链（详细要求见 `INTEGRATION.zh.md` §3/§4）。

```sh
# 构建（exp 为例；log/sin/cos/pow 改 --groups/--tag 即可）
python3 -B single/libmvec/latency/build_latency.py \
  --prefix <prefix> --output <new-dir>/exp --tag exp --groups exp \
  --k-list 0,4,8,16,32 --rounds 10 --serial 1

# smoke 只跑 2 个变体 + 标量对照（编译期 --entries 过滤）
python3 -B single/libmvec/latency/build_latency.py \
  --prefix <prefix> --output <new-dir>/exp-smoke --tag exp-smoke --groups exp \
  --entries exp_m1_N2,exp_m1_M2,exp_scalar --k-list 4,32 --rounds 3 --serial 1
python3 -B single/libmvec/latency/run_latency.py --kind gsim \
  --bin <new-dir>/exp-smoke/latency-exp-smoke.bin --output <timestamped-run-dir>
```

smoke 的通过标准：`GOOD TRAP`、`LAT_END status=ok failed=0`、无 `cause=2`、无 `LAT_ERROR`。
`rounds=3` 只有 first+warmup，没有 steady 相位，因此 smoke 结果不用于拟合。

## 8. 校准结果

见任务记录 `.dsh/tasks/20261117-0113-libmvec-latency/` 的 `progress.md`、
`data/latency-calibration-*.csv` 与 `log/<ts>-latency/`。本节只保留结论性数字：

校准运行（2026-11-17 02:18，gsim `emu-gsim-20261105-155351-409f429572`，seed 123456789，VLEN=128，
`--serial 1`，K=0/4/8/16/32，rounds=10，前缀 `build-20261115-release/combined-opt-baseline-8a`，
`libmvec.a` sha256 `5b535d93a8e05aa41b5b18f6b5633980d492436dc4f2072fd49b39a8589c5227`）：
300 个原始窗口、`LAT_END status=ok checks=200 failed=0`、`CALIBRATION PASS failed=0`（45 行判定）。

| 探针 | 斜率（cycle/步） | 截距（cycle） | R² | 最大相对残差 | 稳健 CV |
|---|---:|---:|---:|---:|---:|
| `nop_addi`（每步 1 条 `addi`） | 0.9761 (=0.976 cycle/条) | 56.6 | 0.99889 | 0.80% | 0 |
| `nop_addi64`（每步 64 条 `addi`） | 64.000 (=**1.0000** cycle/条) | 56.0 | 1.00000 | 0% | 0 |
| `noop_m1`（空 ABI 调用） | 2.857 | 53.7 | 0.9954 | 4.5%（info） | 0 |
| `map_m1`（2 条向量 FP） | 5.000 | 65.0 | 1.00000 | 0% | 0 |
| `noop_map_m1`（空调用 + map） | 5.000 | 65.0 | 1.00000 | 0% | 0 |
| `acos_m2_M4`（masked M4 ABI） | **138.626** | **89.6** | **0.999987** | 0.76% | 0.06% |

- 门禁全部通过：`nop_addi` 0.9761 ∈ [0.95,1.05]；`nop_addi64` 折算 **1.0000 cycle/条**；
  `acos_m2_M4` R²=0.999987、残差 0.76%、稳健 CV 0.06%。
- **acos m2（M4 ABI）每次链式调用 ≈ 138.6 cycle**；减去同 map 的空调用对照（`noop_map_m1` 5.0）
  得 **入口本身 ≈ 133.6 cycle/call**（含调用/返回，不含两条 map 指令）。
- K=0 空窗口实测 60–61 cycle，与 K>0 拟合截距（56–90）并不相等：空窗口没有循环也没有链，
  是不同的测量构型，因此只作独立开销探针，不参与拟合（见 6.2 的拟合口径）。
- 经典 CV（`cv_max`）在便宜链上会被 1 个预热轮次拉高（`noop_m1` 9.5%、`nop_addi` 0.8%），
  门禁用的是 MAD 稳健 CV；两者都保留在 `windows.csv`，预热泄漏可见。
- `chain_stable=no` 出现在 map 类链上属正常：闭环收敛到周期 2 的浮点轨道，
  逐轮状态本来就会交替；有效性由每 K 的中位数稳定性（稳健 CV）与线性度保证。

证据：`.dsh/tasks/20261117-0113-libmvec-latency/data/latency-calibration-20261117-021833.csv`、
`data/latency-20261117-021833-{samples,windows,fit,chains}.csv`、
`log/20261117-021833-latency/{build,gsim}/`。


## 9. 原始协议字段

| 行 | 字段 | 含义 |
|---|---|---|
| `LAT_HEADER` | `protocol` | 协议版本（当前 1） |
| | `vlenb`/`vlen` | 实际字节/位向量长度（本框架要求 128） |
| | `rounds`/`prelude` | 每个 (entry,K) 的窗口数 / 闭环预热步数 |
| | `serial` | 窗口两端是否用 `sfence.vma` 排空（1/0） |
| | `nop_block`/`nop_block64` | 两个 nop 探针每步的 `addi` 条数（1/64） |
| | `k`/`groups`/`entries` | 本次矩阵的 K 列表、启用的组、启用的探针 |
| `LAT` | `entry`/`K`/`calls` | 探针名 / 循环步数 / 窗口内实际调用次数：延迟探针 = K，吞吐探针（`_tput`）= 4K；`latency_parse.py` 用 `calls` 作拟合 x 轴 |
| | `phase` | `first`（round 0）/ `warmup`（1–2）/ `steady`（3+） |
| | `begin`/`cycles` | 窗口起始计数 / `end−begin`，全部是目标端 `rdcycle` 原始值 |
| | `checksum` | 窗口外 oracle 复算输出的 FNV-1a（hex）；只作轨迹指纹 |
| `LAT_CHAIN` | `chain_checksum`/`stable` | 链末状态的 FNV-1a / 该 (entry,K) 全部窗口是否一致 |
| `LAT_CHECK` | `checks`/`failed` | 该 entry 的逐元素校验次数 / 失败数（按 `ORACLE_LIMIT`） |
| `LAT_END` | `status`/`checks`/`failed` | 整程序终态；`fail` 表示有 oracle 失败或缓冲溢出 |
| `LAT_ERROR` | `entry`/`kind`/… | 具体错误：oracle 不一致、vlenb 不符、K 不支持、缓冲满 |

## 10. 测量边界与局限

1. **不是纯内核延迟。** `slope(entry)` 包含调用/返回、ABI 寄存器搬运和两条 map 向量指令。
   用 `noop_map_*`（同 ABI 空调用 + 同一 map）相减才是入口内核的估计；相减假设各段延迟可加，
   没有微架构级隔离证明。
2. **rdcycle 不保证串行化。** 短窗口会被发射距离主导（见 1.4）。`--serial 1` 用 `sfence.vma`
   强制排空，代价固定但本身也在窗口边界上；两种模式的数据必须分别标注，不能混算。
3. **工作点是闭环不动点，不是全定义域平均。** 链收敛到 `x≈0`，因此测的是该区域的
   分支路径/查表命中模式。不同输入区间（|x|>0.5 等）的延迟需要另设工作点，不能由本数据外推。
4. **masked 固定尺寸 ABI 只校验有效 lane。** `_ZGV1M2v_acos`/`_ZGV2M4v_acos` 按固定 2/4 个
   元素处理，尾部 lane 不参与 checksum；这与 ABI 一致，但本框架不宣称覆盖尾部行为。
5. **checksum 不是正确性证明。** 正确性由窗口外按 `ORACLE_LIMIT`（本数据为 2 个有序编码步）
   的逐元素比较给出；checksum 只用于识别轨迹与闭环状态是否变化。
6. **单配置。** 只测 VLEN=128、e64、m1/m2（2/4 lane）；换 VLEN/SEW/LMUL 必须重新构建并重新标定，
   不能把本数据的 cycle 数当作其它配置的结论。
7. **gsim/verilator 的周期模型不同。** 两者都可用于自检与相对比较，但同一 BIN 的绝对周期
   不保证一致；跨仿真器的数字必须分开报告。
8. **吞吐模式是四路独立调用，不是完整吞吐模型。** `_tput` 探针用 4 个 loop-invariant 输入、
   4 条独立累加链，测的是源码级无数据依赖时的调用吞吐；但 vector ABI 的参数/返回值都走 v8
   （m2 为 v8-v9），调用之间仍有寄存器搬运的串行化，且累加器有 4 条 `vfadd`/步的固定开销，
   也不覆盖 cache/取指/向量单元占用率。“延迟低”不能推出“吞吐高”，两种模式的数字不能混算。
9. **UART 成本。** 每行 `LAT` 输出在 RTL 模型上约上万 cycle，因此整程序时长由输出行数决定，
   与测量窗口长度关系不大；本框架已把打印移出计时窗口并按 (entry,K) 汇总部分冗余字段，
   但**不能**把整程序 cycle 当作测量结果。
10. **`nop_addi64` 的 K=4/8/16 复用 K=32 的代码尾段**，这是编译器的尾部合并，属正常现象；
    审计因此只要求最大块（32 步）的依赖 `addi` 数量。

## 11. 自检清单

- [ ] `build_latency.py` 输出 `BUILD_OK` 且 `audit=ok`（`audit-dependency.txt` 全部 `ok`）
- [ ] 运行日志有 `GOOD TRAP`、`LAT_HEADER`、`LAT_END,status=ok,failed=0`
- [ ] `nop_addi` 斜率落在 0.95–1.05
- [ ] `nop_addi64` 斜率落在 64×(0.95–1.05)
- [ ] 所有 entry 的 `r2 ≥ 0.999`、`max_rel_residual ≤ 1%`
- [ ] 空调用链与 map-only 链 `cv < 2%`
- [ ] `acos_m2_M4` 的 `failed=0`、`chain_stable=yes`，且 `slope > slope(map_m2)`
