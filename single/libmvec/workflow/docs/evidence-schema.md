# 证据 CSV schema（版本 1）

首版是 full-development profile；G3/G4/G5 的适用矩阵不能整体省略。以下列名以同目录 `evidence.py` 的 `FORMATS` 和运行时检查为准；CSV 用 UTF-8、表头不能重复、不能有多余/缺少列，逐行 `schema_version=1`。运行前应冻结预登记案例，绝不靠删除结果行或改变适用性掩盖缺口。所有 ID 应能通过已登记的 artifact 定位到完整、可复核的永久证据；自填 ID 本身不证明结果。

## cases.csv：预登记矩阵

```text
schema_version,case_id,gate,function,precision,interface,lmul,vlen,mask,frm,input_group,required,expected_count,phases,metric,limit,unit,vector_required,reason
```

`case_id` 是案例标识；`gate` 为 G3 或 G4；其余维度描述函数、精度、接口、LMUL、VLEN、mask、舍入和输入组。`required` 为 yes/no；非必测需说明 `reason`，并由独立评审判断。`expected_count` 对 G3 是规定检查数，对 G4 是规定窗口数；G4 `phases` 以 `first:数量;warmup:数量;steady:数量` 形式登记实际需要的相位。`metric` 与 `limit` 是误差指标及预算；G4 的 `unit` 和 `vector_required` 冻结统计单位及是否必须实际执行向量调用。规划文件和适用性须绑定契约与批准身份。

新增公共标量函数或精度时，每个 `(function, precision)` 须在 G3 cases 以 `input_group=new-api` 预登记 ABI 探针。该组 `lmul=1|2|4|8`，`interface=fixed|variable`（variable 对应 glibc manifest 的 vla），`mask=masked|unmasked`。必须有 16 格；m1/m2 全 `required=yes`，m4/m8 未承诺项 `required=no` 且有理由，G3 coverage 仍保留对应行和真实状态。每格同 VLEN/frm 不重复；可另列其它 VLEN/frm，用不同输入组另验数值和 fallback。示例：f64 atan2，m2/variable/masked 对应 `_ZGV2Mxvv_atan2`。`new-api` 是任务契约声明；CLI 不解析标量名的业务含义，全部漏标或漏列整个函数须由 G0 独立审查抓出。矩阵校验不是符号表/链接/执行校验，后者按[集成路径](glibc-integration.md)逐格绑定。

## coverage.csv：正确性覆盖

```text
schema_version,case_id,gate,function,precision,interface,lmul,vlen,mask,frm,input_group,status,expected_count,actual_count,metric,limit,observed,reason,evidence_id
```

每个预登记 G3 案例一行，维度及预算与矩阵一致；`status` 使用 `pass`、`fail`、`skip`、`not_run`、`not_applicable`。必测项须通过，`not_applicable` 需理由及独立审阅。`actual_count` 是实际检查数；`metric` 明确真值 ULP、编码步距或其他指标，pass 行的 `limit/observed` 必须是同口径的非负有限预算及观测，且观测不超过预算；布尔语义检查可使用失败数预算 0。采样不证明全域上界。

## runs.csv：运行和 A/B 身份

```text
schema_version,run_id,role,pair_id,status,returncode,completion,checks,failures,identity_id,tool_id,binary_id,library_id,link_id,source_sha256,evidence_id
```

`role` 为 baseline、candidate 或 validation；性能比较的同一 `pair_id` 应各有一个基线和候选。`status=pass`、零返回码、`completion=complete`、正数 checks 和零 failures 是接受条件而非完整语义证明。`identity_id` 指向已登记的环境键值 CSV；`tool_id` 与 `evidence_id` 指向已登记工具和原始运行证据；`binary_id`、`library_id`、`link_id` 分别指向每次运行的二进制、所用库及链接/加载证据登记项，A/B 对象可以不同；`source_sha256` 必须对应原始运行证据 hash。环境必须登记 simulator_family、simulator_sha256、reference_sha256、dram_sha256、isa、vlen、toolchain_sha256、harness_sha256、input_sha256、flags、window。还须独立确认实际链接库、配置和证据范围；共用身份标签不能代替真实相同环境。

## measurements.csv：原始窗口

```text
schema_version,run_id,case_id,sample_id,phase,cycles,cycle_begin,cycle_end,counter_bits,calls,elements,timing_repeat,vector_calls,vector_elements,unit,checksum,status
```

`phase` 为 first/warmup/steady；每个案例须有 steady 数据。`cycles` 须匹配开始/结束计数器，`counter_bits` 为 32 或 64，可按该宽度回绕。`calls`、`elements`、`vector_calls`、`vector_elements` 均为窗口内实际数量，不是每次重复的数量；其已包括 `timing_repeat`，因此归一化只能除实际分母一次。`unit` 为 `cycle/window`、`cycle/call`、`cycle/element`、`cycle/vector-call` 或 `cycle/vector-element`，向量口径分母分别为实际 vector_calls/vector_elements，不能用全部元素替代。须有实际路径证据，尤其 N 小于 VLMAX 时不得把全标量尾循环当成向量测量。同一 run/case 固定输入的重复 checksum 不得漂移；A/B checksum 可以不同，需要由正确性契约单独裁定。

## claims.csv：结论索引

```text
schema_version,claim_id,kind,metric,selector,operator,expected,unit,scope,text,evidence_id
```

`kind` 为 numeric、qualitative、hypothesis；数值支持 `baseline`、`candidate`、`change_pct`、`coverage_pass_count`。前 3 项 selector 为 `pair_id:case_id`，覆盖计数 selector 为 `all` 或 gate；操作符为 `eq`、`le`、`ge`、`lt`、`gt`。性能变化单位为 `%`，覆盖计数为 `cases`，其余与测量单位一致。`scope` 与证据 ID 说明结论适用范围和来源；`text` 的自然语言含义、因果和推广范围须独立审阅，数值核对不能证明它们正确。

## 限制

导入适配只搬运已识别字段，缺失身份、动态调用数、观测误差等必须补齐并复审，导入成功不代表通过。报告生成结果须重新核对原始数据、汇总、表格、图和自然语言；工具不会验证 DSH 审阅独立性、RTL 覆盖真实性、数学全域精度或微架构因果。退出状态、部分窗口和错误路径均保留，不能用有结果的样本掩盖缺失案例。
