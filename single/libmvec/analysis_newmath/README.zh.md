# newmath 离线样本校验与性能汇总

本目录迁自 newlib-full 历史任务中可复用的严格样本验收、统计、配对与绘图代码。只读 CSV/JSON，不构建目标程序、不启动仿真。仅依赖 Python 3.9+ 标准库，不依赖 tasks 的固定路径。

## 从合并原始 CSV 生成当前十列表

在 rvv-bench 仓库根运行，输入与输出可指定任意路径；输出文件必须尚不存在，父目录应已存在：

```sh
python3 single/libmvec/analysis_newmath/cycles_summary.py \
  --input /path/to/cycles-all-raw.csv \
  --output /path/to/cycles-summary.csv
```

输出精确十列：

```csv
function,n,k,mode,scalar_strict,scalar_fast,veclibm,opt_nofrm,libm_vec_nx,libm_vec_array
```

每行是同 function/N/K/mode 的 steady7 原始周期中位数，不除 N/K、不扣 control。scalar_strict、scalar_fast 各取 new/main-nx 同 function/N/K 的相应标量基线；不取旧库或 array 标量。family/profile 映射：

| 原始 family | 原始 profile | 输出列 |
| --- | --- | --- |
| old-original | main-nx | veclibm |
| old-nofrm | main-nx | opt_nofrm |
| new | main-nx | libm_vec_nx |
| new | aux-array | libm_vec_array |

mode 原样保留 auto_m1、auto_m2、direct_m1、direct_m2。array 的 auto 值也取其独立原始测量，不复制 Nx 数值；接口与代码生成差异不能直接相减为纯 adapter 成本。

输入为合并的 collector samples CSV，包含 family、profile、interface、run_role，以及 collector 原始样本字段：record_type、case、suite、function、mode、n、k、phase、round、order、r、cycle_begin、cycle_end、cycles、run_status、case_status、measurement_valid。保留的 source_csv/source_csv_sha256/source_row 等溯源列可存在，但本命令不读取其路径或核验来源文件 hash。

必须完整四组来源，每组 4320 条，共 17280 条；只接受 run_role=main。每组校验完整七函数，N16/128/1024，log/exp K1/4/16、其余 K1/4，六 math 加三 control，r0..9，first1/warmup2/steady7；phase 内 round、rotate9 order、状态、measurement_valid、uint64 周期及回绕一致性均校验。标量 interface=scalar，控制组=control，主 vector=Nx，aux auto=Nx、aux direct=array。缺组、缺样本、重复、未知 profile、replay、失败样本均拒绝，不默默过滤；验证失败不创建输出。结果为 192 条数据行，函数顺序 log/exp/sin/cos/pow/logf/expf。

## 保留历史 v1 严格证据分析

```sh
python3 single/libmvec/analysis_newmath/analyze.py /path/to/manifest.json \
  --input-root /path/to/evidence-root --output /path/to/new-report-directory
```

manifest 内路径相对 manifest 所在目录，所有绑定文件须位于显式 input-root 内，默认根为 manifest 所在目录。沿用历史 schema_version=1、campaign_id=20261112-0126-newlib-full、profile=main-nx，以及完整三 family × main/replay 六 run、最终 BIN/ELF 门禁、receipt、共享对象/工具身份、whole-program 指令层的严格语义。显式 `--user-authorized-main-only` 仍要求 manifest 的 acceptance_scope=main-only-user-authorized、replay_status=cancelled-by-user，及完整三个 main，不是接受缺样本。

保留 validate_samples、statistics、aggregate、render 等函数及原测试；统计包括 median/min/max/MAD/总体 CV、每次调用/每元素周期、同配置配对比与标量加速比、replay 比较。该入口仍是历史 v1 协议消费者，不将 campaign 的特定验收事实推广为任意新任务通过。

CSV 汇总只保证完整样本协议及统计正确，不能替代上述证据链验收、数学正确性、ABI 或实际重复运行稳定性验证。NEMU whole-program 指令不等于 timed-window 指令；模拟区间周期也不等于硅片性能。

## 测试与迁移范围

```sh
python3 -B -m unittest discover -s single/libmvec/analysis_newmath -p 'test_*.py'
```

测试使用明确合成数据和临时目录，不依赖历史任务、工具链或仿真。覆盖原严格验收、回绕、配对统计、新汇总 schema/四来源映射/new Nx 标量选择/auto-direct 保留，以及缺项、重复、无效状态、覆盖输出拒绝和 CLI。

历史 generate_analysis_manifest、report_main_only、plot_aux 依赖原任务布局、真实构建冻结、特定授权与一次性报告拼装，留待历史归档；未迁入新通用入口。未复制历史测量结果、日志和门禁产物，也未改写历史脚本。原脚本归档后仍保持原有语义；如需重跑，应在有完整原任务数据布局的环境中使用历史脚本，或显式向本目录入口传入证据根。
