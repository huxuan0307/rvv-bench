# 历史延迟与扩展方法

[历史驱动](latency_main.c) 持有 `main()`，用 [latency_driver.c](latency_driver.c) 的探针表运行 affine-map 链、独立调用 `_tput` 或控制链。`x'=map(f(x))` 会偏离冻结输入，得到的是收敛工作点的延迟；不能和[精确输入配对框架](README.zh.md)的结果直接合并。

## 复用旧 LAT

使用独立输出目录，下面只构建校准镜像：

```sh
S=single/libmvec/script/latency
python3 -B "$S/build_latency.py" --prefix "$PREFIX" --output "$LAT_OUT" \
  --groups calib --tag calib --k-list 0,4,8,16,32 --rounds 10 --prelude 16 --serial 1
```

老的 atan/acos 冻结头组必须显式给 `--data-dir`；exp/log/sin/cos/pow 组在窗口外与安装标量 libm 比较。支持的组及 `--entries` 范围以[构建器](../script/latency/build_latency.py)的 `--help` 和配置表为准。旧 LAT 只用安装向量库；替换库使用配对或吞吐构建器。

旧镜像使用[历史运行器](../script/latency/run_latency.py)启动，并需明确模拟器、DiffTest、DRAM 配置和独立的带时间戳运行目录。[历史解析器](../script/latency/latency_parse.py)用 `--run "$LAT_RUN" --output-dir "$LAT_PARSE" --phase steady` 给出窗口和拟合；[校准工具](../script/latency/latency_calibrate.py)用 `--parse-dir "$LAT_PARSE" --run-report "$LAT_RUN/run-report.txt"` 检查结果。校准关注 nop_addi 与 64 条依赖 addi 的斜率、空调用、map-only、残差和抖动。回归只用 `K>0`：窗口固定开销在截距，带 map 的斜率不能直接称为纯内核延迟。旧 LAT 不是配对计时的兼容格式。

## 接入实现与探针

候选只提供算法和 ABI 适配，候选库中不得出现 `main()`、计时、校验、输出协议或仿真依赖。保留 m1/m2、固定/变长、masked/unmasked 对应入口，并用 `riscv_vector_cc` 声明；atan2 始终按 `y,x`。一个实现可由吞吐、延迟和精度框架分别链接，不在函数内复制驱动。

需要扩展配对函数时，新增 `paired_<fn>.c` 的参数和种子注册并复用[探针模板](paired_probe_impl.inc)，同时同步[构建器](../script/latency/build_latency.py)的有序 case 表和解析验收预期。不要在 `rdcycle` 区间内加打印、oracle 或额外的间接调用。冻结数据、检查覆盖与映射不可缺失；静态检查链、回边及目标 ABI，不替代有授权的目标运行。完整 ABI、数学精度和生产编译器消费者验证独立执行。
