# 五舍入模式的 MPFR 区间误差主机分析

[mpfr_accuracy.py](mpfr_accuracy.py) 对**已有的真实目标逐 lane 输出**进行离线分析；它不构建、不运行或模拟目标程序，也不从 RNE 编码距离推断相对于数学真值的误差。`corpus_C` 和 `semantic_S_unvalidated` 严格分开：前者是指定冻结语料；后者只是语义观察样本，其样本覆盖并未由本工具验收。

## 环境与输入

- Python 3 标准库和主机 MPFR/GMP 共享库；不需要 `mpmath` 或 `gmpy2`。当前已验证：x86-64 Linux、Python 3.12.3、MPFR 4.2.1、`libmpfr.so.6`。
- ctypes 绑定依赖**经过验证的标准 LP64 MPFR/GMP ABI**（指针和 C `long` 为 64 位）。这不是对其他平台、定制构建或任意 `--mpfr-library` 的 ABI 兼容性声明；两个类型的宽度检查不能证明所有 ABI 配置兼容。使用不同环境前必须独立验证其 MPFR/GMP ABI 和数值结果。
- 文件可以是 CSV、`.csv.gz`，或经 stdin 提供 CSV（`-`）。gzip 文件记录压缩文件的 SHA256；stdin 标注 `stdin-unavailable`，不能用作输入字节身份的证明。
- 表头严格为 `function,interface,lmul,sample_id,frm,x_bits,z_bits,y_bits,ref_bits`，末尾可选 `rne_ref_ulp`（空或无符号十进制，仅校验格式，不参与真值计算）。`function` 为七函数名；`interface` 为 `Nx`/`array`；`lmul` 为 `1`/`2`；`frm` 为 0–4 或 RNE、RTZ、RDN、RUP、RMM。`x_bits`、`z_bits`、`y_bits`、`ref_bits` 为精确的目标格式位串：binary32 恰好 8 位十六进制，binary64 恰好 16 位，允许 `0x` 前缀。一元函数 `z_bits` 为空；`pow` 的 `z_bits` 为第二输入。`ref_bits` 是冻结的目标格式 RNE 参考位，**不是高精度真值**。
- 相同 `(function,interface,lmul,sample_id)` 必须五行相邻，且恰好各出现一种 `frm`，输入和参考位完全相同。同一 `(function,interface,lmul)` 的所有 ID 形成一个不可重开的连续块。语料 ID 为 `C0`、`C1` 等，从零起覆盖所选 profile 的整个冻结范围；语义 ID 为 `S<组号>:<lane号>`，其 `ref_bits` 必须为空。不得重复 ID、缺 ID、缺某个预期块或提供空输入。各块必须覆盖由 `--functions`、`--interfaces`、`--lmuls` 确定的笛卡尔积；缩小选项只代表缩小后的实验范围。
- `--profile unit` 使用七函数冻结单元 oracle 的各自总数；`--profile perf` 每函数使用 1024 条性能输入。`--oracle-dir` 必须显式指向相应冻结头目录。工具检查冻结头硬编码 SHA256，并对每个 `C` ID 逐条核对 x/z/ref；错误或缺失则失败。性能输入范围较窄，不能把其结果推广到单元语料或数学全域。`S` 样本不参与语料完整性或参考匹配合格率。

## 数值方法

IEEE 输入先按符号、指数及尾数重建为精确二进制数：目标输入尾数最多 53 位，而 MPFR 工作精度至少 64 位，整数及二的幂缩放均可精确表示；不使用 Python 浮点值或十进制近似。对有限输入，用 MPFR 的 RNDD 和 RNDU 分别计算函数真值的闭区间。默认依次使用 512、1024、2048 位；边界不能确定时尝试下一精度，仍无法确定则标为未决。MPFR 区间端点通过精确十六进制数字恢复成有理数，不缩回 binary32/64。

每个模式的**绝对数学误差**和实际 RNE 输出的绝对数学误差进行配对：比较两个精确输出的中点与真值区间，仅当区间严格处于中点一侧时判 closer/worse；输出数值相同或可证明真值恰为中点时判 tie；否则 unresolved。这里的 closer/worse 是该行模式**相对实际 RNE 输出**更接近/更远离真值，并非针对正确舍入参考的编码距离。RMM 是目标 `frm` 的一个标签，不将 MPFR_RNDA 当作 RMM。

对有限真值且可判定的 binade，绝对 ULP 误差的分母定义为：正规真值 `2^(floor(log2(|truth|))-fraction_bits)`；次正规、低于最小次正规及精确零使用最小次正规间隔。误差分箱是对完整真值区间证明得到的闭包，不使用参考编码步数。`zero` 为可证明误差恰为零；`le_0.5` 为 `(0,0.5]`，`le_1` 为 `(0.5,1]`，`le_2` 为 `(1,2]`，`le_4` 为 `(2,4]`，`gt_4` 为 `(4,+∞)`；跨阈值、跨 binade 或未能证明者归入 `unresolved`/`cross_binade`。

真值区间端点分别转为目标格式 RNE 后，只有两端结果一致时才比较 `ref_bits`。`mismatch` 和 `zero_sign_mismatch` 使进程非零退出；特殊值、MPFR 自身范围异常、区间未决等单独计数，**不能将它们算作参考已通过**。`rc=0` 仅说明没有已证实的参考不一致或输入错误，不表示所有参考均已证明，也不代表所有目标输出正确。

## 输出与特殊值边界

stdout 为汇总 CSV，字段为 `function,interface,lmul,sample_kind,frm,metric,category,count`：

- `samples/total`：该组的样本数；`truth`：真值区间分类，包含 `normal_range`、`subnormal_range`、`below_minsubnormal`、`cross_minsubnormal`、`exact_zero`、`outside_target_range` 和其他边界/特殊值。
- `comparison`：`closer`、`tie`、`worse`、`unresolved`；`comparison_eligibility` 给出可比较或不可比较原因（如 `truth_*`、`finite_truth_output_inf`、`finite_truth_rne_output_nan`、`midpoint_unresolved`）。`finite_truth_output` 单独统计已知有限真值下实际输出的类别，以免漏掉错误的 Inf/NaN。
- `absolute_error_ulp_bin`：前述误差分箱或未决；`reference`：`match`、`mismatch`、`zero_sign_mismatch`、`unresolved`、`zero_sign_unresolved`、`absent` 等；`precision_bits`：最后使用的精度，零表示因为输入特殊值未执行 MPFR。
- `input_x`、`input_z`、`output`：按正负正规数、正负次正规数、正负零、正负无穷、qNaN、sNaN 分类，分组样本数和各类分母均须单独报告。`mpfr_underflow`/`mpfr_overflow` 为 MPFR 本身范围异常；`extreme_tiny_guard`/`extreme_large_guard`/`cross_extreme_guard` 为保护主机整数资源而不展开极端指数，与中点未决不同。

stderr 记录 MPFR 版本、库名称、指数范围、输入和代码 SHA、冻结头 SHA、精度与所选范围。**每个**已证实的语料参考不一致均流式输出一条 CSV 记录（不截断），列顺序为：

```text
REFERENCE_MISMATCH,function,interface,lmul,sample_id,status,x_bits,z_bits,ref_bits
```

其中 `status` 分别为 `mismatch` 或 `zero_sign_mismatch`，一元 `z_bits` 为空；记录与诊断 metadata 共存于 stderr，须完整保留。未决样本以汇总分类计数，不伪报为 mismatch。

数值绝对误差中 `+0` 和 `-0` 都是实数零；工具的符号位分类和 `zero_sign_mismatch` **不是跨 frm 零符号语义正确性证明**。NaN 只按位观察 qNaN/sNaN 类别，未验证 payload、传播和 quieting 规则。CSV 不包含逐 lane `fflags`/`errno`，本工具不验异常标志、sticky、errno、frm 恢复、向量 PCS 或完整数学接口语义；这些需要另行设计并运行相应验证。对 Inf、NaN、目标格式溢出、MPFR 范围异常和未决区间，不应凭空给出有限真值优劣统计。

## 示例与验证

已有完整采集 CSV 时，用户确认输出位置后执行，例如：

```sh
E=/absolute/path/to/experiment
SRC="$E/source/single/libmvec"
python3 -B "$SRC/tests/newmath/mpfr_accuracy.py" /absolute/path/lanes.csv.gz \
  --oracle-dir "$E/inputs/oracle-headers" --profile unit \
  --functions sin,cos,log,logf,exp,expf,pow \
  --interfaces Nx,array --lmuls 1,2 >summary.csv 2>analysis.stderr
```

命令中的两个重定向由调用者负责，须使用获授权的独立日期运行目录；本分析器不会创建输出文件。对性能样本须相应改为 `--profile perf --oracle-dir "$E/inputs/perf"`，不得混淆两类语料。运行后应同时检查真实进程退出码、输入和冻结身份、全部 `truth`/`reference`/`comparison_eligibility` 未决分母及 stderr 的完整记录；不要用 exit code 代替这些检查。

主机单元测试（不运行目标仿真）：

```sh
python3 -B "$SRC/tests/newmath/test_mpfr_accuracy.py" -v
```

测试涵盖 256/512 位区间嵌套、七函数冻结 perf 首样本参考、sin/cos 最大有限输入、pow 负底非整数、次正规/零、MPFR 极端范围、完整分块及缺样本、gzip 解码、全部参考 mismatch 记录与非零退出。测试中复制参考位以构造的五模式输出仅用于验证分析流程，不是目标模式精度数据。
