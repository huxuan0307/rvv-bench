# 五种 frm 观察结果的组级比较

此独立工具对两个**已经完成分析**的七函数舍入观察结果进行配对比较。它不修改、导入或执行历史观察器，不要求用新观察器重新分析历史 run，也不启动目标仿真。原有 `rounding_accuracy.py compare` 的 CLI 和结果不变。本工具使用 Python 3 标准库，无额外 Python 依赖。

## 输入与完整性

输入分别是 baseline 和 candidate 的分析目录，要求包含原观察器生成的 `COMPLETE.csv` 和五个结果文件。工具：

1. 严格核对两个 COMPLETE 的字段和 `lanes.csv.gz`、`groups.csv.gz`、`summary.csv`、`paired.csv`、`coverage.csv` 各自的 SHA-256；要求角色分别为 baseline/candidate、函数和顺序相同、archive SHA 不同，且 environment 与 observer SHA 相同。
2. 依据双方相同的 coverage 和 VLENB，按观察器的顺序及分组规则对齐每个函数、接口、LMUL、frm、样本类型及 group ID，并检查 `n`、真实 lane 数 `emitted`、incoming VL、corpus 分组和语义分组。根据第一块 Nx 语义组推导语义数，再检查其余组及不同模式的一致性。
3. 流式读取两侧 lane 数据，严格核对样本 ID 和五个模式的顺序；对每一个样本检查两侧及五模式之间的 `x_bits`、`z_bits` 和 `ref_bits` 一致。单目函数的 `z_bits` 与语义样本的 `ref_bits` 必须为空。输出同时按作用域计数 `y_bits` 的差异。
4. 逐组比较调用返回后的 `frm_after`、组级 `fflags` 和 `errno`。任意缺失、重复、顺序或组身份不一致均拒绝；发布前重新核对两份 COMPLETE、五个文件和本比较器的 SHA。分析量大，数据按行流式读取，不会把全部 lane 载入内存。

这是对 COMPLETE 声称已核验的结果进行进一步配对，**不重新执行旧观察器，也不重新解析原始 NEMU stdout/stderr 或验证模拟器执行**。SHA 是文件身份与防止意外变化的校验，不是签名或防伪证明；它不能发现采集阶段串口传输本身发生、之后又被正常登记的错误。Nx 补齐 lane 不在 `lanes.csv.gz` 中：其内容正确性依赖历史观察器对原始 UART 的校验；此工具只复核组大小、有效 lane 和导出的真实输入，不能独立复验补齐值。

## 使用

以下路径应指向此前已授权、名称含日期时间且不位于任何 `build` 路径的独立运行目录。比较结果及两个日志放在 baseline 的运行目录下；输出文件必须不存在：

```sh
SRC=/nfs/home/huxuan/repos/riscv-gnu-toolchain/.dsh/experiments/20261114-1646-sevenmath/source/single/libmvec/tests/newmath
BASE_RUN=/替换为已有的日期时间-baseline运行目录
CAND_RUN=/替换为已有的日期时间-candidate运行目录
(cd "$BASE_RUN" && TMPDIR="$BASE_RUN/tmp" TMP="$BASE_RUN/tmp" TEMP="$BASE_RUN/tmp" \
  PYTHONDONTWRITEBYTECODE=1 python3 "$SRC/rounding_group_compare.py" \
  --baseline "$BASE_RUN/analysis" --candidate "$CAND_RUN/analysis" \
  --output "$BASE_RUN/analysis/group-comparison.csv" \
  >"$BASE_RUN/group-compare.stdout" 2>"$BASE_RUN/group-compare.stderr")
```

本工具仅在 baseline 分析目录下创建指定的新 CSV，并在其同级临时生成 `.partial` 文件；校验全部通过后，以不覆盖已有文件的方式原子发布。文件系统必须支持同目录硬链接。若被外部强制终止，可能残留 `.partial`，它不是有效结果。默认 CSV 包含**有组级状态差异的组**及所有作用域汇总；增加 `--all-groups` 可输出全部逐组记录，结果可能很大。

## 输出列与口径

- `record_type`：`group` 为一条真实调用组，`summary` 为按函数/接口/LMUL/frm/类型汇总。所有作用域都有 summary；没有状态差异时默认没有 group 明细。
- `function`、`interface`、`lmul`、`frm`：函数、`Nx` 或 `array`、1 或 2，以及输入的 frm 数值 0–4。`kind` 的 `C` 表示冻结 corpus，`S` 表示单独的语义样本。
- `group_id`：`C` 组的 corpus 起始索引，或 `S` 组的语义组号。`n` 是调用长度，`emitted` 是导出真实 lane 数，`incoming_vl` 是进入 Nx 的 VL；array 为 0。这些列仅在 group 记录填写。
- `baseline_frm_after`、`candidate_frm_after`、`baseline_fflags`、`candidate_fflags`、`baseline_errno`、`candidate_errno` 是对应调用的实际返回状态；`fflags_xor` 是两侧 fflags 的十进制按位异或，便于定位哪些位不同。fflags 位值分别为 NX=1、UF=2、OF=4、DZ=8、NV=16。
- `groups_compared` 是比较组数；`frm_after_differences`、`fflags_differences`、`errno_differences` 是对应值不同的组数；`any_state_differences` 是至少一项状态不同的组数，**不是前三者相加**。在 group 行中这些计数为 0 或 1，在 summary 行中为同一作用域内的总数。
- `lane_bit_differences` 仅在 summary 行填写，表示同一作用域真实输出 `y_bits` 不同的 lane 数；Nx padding 不计入。它不是 ULP 误差，也不能单独判定数值正确与否。
- `baseline_complete_sha256`、`candidate_complete_sha256` 是分析清单身份，`comparator_sha256` 是实际执行的本比较脚本身份；逐行记录，便于事后追溯。它们均为小写十六进制 SHA-256。

观察器在每次调用前清零 flags 和 errno。本工具的状态记录是**整组聚合结果**，不是每 lane 的状态，也不是对 sticky flags/errno 保留、ABI、零长度、in-place、全部 canary 或 fallback 策略的验收。差异只表示观察到两个构建的结果不同，并不自动证明任一方正确或将差异归因于数学 archive。数学误差判断仍须独立 MPFR 真值分析。

## 主机合成测试

测试生成的是临时合成记录，不调用 NEMU 或任何目标程序。请将输出放入新的、名称含日期时间的独立运行目录，设置 TMP 路径，并分开 stdout/stderr：

```sh
RUN=/替换为尚不存在的日期时间-host测试运行目录
[ ! -e "$RUN" ] || exit 1
mkdir -p "$RUN/tmp"
(cd "$RUN" && TMPDIR="$RUN/tmp" TMP="$RUN/tmp" TEMP="$RUN/tmp" \
  PYTHONDONTWRITEBYTECODE=1 python3 "$SRC/test_rounding_group_compare.py" \
  >"$RUN/stdout" 2>"$RUN/stderr")
```

测试覆盖双目/单目、组级 flags/errno 和输出位差异、校验和变化、分组丢失/重复/乱序、输入位和模式不一致、角色/observer/environment 不符、处理期间输入改变、已有输出以及 CLI。主机测试通过不代表目标仿真通过。
