# 新函数从算法到已安装的 GNU/Linux 工具链

完成的对象是**用户包含安装的 `<math.h>` 后，由配套 GCC 生成并链接到 glibc libmvec 的调用**，不是实验归档、私有符号或临时声明。声明、实现、可选择、已链接、已执行是不同结论。已有 API 的局部优化保持原有 profile，但不得撤掉既有公开 ABI。新增标量名或精度则必须走以下分支。

## G0：逐函数、逐精度列全 16 个公共 ABI 格

| LMUL | 固定 N（无 mask） | 可伸缩 Nx | 固定 M（有 mask） | 可伸缩 Mx | 实现要求 |
|---|---|---|---|---|---|
| 1 | bit 0 | bit 1 | bit 2 | bit 3 | 全部必做 |
| 2 | bit 4 | bit 5 | bit 6 | bit 7 | 全部必做 |
| 4 | bit 8 | bit 9 | bit 10 | bit 11 | 可选，逐格冻结 |
| 8 | bit 12 | bit 13 | bit 14 | bit 15 | 可选，逐格冻结 |

按**标量符号及精度**填写（例如 `sin` 和 `sinf` 是两项），不是把两个精度、多个 VLEN 或两种 arity 加起来凑八格。低八位必齐；高八位可不做，须记录原因、不公布对应位且消费者走合法回退。选做格一旦承诺，和必做格同等验收；既有已发布的宽接口不能借“可选”撤销。先登记实际已有格、缺格和目标格，再选实现顺序，不盲目复制 16 份数学核心。

在冻结的 G3 矩阵中以 `input_group=new-api` 登记专用接口探针，另列普通、特殊值、状态和性能用例。每个目标逐项对应 case ID、标量原型、预期 mangled symbol/版本、声明位、调用条件、运行证据。G0 审查人将契约中的所有新增目标与登记矩阵对账：没有任何标志行的漏报目标不能靠工具从自然语言推断。

对当前下游齐次 float/double 一元和二元签名：`_ZGV{LMUL}{N|M}{lanes|x}{v或vv}_{scalar}`。定长 lanes = `128 / SEW × LMUL`（架构 ABI 基线，不是按实测 VLEN 重命名）；例 double 二元 `_ZGV2M4vv_atan2`，可伸缩 `_ZGV2Mxvv_atan2`。`x` 是可伸缩向量接口，**不表示传入显式长度参数**。例如 public `_ZGV2Mxv_log(mask,x)` 与 private `_ZGV2Mxv_log_len(n,mask,x)` 有不同签名，后者不能抵一格。其它参数类型、混合精度、非齐次返回或特殊调用约定须另立 ABI 设计，不能套此公式。

## G1a → pilot → G1b：先确定谁可以发出哪一位

1. 固定 GCC、glibc、外层 gitlink 和目标配置，逐层比对 GCC 可生成集合、glibc manifest 集合、安装头实际声明与导出集。`__riscv_simd_math_abi_v1_lmul8` 只代表编译器理解 16 位，不代表库实现 16 格。
2. GCC 的 `gcc/config/riscv/riscv.cc` 中核查 `riscv_simd_math_table` 对名称、原型、arity、模式、位和 legacy 的支持；不在表中的新函数不能只改 glibc。给新名字补匹配/拒绝和 DejaGnu 测试；没有历史 ABI 的函数不要赋予非零 legacy mask。检查 `simd(inbranch/notinbranch)` 与可用位交集、旧编译器低八位、零位与 LTO 混合声明。
3. 区分库对必做格的实现义务和编译器按目标选择的资格。用基线 `rv64gcv/lp64d` 验正例；较大最小 VLEN 下 fixed M 应不被 GCC 选择（不是少实现一格），scalable M 与 fixed N 单独验证。LMUL4/8 正例使用允许宽度的 `-mrvv-max-lmul`；另验低上限不发宽调用。匹配 `-mrvv-vector-bits`、fast-math、向量化开关，预先约定预期选中或合法回退，不能把每个应用循环都必须选中写成契约。
4. 设计双向失配和有条件的编译实验：新 GCC/旧 sysroot、旧 GCC/新 sysroot、`--disable-mathvec`、无 V 或不同 ABI、不满足 fast-math、禁用某可选位。要求不产生不存在的动态或静态引用；同一函数多 TU / LTO 两种链接次序测试。G1a 批准后才 pilot；抽一必做格贯通真正的安装头、应用目标文件、库、执行，再冻结 G1b 基线，不提前宣称全矩阵通过。

## G2：在 glibc 内实现，而非停在测试仓库

在 glibc 分支查 `sysdeps/riscv/rv64/rvd/`：

- 更新 `mathvec-variants.mk` 的列表与 row：function/scalar、precision、arity、lanes、mode(`fixed/vla`)、lmul、mask(`N/M`)、routine、symbol、version、capability、availability、test。共享 kernel/表与分开的 ABI wrapper 均可；确认调用约定、mask-first、二元实参顺序和向量寄存器组对齐。fixed 只计算约定 lanes；public scalable 按其寄存器组契约处理，禁把私有 `_len` 宣告为 public。
- 更新 routine、Makefile 编译/安装条件、`Versions` 与 `libmvec.abilist`；版本节点按目标分支 ABI 策略，不机械复制例子。RVV 对象单独编译，不把基础 libc 或别的 multilib 强制升级到 RVV。
- 修改契约生成器中的 scalar/arity 和 masked allowlist、按需改测试器的期望集合；检查 `gen-mathvec-header.py`、`gen-mathvec-fortran.py`、`sysdeps/riscv/bits/math-vector.h`。从真实 manifest 生成安装头，**不要手改生成结果或无条件宣告 0xffff**。历史 Fortran v1/v2/v3 契约不能暗中扩大；需要向新 Fortran 消费者公开时，设计版本化契约与旧编译器降级。
- 补各 precision、LMUL、N/M、fixed/VLA 的 glibc math tests、test wrapper/头和精度预算。检查 wrapper 本身是否真调用目标符号，不能用同一个 fixed wrapper 冒充 VLA；inactive 注入危险值测试不新增异常，别把未承诺的 inactive 返回位当结果契约。

## G3/G4：安装后验收，不能用 manifest 代替 ELF

1. 在独立构建目录及前缀，从指定组件 SHA 按外层 Linux/glibc 配方构建；记录 stage1 → glibc headers → 对应 multilib 的 glibc 安装 → stage2，核 `-print-sysroot`、multilib 和实际 include 搜索顺序。基线与候选不得互相拾取头或库。若新增函数没有旧向量入口，G4 明示可调用基线，例如同源标量 libm，不造一个不存在的旧 ABI 来做 A/B。
2. 用 `python3 -B sysdeps/riscv/rv64/rvd/check-mathvec-variants.py` 在 glibc 源树做结构核对；带 `--cc DRIVER --function SCALAR` 的探针增加编译证据，但自写 extern 不等于已安装 `<math.h>`。同时运行安装头的 C 契约探针及对应 Fortran、glibc libmvec 与 GCC 用例；核动态版本化导出（如 `readelf -WsV`）及静态归档符号。在目标不提供某链接模式时记录范围与理由。
3. 每个承诺格至少有**具体符号和原型 → 在可选中条件下的编译器调用/重定位 → 真实 sysroot 链接/装载 → 功能执行**的证据。另用直接 ABI caller 隔离包装器错误。保存无手工属性的普通应用源码、编译命令、优化 dump、对象未定义符号、链接 map、已安装库及 ELF hash。负例检查不存在的符号**没有被引用**，而不只是程序偶然能链接。高 VLEN 等被排除选择的形状用独立 fallback 用例，不用 `not_applicable` 代替基线正例。
4. 用最终 SDK 分别编译和链接 LTO off/on 的普通消费者，至少覆盖同一应用内两批 libmvec 调用、strict 无向量调用及可用的静态/动态链接。可用外层 `scripts/libmvec/test-autovec.py --lto off,on` 覆盖已有 scalable N 函数；其余 ABI 格用等效消费者。保存实际编译与链接命令、优化诊断、链接 map、最终 ELF 中的调用指令和执行结果；不能用 LTO 中间对象的声明或符号表替代链接后调用证据。oracle 和结果检查器保持非 LTO。此项不替代 G1 的混合声明/链接顺序验证。
5. 对 0/1/混合 mask、短数组、整向量与尾部、不同 VLEN 核验 active 值、`frm`、既有和新增 `fflags`、ABI/栈；高风险改动按 G3 执行对应 RTL。G4 用最终工具链、安装库的消费者路径；分列直接调用和自动向量化。若主张 LTO 降低多次调用开销，按相同源码、库和输入做 off/on 对照，区分静态调用点数和动态调用次数，检查调用前后栈与向量状态指令并测量周期。非 LTO 构建的库不会因消费者开启 LTO 而被跨库内联；未重编重链不引用旧性能结论。

## G5/G6：报告与外层交接

按目标逐格发布 planned/implemented/advertised/emitted/linked/executed 及证据；合法回退与失败分列。组件提交分别交付 GCC（仅必要时）、glibc（实现、声明、导出、测试）与测试工具。核外层 `git ls-tree <superproject> gcc glibc` 和组件 SHA；在独立外层候选工作树按目标配方验证这些**确切 gitlink** 能生成可用 SDK。若只测试了组件工作树、下游尚未接收组件 commit、或未获更新外层指针的授权，状态只能写“组件已交付，整链待集成”，不能写“已集成”。外层更新只提交相应 gitlink/必要配方，不夹带其它子模块，merge/push 另行授权。
