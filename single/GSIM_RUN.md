# veclibm gsim 批量运行记录

## 本轮启动参数

- 运行目录：[20261107-all-166-j64-48h](<build/veclibm-gsim/20261107-all-166-j64-48h/run-metadata.json>)。
- 启动时间：2026-11-07 23:47（Asia/Shanghai）。
- 25 个校准性能程序 + 141 个精度 / 诊断程序，总计 166 项。
- 仿真器：`/nfs/share/huxuan/emus/emu-gsim-20261105-155351-409f429572`，启动时该目录最新 gsim。
- refmodel：`/nfs/share/huxuan/riscv64-nemu-interpreter-so-fixvfredusum`，差分开启。
- 脚本：`/nfs/home/huxuan/opt/bin/run_emu.sh`，实际解析到 `/nfs/home/huxuan/repos/XiangShanVector/run_emu.sh`。
- 脚本并行度 `-j 64`、逐测试 `--timeout 48h`。没有额外传入指令 / 周期限制，fork / wave 默认关闭。
- 166 个输入 BIN 均核对 manifest 哈希并复制到运行目录，不从原始构建目录动态读取；元信息保留全部输入哈希、两套 manifest 快照、emu / refmodel / 脚本身份。

精确运行命令记录在 [元信息](<build/veclibm-gsim/20261107-all-166-j64-48h/run-metadata.json>)，启动和逐项状态见 [脚本日志](<build/veclibm-gsim/20261107-all-166-j64-48h/runner.log>)、[脚本 stderr](<build/veclibm-gsim/20261107-all-166-j64-48h/runner.err>)。

## 最终结果（2026-11-08）

- 脚本于 03:45:35（Asia/Shanghai）结束，退出码 0；最终严格核验为 **166/166 passed**，无失败 / 超时 / 剩余进程，未发现差分错误。
- 性能 **25/25**；精度 / 诊断 **141/141**，其中 131 项带原断言，共 **467 次原断言全部通过**，10 项上游仅诊断测试正常完成，不声称这 10 项具有精度判据。
- [性能 CSV](<../.dsh/data/veclibm.csv>) 已导出并独立对照原始日志核验：**50 条汇总 + 150 条原始样本**，覆盖 25 函数 × 2 路径 × 3 次。
- [历史比较 CSV](<../.dsh/data/veclibm-comparison.csv>) 已导出并独立对照 HTML 核验：**125 行**，其中 **100 行有历史数据、25 行 X280 历史缺失**；BPI-F3 / X60 已去重。
- 主机侧 **90 项回归测试通过**，`git diff --check` 通过。

各平台 25 个函数的 **RVV 吞吐本轮 / 历史比值的等权几何平均**如下。大于 1 仅表示本轮配置报告的吞吐更高，不代表在一致输入 / 库 / 构建下的硬件加速。

| 历史平台 | RVV 吞吐比几何平均 | 本轮吞吐更高的函数 |
|---|---:|---:|
| AX45MPV / AndeSim | 0.7985× | 4/25 |
| SpacemiT X60 | 2.7084× | 25/25 |
| Ascalon-S | 1.2034× | 21/25 |
| Ascalon-X | 0.6878× | 0/25 |
| SiFive X280 | 无历史数值 | 不计算 |

本轮 RVV 相对本轮静态标量 libm：19 个函数更快、6 个更慢；25 函数速度比中位数为 3.0652×，等权几何平均为 2.4571×。这些是本轮 gsim 模型 / 指定输入下的函数调用区间周期结果，不是实机性能承诺。

CSV SHA256：

```text
veclibm.csv             0ab7976e78dde7bdfc386ccefe533cb630bd0e1a53f729375cee4a152a5c0fd9
veclibm-comparison.csv  c9d831737e9ad5bfc8f61778149e87e8c49ced186f6cdddae5df24bdcc1a68b3
```

## 只查看进度，不重启

```sh
python3 -B single/collect_veclibm_gsim.py \
  --run single/build/veclibm-gsim/20261107-all-166-j64-48h
```

该命令只更新以下报告：

- [Markdown 进度 / 结果](<build/veclibm-gsim/20261107-all-166-j64-48h/reports/summary.md>)
- [JSON 完整指标与证据](<build/veclibm-gsim/20261107-all-166-j64-48h/reports/results.json>)
- [CSV](<build/veclibm-gsim/20261107-all-166-j64-48h/reports/results.csv>)

运行过程中 `state=in_progress`，收集器退出 0 只表示成功生成进度报告，不表示测试全部通过。完成后必须覆盖全部 166 项且全部满足检查才为 `state=passed`。不要因为脚本退出 0 或 refmodel 的 `GOOD TRAP` 就当作最终成功。

成功判定包括：脚本唯一 `.pass_list` 记录、**RTL Core 0 的 GOOD TRAP**、匹配当前测试的完整终态标记、无差分 / 断言 / 超时错误、暂存输入哈希一致。性能程序额外校验每路径各 3 个样本；精度程序校验 131 项有原断言、10 项为上游仅诊断测试，且完成失败计数为零。

## 性能统计解释

收集器保留每个性能样本原始 cycles / insns，并按 `libm` / `rvvlm` 分别报告中位数 cycles/element、elements/cycle 以及标量 / RVV 周期比值。来源是 **gsim 目标程序读取的 rdcycle**，不是宿主机墙钟耗时。仍运行测试的部分样本不能作为最终性能结论。

emu 的 `Core-0 instrCnt / cycleCnt / IPC` 为整个程序的统计；精度 `body_instructions` 还包括参考 libm、误差计算及诊断，不能当作 RVV 函数吞吐。

## 每小时检查与性能 CSV

已按用户要求设置每小时检查本轮运行。使用 [导出入口](<export_veclibm_gsim.py>) 可一次刷新进度，并在全部 25 个性能测试终态后生成 `.dsh/data/veclibm.csv`，不必等待精度套件：

```sh
python3 -B single/export_veclibm_gsim.py \
  --run single/build/veclibm-gsim/20261107-all-166-j64-48h \
  --output .dsh/data/veclibm.csv
```

若性能测试仍运行 / 排队，不创建 CSV。若全部通过，CSV 含 50 条 `(函数, mode)` 汇总行与 150 条逐次 sample 行，通过 `record_type` 区分；包含原始 cycles / insns / checksum、n、各路径中位数 cycles/element、elements/cycle、标量 / RVV 周期比、VLEN、整程序 Core 0 指标、BIN / emu / refmodel 身份及日志路径。若有失败或超时，保留对应汇总行、已有样本和原因，`measurement_valid=False`，缺失指标留空。

用户追加历史结果对比后，定时入口同时接入全部 `*/veclibm.html` 的吞吐比较：

```sh
python3 -B single/export_veclibm_gsim.py \
  --run single/build/veclibm-gsim/20261107-all-166-j64-48h \
  --output .dsh/data/veclibm.csv \
  --baseline-dir /nfs/home/huxuan/bench_result/rvv-bench-results \
  --comparison-output .dsh/data/veclibm-comparison.csv
```

比较 CSV 每行对应一个历史平台和函数，统一用 `FP64 elements/cycle`，保留双方吞吐、cycles/element、标量 / RVV 速度比、本轮 / 历史吞吐比与百分比变化。BPI-F3 / X60 链接去重，X280 空表保留缺失状态，不补零。HTML 数值保留发布精度，发布的 speedup 与用六位小数吞吐重新计算的比值分别记录。

历史页面模板声明输入 `[0,1)`；本轮使用 `[0.125,0.875)`，`acosh` 还整体加 1，标量路径为本轮工具链的静态 libm。历史页面未给出同样的采样规模、重复策略及完整构建配置，因此 CSV 将这些差异列出，比较属于各配置实测结果对照。

全部 166 项终态且最终报告与两份 CSV 核验完成后停止该定时检查。

## 新建其它运行

```sh
python3 -B single/run_veclibm_gsim.py --jobs 64 --timeout 48h
```

新运行自动创建唯一目录。已有本轮仍在运行时不要再次启动相同 166 项；这里只保留复现入口。
