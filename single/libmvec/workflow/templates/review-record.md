# 独立审批记录模板（record-review）

> 用途：人工准备审批材料，再由 `cli.py record-review` 落盘为 `reviews.csv` +
> `review_items.csv`。本模板不是证据本身；CSV 记录绑定当时的 `target_digest`，
> 证据（artifacts/标准表/freeze）任一处变化都会使该审批 stale。
> 审批人必须不是本门禁及全部上游 artifact/freeze 作者，且不能由推进人兼任；G3/G5/G6 至少 2 个非作者、不同 reviewer、不同角色
> （code / semantic / data / process）。Lead 核验引用在 `advance` 时必填，CLI 不代替核验。

## 基本信息

| 字段 | 值 |
|---|---|
| gate | G_ |
| reviewer（agent 名） | |
| provider / model | |
| reviewer_role | code / semantic / data / process |
| author_scope（审查范围：文件、矩阵、表格、逐项 ID） | |
| verdict | approve / request_changes / blocked |
| session_id | |
| event_seq_from / event_seq_to | |
| 冻结对象（commit / artifact_id / sha256） | |

## 逐项判定（每项对应一条 `--item ID:JUDGEMENT:REASON[:EVIDENCE_ID]`）

| item_id | 判定 pass/fail/na | 理由（必填） | 证据（artifact_id 或 data 行定位） |
|---|---|---|---|
|  |  |  |  |
|  |  |  |  |

规则：
- 任一 item 为 `fail` → verdict 不得为 `approve`。
- `na` 必须写理由，且说明为何不适用（适用范围来自已冻结契约，不新增门禁状态）。
- 只有本表 + 落盘 CSV 一致时才作为审批证据；不得手改 CSV 绕过 CLI。

## 提交命令

```sh
python3 cli.py record-review --task <task> --gate G0 \
  --reviewer <agent> --provider <provider> --model <model> \
  --role <code|semantic|data|process> --scope '<范围>' \
  --verdict approve --session-id <sess> --event-from N --event-to N \
  --item 'G0-01:pass:范围理由:A2' --item 'G0-02:pass:语义理由:A2' \
  --item 'G0-03:pass:方案理由:A3' --item 'G0-04:pass:工作区理由:A3'
```
