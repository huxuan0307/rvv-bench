# 工具操作、权限和恢复

## 作用与非目标

本目录 CLI 是离线门禁记录和机器检查入口，不是 DSH 调度器，不运行构建、测试或仿真，不自动创建 agent，不自动提交、推送、发布、清理或恢复测试。其状态不能替代独立评审、实际数值/ABI/RTL 验证或 Lead 对会话证据的核查。使用前运行入口的 `--help` 并以当前代码支持的参数为准；旧工具的适用域及文档可能漂移，入口不存在或数据 schema 不匹配应阻断，不能假装已验证。

## 典型流程（命令名，不暗示已满足条件）

入口为 `python3 single/libmvec/workflow/cli.py`；在对应源码仓库根目录执行，并给出显式任务目录。依序使用：

1. `init`：建立门禁记录；已有非空记录应拒绝覆盖。
2. `freeze`：记录 G0 契约/范围/矩阵、G1a 设计/协议及 G1b 基线/环境的冻结项和文件身份；冻结本身不是批准。
3. `check`：检查指定门禁或当前状态，阻断项必须修复或明确标为未完成；不要将机器检查当作语义判断。
4. `record-review`：记录独立审查结论、逐项意见、会话与事件范围；Lead 另行核验。作者与审查者不得是同一人，但不同字符串也不证明独立。
5. `advance`：在全部前置条件及 Lead 核验满足时以预期 revision 推进；并发冲突需重新读取状态后审查，不能盲目覆盖。
6. `status`：查看阶段、身份、审查及失效情况；`report`：在任务 `report/` 下尚不存在的子目录生成非当前交付依据的草稿，独立审查后按规范另行发布至根目录；`import-data`：按支持的 kind 导入数据，具体格式由工具及其模块决定。

### 可复制的 G0 最小操作模板

下列命令会写任务状态，仅在已有授权和任务目录下运行。先把占位值替换为实际值；各文件须事先存在。不同任务不应照搬示例身份。契约、范围和矩阵文件须均在任务目录内；`cases.csv` 按 [schema](evidence-schema.md) 准备，Lead 必须核实真实 DSH 事件。以下展示命令形式，不代替各 checklist 项的实质审查。

```bash
TASK=/absolute/path/to/existing-task
CLI=single/libmvec/workflow/cli.py
python3 "$CLI" init --task "$TASK" --author lead
python3 "$CLI" artifact --task "$TASK" --gate G0 --kind contract --path data/contract.md --author writer --artifact-id CONTRACT
python3 "$CLI" artifact --task "$TASK" --gate G0 --kind target_matrix --path data/workflow/cases.csv --author writer --artifact-id MATRIX
python3 "$CLI" freeze --task "$TASK" --gate G0 --author writer \
  --file contract_sha256=data/contract.md --file scope_sha256=data/scope.md \
  --file target_matrix_sha256=data/workflow/cases.csv
python3 "$CLI" check --task "$TASK" --gate G0 --evidence-only
```

预检成功不代表审批通过。以下审阅各项的理由、证据 ID、模型及事件应替换为真实内容：

```bash
python3 "$CLI" record-review --task "$TASK" --gate G0 \
  --reviewer reviewer --provider PROVIDER --model MODEL --role process \
  --scope '契约与目标矩阵' --verdict approve --session-id SESSION \
  --event-from 1 --event-to 10 \
  --item 'G0-01:pass:已核范围:CONTRACT' \
  --item 'G0-02:pass:已核语义:CONTRACT' \
  --item 'G0-03:pass:已核方案:CONTRACT' \
  --item 'G0-04:pass:已核工作区:CONTRACT'
python3 "$CLI" check --task "$TASK" --gate G0
python3 "$CLI" advance --task "$TASK" --gate G0 --author lead \
  --expect-revision 0 --lead-verification '已核实的会话与事件引用'
python3 "$CLI" status --task "$TASK"
```

`0` 仅适用于尚无已推进状态的首次 G0，重试须先读取当前 `state.csv` 的最大 seq；reviewer 不能为作者或推进人。后续门禁所需证据、冻结键、审阅数量及全部逐项检查见实现与 [checklist](checklist.md)，不能以该示例充当全部门禁。替代已变更的同路径证据时可用 `artifact ... --supersedes OLD_ID`；缺省时同路径同 kind 当前项也会被替代，替代后须重审受影响阶段。`import-data` 只生成待核对的适配结果和来源记录，不自动登记或通过门禁。

详细参数、支持的证据类别和退出码以 `--help` 及当前实现为准：0 为成功、1 为门禁阻断、2 为输入或工具错误。缺少证据模块或格式未知时不得把输出当通过。工具生成的 CSV 是机器可读证据，不取代面向用户的 Markdown 报告。

## 主机回归与依赖

依赖 Python 3.10+ 标准库及 POSIX `fcntl`；没有第三方 Python 包，不适用于未适配的 Windows 文件锁环境。以下只运行离线合成数据/状态测试，不启动 RISC-V 仿真。`TASK` 指向已获授权的现有任务，`SRC` 是对应 worktree 源码的绝对路径。

```bash
RUN="$TASK/log/$(date +%Y%m%d-%H%M%S)-workflow-tests"
mkdir -p "$RUN"
export TEST_TMPDIR=$(mktemp -d "$RUN/fixtures-XXXXXX")
# 运行现场是临时输出；需长期保留的结果另行归档到任务 data。
(cd "$RUN" && python3 -B -m unittest discover -s "$SRC/single/libmvec/workflow" -p 'test_*.py' -v >stdout 2>stderr)
```

标准输入：按 schema 预登记 CSV、永久证据与审阅记录；标准输出：CSV 状态、诊断、报告文件。副作用只在指定任务产生或追加文件，不覆盖既有报告目录。`report` 产出 CSV/Markdown/SVG 草稿，不授予批准；`import-data --kind libmvec-samples` 只接受采集器已知样本列并剔除 calibration 记录（原件保留），`--kind newmath-coverage` 只接受既有 newmath 覆盖列。补齐身份和实际工作量后才能作为正式证据登记。

不完整写入应停止使用并由 Lead 恢复：文件锁防止合作进程并发推进，但不是跨 CSV 的断电事务，也不是对恶意修改者的安全隔离。缺失/破损行可能报输入错误或阻断；不能宣称具备签名、崩溃自动恢复或自动认证 DSH 事件的能力。

## 写入、运行与发布边界

- 所有正式代码仅在任务目录的对应 `worktree/` 中修改；隔离共享分支和安装前缀，不动其他会话未提交现场。每一仓库单独检查 diff 和暂存。
- 测试不得在任何 `build` 路径下运行；测试工作目录、staging、日志与模拟器临时输出使用独立且带日期时间的运行目录。stdout/stderr 分离。任务顶层白名单见[派工模板](roles-and-templates.md)。
- 工作区外输出、可复用新脚本、提交等依具体用户授权和规范；已授权的代码交付不意味着获准 push/发布、删除、清理或启动长仿真。定时任务须用户明确授权。
- 进程在当前 PID namespace 或主机中不可见，不证明远端进程已经结束；PID 存在也不能排除复用。只能视作提示，以可核验的运行终态、身份和逐 case 证据判定。停止的测试不得自动恢复，重跑需最新用户授权。
- 中间构建树放获准的任务目录外缓存，任务目录留配方；`build/` 可删除，只留登记来源/hash 的必要产物，不作为测试工作目录。迁移前检查冲突，迁移后核验内容/hash；清理须获授权并核实绝对路径、引用和占用。

## 中断与失效恢复

1. 只读检查当前 task、各仓库 Git 状态、状态记录 revision、运行任务和永久证据；不要根据旧会话或残留 PID 推测成功。
2. 核对本次工作区、基线/候选源和未提交 diff、配置、工具/输入、构建产物、原始数据及审查对象身份；改变的对象及依赖下游标记失效。
3. 对失败、部分、取消和缺失运行逐格标记，保留独有证据；必要时经用户授权重新运行，不覆盖历史结果。明确窗口只部分覆盖时仅剔除受影响的调用/窗口，记录拒收数和原因。
4. 从最早失效的门禁重新检查；必要时重新独立审批，Lead 核实新的会话/事件和绑定身份；revision 不匹配时先读取最新状态及变更原因。
5. 只有全部必需证据、批准和授权重新满足时推进。无法运行 RTL、无法确定语义或审查不可验证时保持阻断/未完成，向用户说明具体缺口。
