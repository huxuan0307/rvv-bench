# libmvec 香山裸机基准

> 状态：当前入口导航（2026-11-19）。本工作树已改为函数优先布局；旧实验中位于 `single/libmvec/` 根下的
> `build.py`、`run.py`、`recover.py`、`analyze.py` 等命令不属于当前布局，不要照抄。历史七函数方法见
> [SEVENMATH.zh.md](SEVENMATH.zh.md)，工具细节见 [TOOLS.zh.md](TOOLS.zh.md)。

测试配套 GCC/glibc 的**公开 RVV libmvec**，不是原始 veclibm 的重命名。复用本仓 AM、TLS、klib 和 errno
支持，直接链接发布安装的静态数学库，产出从 `0x80000000` 启动的裸机 ELF/BIN。**NEMU 只验证功能；性能来自
非 static gsim 的香山 RTL 中目标程序读取的 rdcycle。** 有限采样、checksum、ABI 探测或主机分析都不等于
数学全域正确性证明。

所有命令从本仓根目录执行，使用 Python 3.10+、宿主 MPFR 共享库和含 libmvec 的 RISC-V 工具链。构建/运行
输出目录应每次新建；构建产物、原始日志和 `.dsh` 任务数据不提交 Git。迁移与 Git 更新见
[运行环境说明](<../../docs/RUNTIME.zh.md>)。

## 当前入口导航

| 目的 | 当前入口 | 说明 |
|---|---|---|
| 吞吐协议、CSV v2/v3 与证据边界 | [throughput/README.md](throughput/README.md) | `script/` 下 build/run/collect/perf_compare；v3 质量门槛与 v2 历史口径分开 |
| 单次调用延迟/配对性能 | [延迟与配对框架](latency/README.zh.md) | `script/latency/` 下的构建、运行和解析入口；配对协议 2 与吞吐 CSV3 独立 |
| 正确性、ABI、数值与主机测试 | [tests/README.md](tests/README.md) | 源码在 `tests/abi/`、`tests/unit/`、`tests/accuracy/`；入口在 `script/accuracy/`，主机测试在 `tests/host/` |
| 旧七函数算法源码与候选构建 | [oldarchive/README.md](oldarchive/README.md) | 旧算法源码在 `oldarchive/`；构建见 `script/build_oldarchive.py` |
| 短 trace 与波形定位 | [SEVENMATH.zh.md](SEVENMATH.zh.md) | 历史方法记录；当前脚本为 `script/build_trace.py`、`script/run_trace_*.py` |
| 审批、版本失效、证据与报告门禁 | [workflow/README.md](workflow/README.md) | 显式 `--task`；不启动仿真 |
| 工具全集、输入输出与历史工具状态 | [TOOLS.zh.md](TOOLS.zh.md) | 本索引的逐项说明 |

## 最小当前流程（仅构建 + NEMU 功能预检）

以下示例只演示当前脚本路径和目录约束，不是完整性能验收；实际工具链、冻结输入和模拟器由调用者显式提供。
`OUT` 必须是尚不存在的日期时间目录，且不得位于任何 `build` 路径；stdout/stderr 分开保存：

```sh
OUT="$PWD/.dsh/runs/libmvec-smoke-$(date +%Y%m%d-%H%M%S)"
[ ! -e "$OUT" ] || exit 1
mkdir -p "$OUT/log"
PREFIX=/absolute/path/to/gcc-install-prefix
DATA=/absolute/path/to/frozen-perf-headers
NEMU=/absolute/path/to/riscv64-nemu-interpreter

python3 -B single/libmvec/script/build.py --prefix "$PREFIX" --data-dir "$DATA" \
  --case log --profile format-smoke --output "$OUT/build" \
  >"$OUT/log/build.stdout" 2>"$OUT/log/build.stderr"
python3 -B single/libmvec/script/run.py nemu \
  --manifest "$OUT/build/manifest.json" --nemu "$NEMU" \
  --output "$OUT/nemu" --case perf-log --jobs 1 --timeout 120s \
  >"$OUT/log/nemu.stdout" 2>"$OUT/log/nemu.stderr"
python3 -B single/libmvec/script/collect.py --run "$OUT/nemu" --require-complete
```

NEMU 通过后才考虑非 static gsim；gsim 必须显式给 `--emu`、`--reference` 和 `--dramsim3-ini`，并另建日期运行
目录。构建、运行、采集和报告使用的工具/库/输入身份以 manifest 与运行元数据为准，不以文件名代替身份。

## 目录概览

- `script/`：公共构建/运行/采集/对比入口；`script/accuracy/` 为数值与 ABI 分析入口。
- `throughput/`、`runtime/`：吞吐 C 源码、RNE/TLS 运行时；吞吐协议说明在 `throughput/README.md`。
- `tests/`：`abi/`、`unit/`、`accuracy/` 的目标侧源码，`host/` 的主机测试；总说明见 `tests/README.md`。
- `latency/`：配对和历史延迟的 C 驱动、探针与[协议说明](latency/README.zh.md)；工具在 `script/latency/`。
- `oldarchive/`：旧七函数算法源码；不是当前 libmvec 公开 ABI 的实现。
- `workflow/`：审批、冻结、证据和报告状态机。

## 历史命令与边界

- 旧 README 中 `python3 -B single/libmvec/build.py`、`run.py`、`recover.py`、`analyze.py`、`campaign.py`、
  `package.py`、`verify_images.py`、`node_campaign.py`、`abi-probe.py`、`generate_data.py` 等入口已不在当前
  布局；`recover.py` 也不存在，不得把行级回收当作通过。它们只作历史记录，不能直接执行或用于新结论。
- [SEVENMATH.zh.md](SEVENMATH.zh.md) 保存 2026-11-14 历史实验的方法和契约（旧 `math/`、`tests/newmath` 路径
  只作历史参考）；当前命令以本文件和 `TOOLS.zh.md` 为准。
- 主机测试、build-only、NEMU GOOD TRAP、ABI 数量或 checksum 一致都只是局部证据；不得据此声称 RTL 性能、
  数学全域误差或完整 ABI 已验收。
