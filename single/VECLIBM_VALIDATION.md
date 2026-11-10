# veclibm 构建、NEMU 验证及快速规模校准

## 最终结论

2026-11-07 在本机 GCC 16.1.0、RV64GCV、NEMU VLEN=128 配置下，完成两套测试的实际构建与执行：

| 套件 | 独立程序数 | 最终执行结果 | 整程序指令数 min / median / max |
|---|---:|---|---:|
| 性能 / smoke | 25 | 25/25 正常完成；150 次计算样本；25/25 落在 5M–15M | 9,434,834 / 9,676,270 / 9,980,713 |
| 上游精度 / 诊断 | 141 | 141/141 正常完成；131 项合计 467 次原始断言全部通过；10 项仅诊断 | 4,923 / 8,684,598 / 11,041,070 |

所有数字均来自 **NEMU 宿主报告的 `total guest instructions`**，不是估算、数组元素数或仅内核 body 的 `rdinstret`。25 个性能程序合计 242,337,188 条；141 个精度 / 诊断程序合计 746,413,217 条。目标约 10M 是**单独程序规模**，不是整套合计。

精度测试中 88 项为 1M–15M，53 项低于 1M（20 项特殊值测试 + 33 项跨步测试）；这些测试保留了原始小规模工作负载，不用重复或空循环填充。77 项超过 5M。最大项为 `lgamma.lgamma_right`。最终没有程序超过 15M。

## 最终可复现命令与证据

从仓库根目录执行过以下端到端入口，均成功退出：

```sh
make -C single run-veclibm-quick \
  VECLIBM_RUN_DIR=build/veclibm-runs/final-make-performance

make -C single run-veclibm-accuracy \
  RUN_OUTPUT=build/run-final-parent
```

复现时应改用新的输出目录，或者不指定目录、使用自动时间戳；运行器拒绝覆盖已有证据。

| 内容 | 索引 |
|---|---|
| 性能 25 项逐项指令数 / 日志 | [性能汇总](<build/veclibm-runs/final-make-performance/summary.md>) · [JSON](<build/veclibm-runs/final-make-performance/results.json>) · [CSV](<build/veclibm-runs/final-make-performance/results.csv>) |
| 精度 / 诊断 141 项逐项结果 | [精度汇总](<veclibm-accuracy/build/run-final-parent/summary.md>) · [JSON](<veclibm-accuracy/build/run-final-parent/results.json>) · [CSV](<veclibm-accuracy/build/run-final-parent/results.csv>) |
| 性能构建参数及 BIN 哈希 | [性能 manifest](<build/veclibm-quick/manifest.json>) |
| 精度构建参数、原源文件位置 / 哈希、BIN 哈希及验证类别 | [精度 manifest](<veclibm-accuracy/build/manifest.json>) |
| 性能端到端构建 / 运行日志 | [性能日志](<build/performance-final-make.log>) |
| 精度端到端构建 / 运行日志 | [精度日志](<build/accuracy-final-parent.log>) |
| 逐函数规模 | [性能 quick 配置](<veclibm-quick.json>) |
| 逐 TEST 规模、方法与最终统计 | [精度 quick 配置](<veclibm-accuracy/quick.json>) · [校准记录](<veclibm-accuracy/calibration.json>) |

NEMU 可执行文件 SHA256：`7791f5112892e446185021b858ae1021dc820cfec56d320ca558de934dd34350`。工具链为 `riscv64-unknown-linux-gnu-gcc (g6afcc4f6d) 16.1.0`。实际 flags / NEMU 命令 / BIN 哈希保存在上述 manifest 和结果中，不能脱离配置把本次指令数视为所有机器通用值。

## 10 项仅可运行 / 观察，不作精度通过声明

这不是适配器删掉了断言，而是上游本来没有 `EXPECT`：

- `cdfnorminv.special`、`cosh.special`、`erfc.special`、`erfcinv.special`、`erfinv.special`、`lgamma.special`、`sinh.special`、`tgamma.special`：调用 `show_special_fp64`，输出 26 个特殊输入及结果位模式，未定义预期值断言。例见 [cosh 特殊值输出](<veclibm-accuracy/build/run-final-parent/cases/0038-cosh.special/stderr.log>)。
- `sincosI.test`、`sincospiI.test`：上游 [two-output strided helper](<../thirdparty/veclibm/test/src/test_infra.cpp#L448-L533>) 计算误差后只打印，没有阈值断言。

这些项的 manifest 标为 `diagnostic-only`，其余 131 项标为 `upstream-assertions`。不为缺失断言擅自发明阈值；最终逐项审计核实 131 项 `checks>0`、合计 467，另 10 项 `checks=0`，所有项 `failed=0`。

## 关键构建修正

1. 性能基线从近似数学 stub 改为真实静态 libm。libc 中的真实 `frexp/ldexp/scalbn` helper 按需链接，通过 TLS errno 别名避免重复定义。未改共享 autovec 运行时原文件。
2. 精度参考保留 binary128 long double，使用工具链真实 libm / libc / libgcc。补足 `-nostdinc++` 下丢失的标准 C++ long-double `cos/sin/log2` 重载，不能隐式降为 double。
3. 原始 81 个功能源文件不改动；仅在生成的基础设施副本插入采样上限，并修复上游 [二维输入下标错位](<../thirdparty/veclibm/test/src/test_infra.cpp#L1024-L1030>)。断言表达式、比较类型、严格 `<` 阈值、特殊值及显式参数表保留。
4. 默认校准配置只减少区间内部采样，保留全部区间、target、stride 外层循环和端点索引。full 入口恢复上游请求量，详见 [使用说明](<README.md>)。
5. 普通诊断、特殊值十六进制输出保留；只抑制 klib 不支持的含 `%L` 的整次打印。数值比较不受打印精度影响。

## 失败路径与额外回归

- 早期 `cospi` 参考因缺 C++ overload 而真实触发严格 `<1` 失败：`checks=3 failed=2`、进程退出 1、NEMU `BAD TRAP`。见 [原始断言失败日志](<veclibm-accuracy/build/run-p16-g4/cases/0043-cospi.test/stderr.log>) 与 [运行记录](<veclibm-accuracy/build/run-p16-g4/cases/0043-cospi.test/result.json>)。修正标准重载后通过，**没有放宽阈值**。早期日志关联的是当时 BIN 哈希，不能用后来重建的同路径 BIN 冒充该历史产物。
- 真实 NEMU 负例验证 `GOOD TRAP` 但缺终态标记会被拒收：[缺标记记录](<build/runner-smoke-marker-20261107-v2/results.json>)。
- 真实 NEMU 预算负例设置 1,000 条，NEMU 执行粒度使实际停在 1,019 条，运行器拒收：[硬上限记录](<build/runner-smoke-limit-20261107-v2/results.json>)。
- 25 函数组合程序亦完成 NEMU 运行（`N=1024,repeats=3`），整程序 22,268,897 条，终态明确完成 25 函数。该项仅额外兼容 smoke，不是默认逐函数校准程序：[组合测试结果](<build/veclibm-runs/final-combined/results.json>)。
- full 模式的 `exp.special` 构建成功；验证了参数恢复和独立输出树，没有声称执行全部原始高密度精度测试。

主机侧回归命令：

```sh
python3 -B -W error::ResourceWarning -m unittest discover -s single -p 'test_*.py'
make -C single/veclibm-accuracy test
```

最终 **32 项构建 / 运行器回归 + 5 项精度适配器回归全部通过**。覆盖日志 ANSI / 逗号计数、双输出流、错误标签与普通误差诊断区分、零 / 非零失败计数、缺 marker、退出失败、预算中断、超时进程回收、manifest 遗留产物 / 哈希拒收，以及采样适配保留断言 / 特殊表、单次求值、目录 / 参数约定。

## 解释边界

- 性能程序的 checksum 是输出活性检查，不等于精度判定；精度结论来自上述原断言。
- 快速采样不等于原始 100K 级密度，更不等于穷尽输入域。保留 full 可配置入口，但本次主要实跑的是 quick。
- NEMU cycle / 指令计数适合验证可执行性与工作量，不能当作硬件吞吐结论。
- 运行证据和 BIN 保留在忽略的构建目录；源码改动不包含大体积生成物，长期保存需另行归档。
