# 数学库代码与工具使用索引

> 状态：当前入口（2026-11-19，函数优先布局）。所有仓库命令从 rvv-bench 根目录执行；旧实验位于
> `single/libmvec/` 根下的脚本路径已经迁移或移除，不能照抄旧 README。

## 入口

| 目的 | 入口 | 输入/输出与限制 |
|---|---|---|
| 构建安装版 libmvec 吞吐镜像 | [script/build.py](script/build.py) | `--prefix`、`--data-dir`、`--case`、`--profile`、`--output`；冻结测试源码、oracle、ELF/BIN 与 manifest；不运行测试 |
| 构建 glibc 来源候选库 | [script/build_oldarchive.py](script/build_oldarchive.py) | `--prefix`、`--source <algo-migration目录>`、`--output`、`--functions`、`--variant`；候选实验，不说明当前公开 libmvec 精度 |
| 构建 ARM 四函数实验候选 | [script/migration/build_archive.py](script/migration/build_archive.py) | `--variant arm`、`--gcc`、`--output`；只产生显式替换归档，见[来源与限制](algo-migration/README.md) |
| NEMU 功能预检 / gsim 周期测量 | [script/run.py](script/run.py) | `nemu`/`gsim` 子命令、`--manifest`、`--output`；gsim 会启动仿真，须显式 `--emu`、`--reference`、`--dramsim3-ini` |
| 严格采集与审计 | [script/collect.py](script/collect.py) | `--run`、`--require-complete`；写 `reports/samples.csv`、`groups.csv`、`results.csv` 等；不启动仿真 |
| 已验收吞吐 CSV 比较 | [script/perf_compare.py](script/perf_compare.py) | 同标签/九模式/十轮 steady；v3 软件门槛、v2 历史口径；单次比较不替代独立复跑 |
| 短 trace 构建与运行 | [script/build_trace.py](script/build_trace.py)、[script/run_trace_nemu.py](script/run_trace_nemu.py)、[script/run_trace_verilator.py](script/run_trace_verilator.py) | 复用基准内核和数学库；Verilator 命令会启动仿真，须获授权 |
| 七函数历史 MPFR512 / array / Nx 门禁 | [script/accuracy/gate.py](script/accuracy/gate.py) | `prepare` 物化共享头；`run` 可选择 `--build-only`；实际 NEMU 需显式 `--nemu` |
| 五 frm 直接入口行为观察 | [script/accuracy/frm_observe.py](script/accuracy/frm_observe.py) | `run` 构建/可选运行；`compare` 比较两份完整 GOOD TRAP 日志；不是 MPFR 真值分析 |
| 完整 corpus 五 frm 观察与比较 | [script/accuracy/rounding_accuracy.py](script/accuracy/rounding_accuracy.py) | `run`/`analyze`/`compare`；支持 `--samples` 限额 RNE 与四函数 `--nx-only`；只在显式 `--nemu` 时运行 |
| 已采集输出的 MPFR 区间分析 | [script/accuracy/mpfr_accuracy.py](script/accuracy/mpfr_accuracy.py) | 主机离线分析；不执行目标；`--oracle-dir`、`--profile` 为必需输入身份 |
| 两组已完成分析的组级比较 | [script/accuracy/rounding_group_compare.py](script/accuracy/rounding_group_compare.py) | 只读 COMPLETE 与结果文件；描述差异，不判定任一方正确 |
| 单次调用延迟/配对框架 | [延迟与配对框架](latency/README.zh.md) | `script/latency/`；配对构建使用 `--protocol paired` 和显式冻结输入；旧 LAT 单独解析 |
| 审批/冻结/证据/报告状态 | [workflow/README.md](workflow/README.md) | 显式 `--task`；规范 CSV、审阅和永久证据；不启动仿真 |
| 正确性源码与主机测试 | [tests/README.md](tests/README.md) | `tests/abi/`、`tests/unit/`、`tests/accuracy/`、`tests/host/`；主机测试不替代 NEMU/RTL |

共享模块 `script/runner_common.py`、`script/compact_csv.py`、`script/run_emu_backend.py` 不是独立运行入口。
`script/build.py` 的 `--profile full/format-smoke/n16/grid` 和 `--case` 以 `--help` 为准；当前吞吐协议、CSV
v3 质量门槛和 v2 历史口径见 [throughput/README.md](throughput/README.md)。

## 测试与运行时源码

| 文件 | 职责 |
|---|---|
| [throughput/kernel-loop.c](throughput/kernel-loop.c)、[kernel-direct.c](throughput/kernel-direct.c)、[kernel-array.c](throughput/kernel-array.c) | 标量/自动向量化、Nx 和 array 适配；`throughput/perf.c` 计时与协议打印 |
| [runtime/entry_rne.c](runtime/entry_rne.c)、[runtime/tls_anchor.c](runtime/tls_anchor.c)、[runtime/tls-safe.ld](runtime/tls-safe.ld) | RNE 外层、裸机 TLS 与布局支持 |
| [tests/abi/nx_gate.c](tests/abi/nx_gate.c)、[tests/abi/nx_probe.S](tests/abi/nx_probe.S) | Nx 数值/环境与 incoming VL、寄存器探针 |
| [tests/unit/](tests/unit/) | 历史 array 数值/语义与独立 float3 用例，配合冻结 MPFR 头 |
| [tests/accuracy/rounding_accuracy.c](tests/accuracy/rounding_accuracy.c) | 完整冻结 corpus 的五模式逐 lane 采集 |

## 历史工具状态

`math/` 目录、`math/build.py`、`node_campaign.py`、`recover.py`、`analyze.py`、`campaign.py`、`package.py`、
`verify_images.py`、`abi-probe.py`、`generate_data.py` 和 `analysis_newmath/*.py` 属于旧布局，当前仓库没有
对应可执行入口；不要按旧 README 或历史绝对路径运行。`analysis_newmath/README.zh.md` 与
[SEVENMATH.zh.md](SEVENMATH.zh.md) 只保留历史方法和契约，不是当前命令索引。

`recover.py` 已移除：只有完整运行目录才有严格审计；截断日志的行级恢复只能作诊断，不能当作通过。旧构建
产物和部分输入已经清理，归档脚本不应直接重放。任何主机测试、build-only 或历史 checksum 都不是当前
交叉编译、NEMU/RTL 性能或数学精度的通过声明。
