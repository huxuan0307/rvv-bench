# veclibm：RVV 标准扩展裸机构建与 NEMU 快速测试

这里提供两套互补的测试，均以 `rv64gcv/lp64d` 构建，在 AM 裸机环境运行，不依赖 Linux 系统调用，也不要求非标准 RVV 扩展。

| 套件 | 覆盖 | 判定内容 | 默认规模 |
|---|---|---|---|
| 性能 / smoke | 25 个函数，每函数一个程序 | 真实静态 libm 和 veclibm 均完成计算、输出 checksum、正常退出 | 每程序约 10M 条动态指令 |
| 上游精度测试 | 原始 81 个功能测试源文件、141 个 `TEST`，含连续 / 跨步、特殊值、多输入 / 多输出 | 原始 `EXPECT_LT` ULP 阈值及 `EXPECT_EQ` 位模式断言 | 按 `TEST` 限制区间采样数，较大测试约 10M；原本很短的测试不填充 |

**规模目标按独立程序计算，不是两套测试合计 10M。** 计数使用 NEMU 末尾的 `total guest instructions`，包括启动、输入初始化、参考计算、断言和输出。程序内 `rdinstret` 的局部计数仅辅助诊断，不用来替代整程序预算。

## 脚本索引

另一个独立套件是[已发布libmvec基准](<libmvec/README.zh.md>)；它与本页的上游veclibm不共用验收结论。迁移与Git更新见[运行环境](<../docs/RUNTIME.zh.md>)。

|脚本|用途|
|---|---|
|[build_veclibm.py](<build_veclibm.py>)|构建25函数性能BIN，支持config/case/output|
|[run_veclibm.py](<run_veclibm.py>)|按manifest运行NEMU、保存日志/预算/功能判定|
|[精度build.py](<veclibm-accuracy/build.py>)|列出/构建141个上游TEST的裸机适配|
|[run_veclibm_gsim.py](<run_veclibm_gsim.py>)|stage镜像并调用外部run_emu.sh批量差分|
|[collect_veclibm_gsim.py](<collect_veclibm_gsim.py>)|只收集既有运行，不启动仿真|
|[export_veclibm_gsim.py](<export_veclibm_gsim.py>)|导出严格验收结果及性能CSV|
|[compare_veclibm_gsim.py](<compare_veclibm_gsim.py>)|比较已导出性能与指定基线|

每个Python入口支持`--help`。构建/日志目录不提交Git；历史验证文档中的证据仅在本地保留，不表示一次新clone已经执行过测试。迁移后用新路径重新构建，不重放包含旧绝对路径的manifest。

## 一键构建与运行

从仓库根目录执行：

```sh
# 25 个性能测试：构建校准配置，然后在 NEMU 上逐个验证
make -C single run-veclibm-quick

# 141 个精度测试：构建默认 quick 配置，然后在 NEMU 上逐个验证
make -C single run-veclibm-accuracy
```

这两个目标是真正执行 NEMU，而不是仅生成 BIN。默认并发运行 4 个程序，每个进程超时 120 秒、硬停止上限 50M 条指令。输出使用新的带时间戳目录，已有运行目录会被拒绝覆盖。性能目标还要求每程序落入 5M–15M 的校准范围；精度套件保留原本短测试，因此功能成功与预算区间分别报告。

只构建、不运行：

```sh
make -C single veclibm-quick
make -C single veclibm-accuracy
```

单个测试及参数调整：

```sh
# 性能：统一 N 与重复次数；与校准配置是两种独立入口
make -C single veclibm-all VECLIBM_N=1024 VECLIBM_REPEATS=1
make -C single run-veclibm VECLIBM_N=1024 VECLIBM_REPEATS=1

# 性能：仅构建一个校准函数，隔离输出目录
python3 single/build_veclibm.py --config single/veclibm-quick.json \
  --case exp --output single/build/selected-exp

# 精度：限制区间 helper 每段最多 32 点、二维每轴最多 4 点
# 显式设置 POINTS/GRID_POINTS 时不加载逐 TEST 的 quick 配置
make -C single/veclibm-accuracy all CASE=exp.small_args POINTS=32 GRID_POINTS=4 BUILD=build-selected
python3 single/run_veclibm.py \
  --manifest single/veclibm-accuracy/build-selected/manifest.json \
  --output single/veclibm-accuracy/build-selected/run-new
```

`CASE` 使用上游真实 `suite.test` 名称；可用 `make -C single/veclibm-accuracy list` 列出全部 141 项。保留上游名称中的拼写，例如 `asinpI.test`，不擅自更名。

## gsim / RTL 差分批量运行

已有完整 quick 构建后，可通过 [gsim 启动器](<run_veclibm_gsim.py>) 调用外部 `run_emu.sh`：

```sh
python3 -B single/run_veclibm_gsim.py --jobs 64 --timeout 48h
```

默认从 `/nfs/share/huxuan/emus/emu-gsim-*` 按修改时间选最新版本，使用 `/nfs/home/huxuan/opt/bin/run_emu.sh`，refmodel 为 `/nfs/share/huxuan/riscv64-nemu-interpreter-so-fixvfredusum`。一次运行全部 25 个性能 + 141 个精度 / 诊断程序。`48h` 是外部脚本的**每测试超时**，没有额外施加更短的指令 / 周期上限。

启动前检查 manifest 的构建状态与 BIN 哈希，把 166 个 BIN 和两份 manifest 复制到独立运行目录，避免原构建树后续变化影响本轮。目录默认位于 `single/build/veclibm-gsim/`；可以通过 `--output` 指定不存在的新目录。运行元信息记录所选 emu、refmodel、脚本的实际路径与 SHA256、并发参数和精确命令。不要重复启动同一轮测试；已有运行可只收集进度：

```sh
python3 -B single/collect_veclibm_gsim.py --run /path/to/existing/run
```

收集器只更新运行目录下的报告，不重启仿真。区分仍运行、超时、差分 / 断言失败与成功完成；最终需逐项 `GOOD TRAP`、完成标记和样本 / 断言检查，不以脚本退出 0 单独判定成功。

## 工具链与 NEMU

本机验证配置：

- GCC 16.1.0，默认前缀 `/nfs/share/riscv-toolchain-gcc16.1.0-260715-nightly/bin/riscv64-unknown-linux-gnu-`。
- NEMU 默认 `/nfs/home/huxuan/opt/bin/riscv64-nemu-interpreter`，实际命令为 `NEMU -b --max-instr=50000000 IMAGE.bin`。
- 本机 NEMU 实测 VLEN = 128 位。更换工具链、VLEN、NEMU 配置后应重新测量并校准，不保证原样仍是 10M。

覆盖路径示例：

```sh
make -C single run-veclibm-quick \
  CC=/path/to/riscv64-unknown-linux-gnu-gcc NEMU=/path/to/nemu
make -C single/veclibm-accuracy run \
  GCC=/path/to/riscv64-unknown-linux-gnu-gcc NEMU=/path/to/nemu
```

不要使用仓库根目录的 QEMU 用户态运行脚本启动这些 AM 裸机程序。

## 快速规模与完整规模

性能逐函数参数在 [veclibm-quick.json](<veclibm-quick.json>) 中；保持 3 次 libm、3 次 RVV 调用，按每个函数的实际成本设置 `n`。首次以 `N=1024` 实测整程序指令数，然后按比例估算 `n`、向 64 的倍数取整，重建后再次实跑验证。不是所有函数盲目使用同一个 `N`。

精度逐 `TEST` 参数在 [quick.json](<veclibm-accuracy/quick.json>) 中，`points` 为每个连续区间的采样上限，`grid_points` 为二维每轴采样上限；不会超过原始测试的请求量。默认 `repeats=1`，不重复特殊值或插入空循环凑指令数。特殊值表、显式参数表、所有区间 / target / stride 外层循环保留，缩减的是每段区间的内部采样密度。较短的跨步测试通常原本仅 30 / 300 个样本，不强行放大。

恢复完整原始样本规模：

```sh
# 性能：历史 N=131072，每路径 3 次；生成 25 个独立程序
make -C single veclibm-all VECLIBM_N=131072 VECLIBM_REPEATS=3

# 精度：不使用 quick.json，cap=100000（不小于上游请求量）
# 输出到 build-full，避免覆盖默认 quick 产物
make -C single/veclibm-accuracy full
```

完整模式未承诺 10M 指令，也不代表本次对全规模完成了精度验证。如需运行完整模式，显式选择该 manifest，并增大 `--max-instr` 和 `--timeout`：

```sh
python3 single/run_veclibm.py \
  --manifest single/veclibm-accuracy/build-full/manifest.json \
  --max-instr 20000000000 --timeout 600 \
  --output single/veclibm-accuracy/build-full/run-new
```

## 裸机适配边界

- 性能程序 [veclibm.c](<veclibm.c>) 现在链接工具链真实静态 libm，**不再链接近似数学 stub**。标量循环关闭自动向量化；输入确定性、有限且处于各函数实数定义域，`acosh` 使用大于 1 的输入。checksum 观察全部输出，但性能套件本身**不作 ULP 精度判定**。
- 两套程序复用 AM、TLS 和 klib，补充 `__libc_errno` 到运行时 TLS `errno` 的别名，使 libc 中的真实 `frexp/ldexp/scalbn` 数学辅助对象可链接。未替换为近似参考函数。
- 精度套件使用 [受限 GTest 兼容层](<veclibm-accuracy/include/gtest/gtest.h>)，仅实现该上游实际使用的 `TEST`、`EXPECT_LT`、`EXPECT_EQ`，**不是完整 GoogleTest**。比较按原表达式类型执行；仅诊断显示转为 double。断言失败会使程序非零退出。
- `long double` 由构建期断言限定为 binary128 / 113 位有效精度。使用 [数学重载兼容头](<veclibm-accuracy/include/math.h>) 补足 `-nostdinc++` 下缺失的 `cos(long double)`、`sin(long double)`、`log2(long double)` 标准重载，转发到真实 `cosl/sinl/log2l`，避免静默降精度。
- klib 不支持 `%L` 格式，所以仅跳过含该格式的整次诊断 `printf`；普通诊断、特殊值位模式输出仍保留。适配运行时额外输出每条 ULP 断言及失败位模式；未屏蔽数值比较。
- 上游 [test_infra.cpp:1028–1029](<../thirdparty/veclibm/test/src/test_infra.cpp#L1028-L1029>) 的二维 unit-stride 填充存在 `x[cnt++]` 后写 `y[cnt]` 的错位 / 越界。仅在生成副本中修复为 `x[cnt]`、`y[cnt]`、再 `++cnt`。上游源码不修改，阈值不修改，manifest 记录该适配。
- 上游部分测试只有诊断计算，没有 `EXPECT`；这些测试只能判定可运行，不能称为精度通过。不得为缺失断言擅自发明阈值。
- 本次结果说明所选快速采样点上的断言通过，不等于穷尽输入域，也不把 NEMU 的 cycle 计数当作硬件性能结论。

## 运行证据与失败判定

[run_veclibm.py](<run_veclibm.py>) 保存每项 stdout / stderr、命令、BIN SHA256、退出码、耗时、整程序指令数，并输出 JSON、CSV、Markdown 汇总。manifest 中构建失败的产物不运行，提供哈希时校验实际 BIN，避免误用遗留文件。

功能成功必须同时满足：进程成功退出、NEMU `GOOD TRAP`、所需终态标记存在、NEMU 整程序计数可解析、无错误 / 断言失败 / 超时 / 预算中断。单独出现 `GOOD TRAP` 不足以通过；`failed=0` 是合法成功计数，非零失败计数会拒收。

具体运行结果、实测数据和证据索引保存在任务目录，不在源码说明中维护。长期归档应同时保存 manifest、BIN 和结果目录；历史记录不代表重新执行或完成当前配置验证。

主机侧适配器 / 解析器回归测试：

```sh
python3 -B -W error::ResourceWarning -m unittest discover -s single -p 'test_*.py'
make -C single/veclibm-accuracy test
```
