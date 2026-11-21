# 七函数精简构建、运行与证据

此说明对应 2026-11-14 的独立实验：数学库构建仅准备 log，不启动 gsim/Verilator。七函数性能框架可选择函数和 N/K；九模式、十轮（first 1、warmup 2、steady 7）固定，CSV v2 按九模式轮转。此处不是全数学语义、向量 PCS、RTL 性能的验收声明。所有脚本均需 Python 3.10+；交叉编译需显式提供同一 GCC 安装前缀、运行时库、源树及冻结输入，主机审计和生成输入所需的独立依赖由调用者显式准备。

> 状态：**历史实验方法记录（2026-11-14）**，不是当前入口。本文中的 `math/`、`tests/newmath/` 和
> `$SRC/build.py` 等路径属于旧布局，命令不能直接执行；保留本文是为了记录当时的方法和契约。当前入口见
> [README.zh.md](README.zh.md) 与 [TOOLS.zh.md](TOOLS.zh.md)，精度工具的当前路径在 `script/accuracy/`。
>
> 路径迁移：旧 `$SRC/math/build.py` 与 `math/` 无当前等价（旧算法构建见
> `script/build_oldarchive.py --source algo-migration`，CLI/语义不同）；旧 `$SRC/build.py` 对应
> `script/build.py`；旧 `$SRC/run.py`、
> `collect.py`、`build_trace.py`、`run_trace_*.py` 对应 `script/` 下同名脚本；旧 `$SRC/test_*.py`
> 对应 `tests/host/`。以各脚本 `--help` 为准。

## 不可违反的目录约束

构建输出置于独立实验 `build/`；**不得在任何 build 路径执行测试**。每次模拟、staging、工作目录、模拟器临时文件和日志均放独立的、名称含 `YYYYMMDD-HHMMSS` 的运行目录；stdout/stderr 分开。运行器的 `--output` 必须是不存在的新日期目录，且不得位于 build 目录。工具路径通过参数显式给出，不自动寻找旧任务；禁止自动恢复已停止测试。正式 RTL 仿真是否启动由协调者决定。

## 三版本的数学库与 N16 镜像

本次明确测试工具前缀为 `/nfs/share/huxuan/riscv-gnu-toolchain-gcc16.1.0-20261109-142438-6ba7795932e0-libmvec`。操作示例从非 build 目录执行；每个新输出的父目录必须存在且目标本身不存在：

```sh
# 历史命令（旧布局，不能直接执行）：
E=/absolute/path/to/experiment
PREFIX=/absolute/path/to/gcc-install-prefix
SRC="$E/source/single/libmvec"
INPUT="$E/inputs/perf"  # 由使用者校验并准备的 log.h 等
V=baseline               # 分别选择 baseline、cold、lifetime
python3 -B "$SRC/math/build.py" --prefix "$PREFIX" --source "$SRC/math" \
  --output "$E/build/log-$V-math-nolto" --variant "$V"
python3 -B "$SRC/build.py" --prefix "$PREFIX" --data-dir "$INPUT" \
  --case log --profile n16 --direct-interface nx \
  --math-archive "$E/build/log-$V-math-nolto/libnewmath.a" \
  --output "$E/build/log-$V-n16-nx-nolto"
```

同样以 `--direct-interface array` 和另一个输出路径可构建 array direct。`auto_m1/auto_m2` 两者**始终使用 Nx**；direct Nx 经过 `_ZGV1/2Nxv_log`，direct array 使用 `logvec_m1/m2`。每个构建生成 manifest、完整命令、分离的命令输出、ELF/BIN、link map 和反汇编。三版仅数学核预处理开关不同：baseline 定义 `LOGVEC_BASELINE=1`，cold 无实验宏，lifetime 定义 `LOGVEC_HOT_LIFETIME=1`。默认非 LTO；可显式 `--lto`，但 auto TU 为避免 GCC 混合 LMUL 选项而强制非 LTO，其余 TU、数学库与最终链接为 LTO，必须称混合 LTO。

参数 `--case` 可重复选七函数中的任意函数。`--profile grid --lengths 128 --batches 1,4` 可构建未来 N128，但其相应数学 archive 必须显式提供，不由此 log-only 数学库生成；每个列表最多 3 个不重复正整数，N 不超过 1024。log/exp 若需 K=16 可写 `--batches 1,4,16`。构建脚本以用户的共享 header 绝对路径编译并记录 SHA、编译前后对照，不重新生成/覆盖 header。跨函数构建能力不等于已验证其他算法或运行它们。

## 功能预检及调用

NEMU 命令示例，`DRIVER` 自身也是独立日期运行目录，stdout/stderr 均分别保存：

```sh
NEMU=/absolute/path/to/riscv64-nemu-interpreter
DRIVER="$E/runs/$(date +%Y%m%d-%H%M%S)-manual-driver"
mkdir "$DRIVER"
RUN="$E/runs/$(date +%Y%m%d-%H%M%S)-log-baseline-nemu"
(cd "$DRIVER" && python3 -B "$SRC/script/run.py" nemu \
  --manifest "$E/build/log-baseline-n16-nx-nolto/manifest.json" \
  --nemu "$NEMU" --output "$RUN" --jobs 1 --max-instr 500000000 --timeout 180s \
  >nemu.stdout.log 2>nemu.stderr.log)
(cd "$DRIVER" && python3 -B "$SRC/script/collect.py" --run "$RUN" --require-complete \
  >collect.stdout.log 2>collect.stderr.log)
```

`run.py` 在日期目录内暂存并双次校验 BIN SHA，每个 case 的进程 cwd、TMP、TMPDIR、TEMP 隔离；采集必须同时检查实际 rc、trap、目标完成标记和数据完整性。NEMU 仅为功能预检，其 cycle 不得当作 RTL 性能。

正式 gsim 可参考以下**命令模板**，只在获得实际启动授权后执行；必须选择有效的非 static gsim、差分参考模型、DRAM 配置，设置不同的 `RUN`：

```sh
RUN="$E/runs/$(date +%Y%m%d-%H%M%S)-log-baseline-gsim"
(cd "$DRIVER" && python3 -B "$SRC/script/run.py" gsim \
  --manifest "$E/build/log-baseline-n16-nx-nolto/manifest.json" \
  --output "$RUN" --case perf-log --jobs 1 --timeout 48h \
  --emu /absolute/path/to/nonstatic-gsim --reference /absolute/path/to/refmodel.so \
  --dramsim3-ini /absolute/path/to/XiangShan.ini \
  >gsim.stdout.log 2>gsim.stderr.log)
(cd "$DRIVER" && python3 -B "$SRC/script/collect.py" --run "$RUN" --require-complete \
  >gsim-collect.stdout.log 2>gsim-collect.stderr.log)
```

不要把 `run.py` 的进程 rc=0 单独当成通过；必须等待完成并对每个 case 用 collector 严格验收。运行计划、进程管理及可能的长批提醒由协调者负责。

## 七函数逻辑批与历史陪跑

指定的历史六陪跑 manifest 是只读输入 `/nfs/home/huxuan/repos/riscv-gnu-toolchain/.dsh/rvv-bench/single/libmvec/build/sixwave-n16-baseline/manifest.json`。其 SHA256 为 `baabf24c2f44137353693a9bebfac568419ceb927a0b0ede4fd7c8ecbb2ac603`；2026-11-14 一次只读审计表明六个 `perf-exp`、`perf-expf`、`perf-logf`、`perf-pow`、`perf-sin`、`perf-cos` 的原 BIN SHA 与 manifest 一致。实际新启动前仍必须再次验证 manifest 和 BIN。可将该 manifest 显式传给上述 `run.py gsim`，重复 `--case` 六次、在新日期目录分离 staging/输出；不可在原 build 执行。将六个完整通过的陪跑结果引用到各 log 逻辑批时，明确它们是同一份测量及其原始 manifest/BIN 来源，而不是每版新跑；不同库与编译选项也不能冒充三版严格同条件。历史六陪跑为 N16，不能当作 N128 结果。

## 短 trace

```sh
python3 -B "$SRC/script/build_trace.py" --build "$E/build/log-baseline-n16-nx-nolto" \
  --data-dir "$INPUT" --output "$E/build/log-baseline-trace-nx-nolto" \
  --case log --mode auto_m2 --mode direct_m2 --k 1 --offset 0
TRACE_RUN="$E/runs/$(date +%Y%m%d-%H%M%S)-trace-baseline-nemu"
(cd "$DRIVER" && python3 -B "$SRC/script/run_trace_nemu.py" \
  --manifest "$E/build/log-baseline-trace-nx-nolto/trace-manifest.json" \
  --nemu "$NEMU" --output "$TRACE_RUN" \
  >trace.stdout.log 2>trace.stderr.log)
```

短镜像复用原基准对应 kernel 目标文件和数学库，校验共享输入、源 ELF/BIN 和相关 SHA；`trace_region` 前两次暖机、其中一次 K=1 调用，再核对 16 个结果。它是波形定位小用例，不是完整数值/PCS 验收。若修改 trace 源码，必须先重建冻结的基准快照。

经授权的短 Verilator 测量可使用如下模板；此命令会实际启动仿真，由协调者决定是否执行，调用之前先分别准备 baseline/cold/lifetime 三份 trace：

```sh
TRACE_BATCH="$E/runs/$(date +%Y%m%d-%H%M%S)-log-trace-verilator"
(cd "$DRIVER" && python3 -B "$SRC/script/run_trace_verilator.py" \
  --trace "baseline=$E/build/log-baseline-trace-nx-nolto/trace-manifest.json" \
  --trace "cold=$E/build/log-cold-trace-nx-nolto/trace-manifest.json" \
  --trace "lifetime=$E/build/log-lifetime-trace-nx-nolto/trace-manifest.json" \
  --emu /absolute/path/to/emu-verilator --reference /absolute/path/to/refmodel.so \
  --dramsim3-ini /absolute/path/to/XiangShan.ini --output "$TRACE_BATCH" \
  --seed 123456789 --jobs 3 --timeout 48h \
  >verilator.stdout.log 2>verilator.stderr.log)
```

实际执行 `-b 0 -e 30000 -C 100000`、差分、独立 FST 和提交 trace；每 case 及批次的 `result.csv`/`results.csv` 包含状态、退出码、超时、SHA、trace 记录、真实 Core0 GOOD TRAP、FST 大小及 SHA。只有 rc=0、唯一完整 `TRACE_RESULT` 且 failed=0、周期记录一致、Core0 GOOD TRAP、FST 非空、无错误及 SHA 无变化才 passed；缺行或截断不得判成功。已检查的旧 cos 波形日志只有 GOOD TRAP、没有 `TRACE_RESULT`，按新规则必须失败，不能冒充现有新 trace 的通过证据。主机测试为 `python3 -B "$SRC/tests/host/test_trace_verilator.py"`，应在日期运行目录运行并将 stdout/stderr 分开。

## 主机小测试

```sh
(cd "$DRIVER" && python3 -B "$SRC/tests/host/test_sevenmath_infra.py" >host.stdout.log 2>host.stderr.log)
```

测试覆盖七函数的模式映射、九模式十轮轮转、N/K 网格的边界和运行目录规则，不替代交叉编译及 NEMU/RTL。
