# libmvec 香山裸机基准

> 2026-11-14 适用入口与安全目录规则见 [七函数精简构建运行说明](SEVENMATH.zh.md)。本文下方部分历史示例依赖已删除脚本或在 build 下运行，不能照抄；以新说明为准。

测试配套 GCC/glibc 的**公开 RVV libmvec**，不是原始 veclibm 的重命名。复用本仓 AM、TLS、klib 和 errno 支持，直接链接发布安装的静态数学库，产出从 `0x80000000` 启动的裸机 ELF/BIN。**NEMU 只验证功能；性能来自非 static gsim 的香山 RTL 中目标程序读取的 rdcycle。**

所有示例从本仓根目录运行，使用 Python 3.10+、宿主 MPFR 共享库和支持libmvec的RISC-V工具链。构建/运行输出目录应每次新建；构建产物、原始日志和`.dsh`任务数据不提交Git。迁移与Git更新说明见[运行环境说明](<../../docs/RUNTIME.zh.md>)。

## 脚本索引

新增七函数算法、Nx/array正确性测试、周期汇总及历史脚本状态的统一入口见[工具使用索引](TOOLS.zh.md)。该索引列明输入、输出、运行入口与尚未完成参数化的历史工具。

|脚本|用途|主要参数|
|---|---|---|
|[build.py](<build.py>)|冻结源码、生成oracle并构建BIN/ELF|`--prefix`、`--output`、可重复`--case`、`--profile full/format-smoke`|
|[run.py](<run.py>)|运行NEMU或非static gsim|`nemu/gsim`、`--manifest`、`--output`、可重复`--case`、`--jobs`、`--timeout`|
|[collect.py](<collect.py>)|严格验收并回收已打印完整行|`--run`、`--require-complete`|
|[recover.py](<recover.py>)|只有截断日志时独立解析完整行|`--manifest`、`--case`、可重复`--log`、`--output`|
|[analyze.py](<analyze.py>)|只分析完整通过的RTL数据|可重复`--report`、`--output`、`--require-full`|
|[campaign.py](<campaign.py>)|通过门禁后分阶段运行全参数|`--manifest`、`--nemu-run`、`--abi-run`、`--output`|
|[verify_images.py](<verify_images.py>)|核对源码/镜像SHA及关键反汇编|`--manifest`、`--output`|
|[package.py](<package.py>)|打包BIN/ELF、快照和已有门禁证据|`--build`、`--output`（新ZIP）|
|[generate_data.py](<generate_data.py>)|独立生成MPFR语料，通常由build调用|输出目录位置参数|
|[abi-probe.py](<abi-probe.py>)|生成/检查36公开ABI测试语料|`--check`、`--json`、`--out`|

参数详情均可用`python3 -B 脚本 --help`查询。`compact_csv.py`与`runner_common.py`是共享模块，不是运行入口。

## node033 与 run_emu.sh 后端

`run.py gsim --backend run-emu --runner /nfs/home/huxuan/opt/bin/run_emu.sh --jobs 32`通过外部脚本调度。每个BIN仍在独立工作目录运行，DRAM输出相互隔离；元数据同时保存外部命令与真实emu命令。直接执行后端仍限制4并发，外部后端允许1–32。

```sh
# 从node033执行，MANIFEST、NEMU_RUN和OUTPUT使用新副本的绝对路径。
python3 -B single/libmvec/node_campaign.py \
  --manifest "$MANIFEST" --nemu-run "$NEMU_RUN" --output "$OUTPUT" \
  --jobs 32 --timeout 48h \
  --emu /nfs/share/huxuan/emus/emu-gsim-20261105-155351-409f429572 \
  --reference /nfs/share/huxuan/refmodel/riscv64-nemu-interpreter-so-fixvfredusum
```

[node_campaign.py](<node_campaign.py>)先核对完整NEMU门禁，再运行ABI RTL、七个性能BIN、LOG/EXP独立复跑，逐阶段严格验收后才继续。7个性能BIN各含全部n/K/模式，因此32是并发上限，实际主批次最多7进程。任一失败停止后续阶段，不自动重试。

`run_emu.sh`在只有超时的批次可能返回0；收集器额外检查逐例清单和真实进程记录，不能仅凭脚本退出码判通过。外部脚本日志和目标stdout/stderr分别保留；部分完整CSV行仍可诊断回收。长期运行应记录远端PID、独立后台存活，每30分钟采集进展，结束后再汇总，不重复启动同一轮。

## 工具与默认值

- 已验证工具链前缀：`/nfs/share/huxuan/riscv-gnu-toolchain-gcc16.1.0-20261109-142438-6ba7795932e0-libmvec`；可用`build.py --prefix`替换。稳定版前缀不一定含libmvec。
- gsim只使用非static版本，默认从`/nfs/share/huxuan/emus`选最新有效版本；可用`--emu`固定。裸机BIN的静态库链接与模拟器非static要求是不同概念。
- refmodel默认从`/nfs/share/huxuan/refmodel`选择有效DiffTest库；可用`--reference`固定。
- NEMU默认`/nfs/home/huxuan/opt/bin/riscv64-nemu-interpreter`，可用`--nemu`替换。
- GCC、头文件和libm/libmvec/libc/libgcc必须来自同一发布前缀。每次记录实际工具、配置、库、源码、镜像SHA及命令，不以文件名代替身份验证。

## 先运行小参数用例

```sh
# 输出根目录必须不存在；日志stdout/stderr分开保存。
OUT="$PWD/.dsh/runs/libmvec-smoke-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT/log"
python3 -B single/libmvec/build.py --profile format-smoke --case log \
  --output "$OUT/build" >"$OUT/log/build.stdout" 2>"$OUT/log/build.stderr"
python3 -B single/libmvec/verify_images.py --manifest "$OUT/build/manifest.json" \
  --output "$OUT/image-audit.json"
python3 -B single/libmvec/run.py nemu --manifest "$OUT/build/manifest.json" \
  --output "$OUT/nemu" --jobs 1 --timeout 120s
python3 -B single/libmvec/collect.py --run "$OUT/nemu" --require-complete

# NEMU完整通过后再启动RTL。以下为已验证的非static模拟器和refmodel。
python3 -B single/libmvec/run.py gsim --manifest "$OUT/build/manifest.json" \
  --output "$OUT/gsim" --jobs 1 --timeout 48h \
  --emu /nfs/share/huxuan/emus/emu-gsim-20261105-155351-409f429572 \
  --reference /nfs/share/huxuan/refmodel/riscv64-nemu-interpreter-so-fixvfredusum
python3 -B single/libmvec/collect.py --run "$OUT/gsim" --require-complete
python3 -B single/libmvec/analyze.py --report "$OUT/gsim/reports/results.json" \
  --output "$OUT/analysis"
```

小用例仅log、n=16、K=1；仍保存1次first、2次warmup、7次steady及全部控制，不能计作全参数验收。示例逐条执行，上一条失败应检查日志，不盲目继续；运行器会把每case的stdout/stderr独立保存。

## 部分输出也能解析

目标串口采用[CSV v2协议](<PROTOCOL.md>)：每个n/K一个B块，每模式一行D，横向保存十轮begin/cycles。公共配置只打印一次，结束计数按uint64可逆还原；hash在目标端确认十轮一致后仅打印一次。

**行可解析与运行通过是两套独立判定。** 有完整有效公共头与B上下文后，每条完整D行可立即还原10样本，不等待其余D、T或E/pass。LF/CRLF确认该行结束；坏行和末尾半行不猜补。缺pass、RTL错误、超时或中止不丢弃其他已解析行，但整次运行不通过。

有运行目录时继续使用collector：

```sh
RUN=/absolute/path/to/run
python3 -B single/libmvec/collect.py --run "$RUN" --require-complete
```

即使命令退出非0，也会写出独立诊断结果：

|输出（相对运行目录）|含义|
|---|---|
|`reports/results.json`、`reports/results.csv`|严格运行/用例状态、失败原因、工具身份|
|`reports/samples.csv`|严格协议展开数据，附run_status/case_status/measurement_valid|
|`reports/recovered-samples.csv`|逐完整D行恢复的诊断数据，不依赖整次成功|
|`reports/recovery.json`|回收数据、源文件/行号、被拒行与上下文问题|
|`reports/perf-<function>.csv`|完整协议通过解码时保留的紧凑原CSV|
|`reports/summary.md`|运行状态摘要|

回收行固定带`row_status=parsed-unverified`、`measurement_valid=false`；重复观察保留并提示，不悄悄覆盖。它们不进入正式性能汇总。运行中没有result文件也能回收。stdout/stderr各自解析，不拼接残行或借用对方上下文。

只有日志、没有完整运行元数据时：

```sh
python3 -B single/libmvec/recover.py --manifest "$OUT/build/manifest.json" \
  --case perf-log --log /path/to/stdout.log --log /path/to/stderr.log \
  --output /path/to/new-recovery-directory
```

输出新目录中的`recovered-samples.csv`和`recovery.json`。**离线工具退出0仅表示解析/导出完成（可能0条），不表示测试通过。** 所有uint64保留精确十进制；Excel/pandas导入checksum应选字符串或uint64，不经float64。

当前目标先测量再集中打印，故只能恢复实际打印出的完整行，无法还原尚未打印的目标内存数据。恢复逻辑不增加目标print字符或改变计时窗口。

## 全参数流程

```sh
FULL="$PWD/.dsh/runs/libmvec-full-$(date +%Y%m%d-%H%M%S)"
python3 -B single/libmvec/build.py --output "$FULL/build"
python3 -B single/libmvec/run.py nemu --manifest "$FULL/build/manifest.json" \
  --output "$FULL/nemu" --max-instr 500000000 --jobs 4
python3 -B single/libmvec/collect.py --run "$FULL/nemu" --require-complete
python3 -B single/libmvec/run.py gsim --manifest "$FULL/build/manifest.json" \
  --case abi-public --output "$FULL/gsim-abi" --jobs 1 --timeout 48h \
  --emu /nfs/share/huxuan/emus/emu-gsim-20261105-155351-409f429572
python3 -B single/libmvec/collect.py --run "$FULL/gsim-abi" --require-complete

# NEMU全8项＋ABI RTL门禁完整通过、且确认输出格式后才启动。
python3 -B single/libmvec/campaign.py --manifest "$FULL/build/manifest.json" \
  --nemu-run "$FULL/nemu" --abi-run "$FULL/gsim-abi" --output "$FULL/campaign"
```

campaign先串行LOG/EXP，再其余五函数最多4并发，最后独立启动LOG/EXP复跑；逐阶段严格审计，不自动重试、不关闭差分、不回退static。冻结门禁所用工具/DRAM身份。完整NEMU显式使用5亿指令上限；默认5000万可能不足。

手工组合已经完整通过的RTL报告：

```sh
python3 -B single/libmvec/analyze.py \
  --report RUN_A/reports/results.json --report RUN_B/reports/results.json \
  --output NEW_ANALYSIS --require-full
python3 -B single/libmvec/package.py --build "$FULL/build" --output NEW_PACKAGE.zip
```

analyze只接受完整passed RTL报告，并核对manifest、BIN、工具、VLEN一致；`--require-full`要求七函数、ABI及LOG/EXP跨启动复跑齐全。输出groups.csv、analysis.json和中文报告；未齐全时不可宣称完整性能结论。package保留历史绝对路径作为来源，不能把解压后的历史run当新运行重放。

## 覆盖、计时和参考

- 七家族：double log/exp/sin/cos/pow，float logf/expf；36公开ABI独立检查，私有_len不在范围。
- 六实现：scalar_strict、scalar_fast、auto_m1、auto_m2、direct_m1、direct_m2；控制为rdcycle、empty、copy。若构建用数学库导出`_ZGV4Nx*`/`_ZGV8Nx*`，同一矩阵追加direct_m4、direct_m8（逐函数探测符号），CSV每块11个D行；若编译器声明16位变体掩码（`__riscv_simd_math_abi_v1_lmul8`），再追加编译器自动生成的auto_m4、auto_m8，最多13列。
- 全配置n=16/128/1024、K=1/4，log/exp增加16。每组first1＋warmup2＋steady7，模式顺序轮换；保留原始值，不删离群、不扣控制开销。
- 窗口含K次数组调用、访存、循环和调用成本；初始化、oracle、hash、打印在外。first前已做正确性测试，不是冷缓存。rdcycle的编译器memory barrier不是完整硬件串行化。
- 直接Nx ABI始终处理VLMAX，尾部走真实scalar。m2自动路径可能有合法m1尾循环，manifest记录实际调用。
- MPFR512定向舍入上下端点转换到binary32/64必须一致；固定seed确定语料。double误差限制1编码步，logf3、expf2；有限采样不构成全域正确舍入证明。

## 主机回归

```sh
python3 -B -W error::ResourceWarning -m unittest discover -s single/libmvec -p 'test_*.py'
python3 -B single/libmvec/abi-probe.py --check
```

覆盖构建/数值/ABI、runner、严格采集、逐行回收、uint64回绕、缺行/半行/错行/终态、分析分组。主机测试不能替代NEMU/RTL；运行产物与阶段性实测记录放在本地忽略目录，不当作已完成全参数性能结论。
