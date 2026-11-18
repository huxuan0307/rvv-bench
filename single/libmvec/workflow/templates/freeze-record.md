# 冻结记录模板（freeze）

> 用途：人工准备冻结清单，再由 `cli.py freeze` 落盘为 `freeze.csv`。`*_sha256` 键
> 除 diff_sha256 可填写 64 位十六进制摘要外，必须用 `--file` 引用真实文件（check 时重算）；contract/target_matrix/design/protocol 还与对应 kind 的已登记 artifact 交叉核对。
> 键名决定门禁，未知键/重复键/跨门禁键一律拒收。

## G0（契约、范围与预注册矩阵；在设计与实现之前冻结）

| 键 | 来源（文件或说明） | 记录方式 |
|---|---|---|
| contract_sha256 | 验收契约/批准计划 | `--file contract_sha256=<path>` |
| scope_sha256 | 适用范围与非目标（含 N/A 判定依据） | `--file scope_sha256=<path>` |
| target_matrix_sha256 | 预注册矩阵 `data/workflow/cases.csv` | `--file target_matrix_sha256=data/workflow/cases.csv` |

涉及精度时，契约包含[真值 ULP、双档与语料要求](../docs/accuracy-screening.md)，矩阵单列数值、状态和消费者回归。G0 不额外填 `oracle_sha256` 等 G1b 键。

## G1a（设计与测量协议）

| 键 | 来源 | 记录方式 |
|---|---|---|
| design_sha256 | 设计/实现方案文档 | `--file` |
| protocol_sha256 | 测量协议（窗口、K、归一化、拒收规则） | `--file` |

## G1b（同源基线与环境；libmvec 开发 profile 全套）

| 键 | 示例 | 记录方式 |
|---|---|---|
| repo | glibc/gcc/rvv-bench 路径 | `--set repo=<path>` |
| commit | 被测与基线 commit（唯一提交人记录） | `--set commit=<sha>` |
| diff_sha256 | 未提交差异指纹（干净时为实际空 diff 的 SHA256，不写 empty） | `--set diff_sha256=<sha>` |
| toolchain | GCC/binutils/sysroot 前缀 | `--set toolchain=<path>` |
| emu | 模拟器名与 sha256 | `--set emu=<name:sha>` |
| refmodel | 参考模型 sha256 | `--set refmodel=<sha>` |
| inputs_sha256 | 冻结输入头/语料清单 sha256 | `--file inputs_sha256=<path>` |
| oracle_sha256 | 主机 oracle 的版本、精度阶梯、工具及包络数据 SHA256 清单 | `--file oracle_sha256=<path>` |
| seed | 随机种子 | `--set seed=<n>` |

适用精度规约时，两份冻结清单须能定位数据和成员 SHA256；审查者复核成员身份。CLI 重算清单或已单独登记文件的哈希，不理解清单内的精度语义。

## 命令

```sh
python3 cli.py freeze --task <task> --gate G1b --author <agent> \
  --set repo=glibc --set commit=<sha> --file diff_sha256=<archived-diff-path> \
  --set toolchain=<prefix> --set emu=<name>:<sha> --set refmodel=<sha> \
  --set seed=123456789 \
  --file inputs_sha256=<path> --file oracle_sha256=<path>
```

冻结后修改任一文件 → `check` 报 `E-STALE-FREEZE`；重新冻结产生新 `freeze_id` 并改变
`evidence_digest`，所有下游门禁自动 stale（需重新审批，不能沿用旧 approve）。
