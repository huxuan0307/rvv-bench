# 七函数 array / Nx 正确性门禁

本目录只保留说明；实际工具在 `../../script/accuracy/`，主机测试在 `../../tests/host/`。下文旧示例中的 `tests/newmath/*.py` 路径属于历史布局，不能直接执行。

本脚本恢复并改造历史门禁，不构建或修改数学库。支持七函数、两个 LMUL 的完整历史 MPFR512 样本及语义测试；此版**未分片**，确保所有原 array 调用的长度、顺序及变长 chunk 相位保持不变。不得把本说明当成已经运行通过的证明。

## 输入一次物化，多库镜像共享

准备一个显式输入目录，包含六个 gzip 格式的 `.dat`：`exp-unit.dat`、`expf-unit.dat`、`log-unit.dat`、`logf-unit.dat`、`trigvec-unit-data.dat`、`powvec_unit_data.dat`。解压字节的 SHA 必须与脚本内冻结值完全一致。准备命令只运行一次，不允许覆盖目标：

```sh
python3 -B single/libmvec/script/accuracy/gate.py prepare \
  --oracle-dir /绝对路径/压缩oracle \
  --headers-dir /绝对路径/共享oracle-headers
```

输出 `oracles.csv` 记录每函数的样本数、原 ULP 限制及压缩、解压 SHA；后续各镜像均显式使用同一个 `--headers-dir`，启动前重新检查解压 SHA，脚本不会假设任何旧任务目录。原容差：sin、cos、log、exp、pow 是 1 ULP；expf 是 2 ULP；logf 是 3 ULP。不可将它们写成全部 1 ULP，也不允许放宽。

## 编译和运行

```sh
python3 -B single/libmvec/script/accuracy/gate.py run \
  --new-archive /绝对路径/libnewmath.a \
  --gcc /工具链/bin/riscv64-unknown-linux-gnu-gcc \
  --scalar-libm /绝对路径/libm-2.44.a \
  --headers-dir /绝对路径/共享oracle-headers \
  --build-dir /绝对路径/build/本次门禁编译 \
  --run-dir /绝对路径/runs/20261114-170000-本次门禁 \
  --build-only
```

移除 `--build-only` 并明确给出 `--nemu /绝对路径/riscv64-nemu-interpreter` 才会运行；应先获得长仿真授权。每次调用都从新目录重新构建，不能拿 `--build-only` 的已有目录作为后一阶段输入；重跑应换两个新目录。两目录必须均不存在，互不包含，运行目录及全部上级路径不得含 `build`，目录名必须含日期时间，模拟器及编译命令的 cwd 均为该运行目录；每条命令的 TMPDIR/TMP/TEMP 都指向运行目录下独立 `tmp/<命令名>`。不要在 build 路径运行测试。仅构建不会产生 pass。可选 `--functions log` 等子集；sin/cos 的原 array 单元绑定，必须一起指定。`--lto` 供 LTO archive 使用。`--max-instr` 和 `--timeout` 是硬上限，超限失败，不得通过删测掩盖。当前不启动 gsim。

编译与链接显式 `-march=rva23u64`；测试 TU 禁用 fast math、融合、builtin 和自动向量化。GCC、libm 显式指定，配套 binutils、libc、libgcc 由 GCC 查询。每个 case 的构建 ELF/BIN 保留在编译目录，并把 BIN 独占创建复制到本次运行目录的 `staging/<case>.bin`，校验两份 BIN 的 SHA 后才可使用；NEMU 实际命令读取的是 staging 副本，不读取 build 路径下的 BIN。运行前、运行后（含失败或超时）和所有 case 结束后核验构建 ELF/BIN、staging BIN 与数学 archive 的身份；拒绝越界或符号链接 staging 路径。`--build-only` 也会完整创建并验证 staging，但不运行目标，也不代表测试通过。

运行目录 `commands.csv` 有完整命令、cwd、返回码及分离的 stdout/stderr；其中 `<case>-nemu` 的 `command` 记录实际 staging BIN 路径，编译和 objcopy 命令仍记录构建目录下的产物。`tests.csv` 有状态与产物 SHA，`identities.csv` 有库/工具/oracle/源码 SHA，`coverage.csv` 逐函数、接口和 LMUL 给出预期与实际样本数及限制，`summary.md` 给出汇总。失败或超时停止，无汇总 pass。两次 TLS offset 测量必须一致。外部预编译依赖的历史 march 不作额外声明。

`coverage.csv` 的 `interface` 为 array 数组入口或 Nx 无 mask 向量 PCS 入口；`lmul` 为 1 或 2；`expected_count` 为冻结 oracle 数，`actual_count` 为动态比较过的样本数；`ulp_limit` 为对应历史限制；`status=not-run` 表示没有完成该组，`pass` 表示该组对应整个 case 的日志校验通过。`tests.csv` 的 `staging` 表示正在复制或校验副本，`built` 仅是成功链接且 staging 通过，`running` 是未完成运行，`fail` 是 staging、运行、trap、身份或日志检查失败，`pass` 是该 case 已完成全套检查。`binary`、`binary_sha256` 仍指构建目录中的原始 BIN；新增末尾列 `staged_binary`、`staged_binary_sha256` 分别是实际运行副本的路径和 SHA。旧结果没有这两列，旧报告仍描述当时的原始运行，不可把新版 staging 行为倒填为历史事实；按表头读取的旧消费者应兼容缺列与新增状态值。部分 `pass` 行绝不代表全部完成；以完成的 `summary.md` 和全部覆盖行共同核验。`commands.csv` 的 `status` 为进程返回码或 timeout。每次调用的记录只说明所选库及所选函数，不可外推到其他库。

主机单测，无需 NEMU、目标库或 oracle：

```sh
python3 -B single/libmvec/tests/host/test_gate.py
```

## 断言和边界

- 每个函数两 LMUL 的 array 与 Nx 各自完整比较冻结参考；该参考由 MPFR512 定向上下界转换到目标格式 RNE 后得到，头文件仅保存输入和目标格式参考位，未保存高精度真值。历史“ULP”指标是有序编码步距，并非严格的数学真值误差。C 内实际递增已比较数量，并与冻结头数量比较；脚本独立要求每个函数及 LMUL 的数量、原限值与语义行，拒绝缺行、重复、错误或空跑。sin/cos 共享 oracle，但两个函数每个 LMUL 各比较全量。Nx 最后一个向量重复末输入填满、仅计算真实 oracle lane。
- 历史比较器并不对每个结果都严格区分特殊值：当参考为最大有限值而输出为同号无穷时，编码步距可为 1；array 一元比较对零与次正规等边界也有相同限制。因此 pass 仅表示按原规则通过，不证明特殊值分类或五种 frm 的真实数学误差。
- 复用所有历史 array 调用及其语义（零长度、special/mixed、flags/errno、tail/canary 与 inplace；具体覆盖因函数而异）；pow 115422 normal 样本区段不允许 scalar fallback；unary Nx 逐实际 scalar fallback 参数检查 ordinary 禁止回退。
- Nx 汇编 caller 对完整 VLMAX 装载参数，测试 incoming vl=0、1、VLMAX−1、VLMAX，tu/mu；检查输出、广播及轮转 mixed special 的值/fflags/errno，v1–v7 和 v24–v31 的每字节哨兵，返回 vstart=0 和 frm=RNE。采集返回 vl/vtype，但不要求保持。超出 vlenb=2048 或 m2 lanes=512 明确失败。
- TLS 的 tp、errno offset、anchor 及 flags/errno sticky 有动态检查。trig 特殊 normal 范围沿用原 1 ULP，tiny/cold 位相同；其余 special 按原规则严格比较。Nx MPFR NaN 采用类别比较，pow 的 zero/inf 按位比较。
- 每个输出必须 good trap、无 BAD/ERROR、完成行失败为零、所需数量和语义行完整、ELF/BIN/库未变化才 pass；这是动态样本证据，不是全 36 ABI、全部 VLEN 或数学全域证明。
- 当前没有安全分片，也不保证每进程约 50M 指令。禁止使用原工作树未验证的分片结果替代此门禁；如果完整单进程超时或超指令预算，应报告未完成而非标 pass。现有 array 测试摘要中的语义次数来自未分片原循环；不能将该摘要逻辑用于局部分片。
- 本正确性门禁使用 RNE 实验设置；outer `entry_rne` 的五个合法 frm（0–4）均验证进入 RNE 并恢复。该设置不是依据实验推导出的产品接口契约。下述直接观察器独立于此门禁，不宣称非 RNE 精度。

## 五种 frm 的直接入口行为观察

`script/accuracy/frm_observe.py` 可将相同探针分别链接到原版和候选数学库，对单个函数的 Nx、array 和两个 LMUL 在五种合法 `frm` 下比较有限样本的输入、每 lane 输出位模式以及调用后的 `frm`、`fflags`、`errno`。每 case 预置 `fflags=0` 或 `NV|DZ`，同时预置 `errno=0` 或 89；检查 sticky 标志未丢失。样本包括普通值、边界、零、次正规、无穷、qNaN/sNaN 和轮转混合，输入组不是 MPFR oracle。`frm=0..4` 对应 RNE、RTZ、RDN、RUP、RMM。

Nx caller 只使用 **incoming vl=VLMAX**；虽然汇编也采样了 `vl/vtype/vstart` 和被调用者保存寄存器，直接观察器**没有检查或比较**这些采样，不能声称已证明 PCS。array 测试长度为 1、VLMAX−1、VLMAX、VLMAX+1 和 2×VLMAX+1（某些 VLMAX 下长度可能重复），Nx 每次一个 VLMAX。程序输出 `FRM_CONFIG` 记录实际 `vlenb`，主机逐键核对配置、长度、预期输入和所有 lane，并拒绝重复或缺失记录、单侧失败。主机单测可不运行 NEMU：

```sh
python3 -B single/libmvec/tests/host/test_frm_observe.py
```

构建示例；去掉 `--build-only` 并显式指定 `--nemu` 后才会执行目标，事先须获得仿真授权。每次必须给两个互不包含的全新目录，测试工作目录和临时文件须带日期时间，且路径不含 `build`：

```sh
python3 -B single/libmvec/script/accuracy/frm_observe.py run \
  --function log --archive /绝对路径/libnewmath.a \
  --gcc /工具链/bin/riscv64-unknown-linux-gnu-gcc \
  --scalar-libm /绝对路径/libm-2.44.a \
  --build-dir /绝对路径/build/direct-frm-编译 \
  --run-dir /绝对路径/runs/20261114-180000-direct-frm \
  --build-only
```

分别构建并获准运行之后，以各自**原始**运行记录执行离线对比（示例为原版对候选）：

```sh
python3 -B single/libmvec/script/accuracy/frm_observe.py compare --function log \
  --before /原运行/logs/nemu.stdout --before-stderr /原运行/logs/nemu.stderr \
  --before-commands /原运行/commands.csv \
  --after /候选运行/logs/nemu.stdout --after-stderr /候选运行/logs/nemu.stderr \
  --after-commands /候选运行/commands.csv --output /新运行目录/比较.csv
```

比较要求两侧 `commands.csv` 唯一 `nemu` 行 rc=0 且对应所给日志路径，NEMU stdout 有真实格式的 GOOD TRAP，两个流均无已知 bad marker，目标 UART 记录来自 stderr。旧版驱动未输出 `FRM_CONFIG`，可人工确认外部条件后显式增加 `--legacy-vlenb 16`（或其他实际值）：这是旧记录缺失元数据的外部假设，不是探针自身测得的证据。不要覆盖旧日志或声称旧版采用新版脚本执行。

`identities.csv` 保存所用源码、runtime/linker、编译及运行依赖、ELF、BIN 和 staging BIN 的 SHA，`commands.csv` 在命令执行及失败后保留，stdout/stderr 分开。观察 CSV 按函数、接口、LMUL 和 frm 分组：`cases` 为调用数，`state_diff` 为 `frm/fflags/errno` 有差异的 case 数，`value_count` 为 lane 数，`value_diff` 为输出位模式有差异的 lane 数；对比进程正常完成不等于差异为零。它只提供有限样本的前后行为观察，**不证明数学精度、全部输入一致、其他 VLEN 或 ABI**。
