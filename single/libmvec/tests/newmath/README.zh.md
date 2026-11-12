# 七新算法 array / 14 Nx 正确性与向量PCS门禁

## 调用

```sh
python3 -B single/libmvec/tests/newmath/gate.py \
  --new-archive /绝对路径/libnewmath.a \
  --gcc /工具链/bin/riscv64-unknown-linux-gnu-gcc \
  --scalar-libm /工具链/lib/libm-2.44.a \
  --oracle-dir /绝对路径/冻结oracle目录 \
  --build-only \
  --output /绝对路径/结果目录
```

仅测exp可加 `--functions exp`。无array符号的旧库PCS门禁加 `--nx-only --abi-only --family old-original` 或 `--family old-nofrm`。ABI-only跳过新算法全域MPFR质量裁决，以该族incoming=VLMAX的输出/flags/errno为baseline，仅验证改变incoming vl不改变结果和PCS状态；这不是独立数学oracle，旧族普通域MPFR门禁由full harness另行负责。`--build-only` 只构建，结果不会标pass。脚本不构建/修改数学库、不修改harness、不调用gsim；执行时去掉 `--build-only`，并用 `--nemu /绝对路径/riscv64-nemu-interpreter` 显式选择执行器。每条目标编译和链接命令显式 `-march=rva23u64`。

## 源与依赖

- 当前目录nx_gate.c：严格translation unit，`-fno-fast-math -frounding-math -ffp-contract=off -fno-builtin -fno-tree-vectorize -fno-tree-slp-vectorize`。
- nx_probe.S：四个scalar ABI汇编caller，对e32/e64、m1/m2调用真正 `_ZGV1Nxv_* / _ZGV2Nxv_*` 以及pow `vv` 符号；不需要C向量类型声明，不依赖newlib.h内部类型。
- `unit/` 保存原 array 测试与声明头，语义和 1ULP 容差保持不变。`--oracle-dir` 必须是平铺目录，包含选中函数对应的 `exp-unit.dat`、`expf-unit.dat`、`logf-unit.dat`、`log-unit.dat`、`trigvec-unit-data.dat`、`powvec_unit_data.dat`。每个 `.dat` 是原冻结 MPFR512 头文件的 gzip 压缩数据；gate 在 `--output/oracles/` 按原字节物化 `.h`，记录压缩数据和头文件两份 hash，再编译。`--output` 不得位于 `.dsh/tasks/`，应使用仓库 build 目录或 mktemp 目录。
- 本机已准备好的 oracle 数据目录：`/nfs/home/huxuan/repos/riscv-gnu-toolchain/.dsh/tasks/20261112-0126-newlib-full/data/correctness-oracles`，共六个 `.dat`，约 11 MB；同目录 `manifest.json` 记录原归档头文件位置、原始及压缩 SHA256。exp/expf/logf 来自归档 batch03/data，trig/pow 来自 batch04/data，log 来自 batch05/data/unit_data.h。此路径仅作为显式 CLI 输入示例，不是脚本默认依赖。
- runtime 使用仓库的 `single/veclibm-runtime.c`，entry_rne、TLS anchor 和 TLS 脚本使用 `single/libmvec/` 的源码，AM/klib 与主 linker 脚本使用仓库 `thirdparty/`。GCC 和标量 libm 必须显式传入；配套 binutils、libc/libgcc 路径由 GCC 查询，不读取旧 manifest。工具与依赖 hash 记录在结果中；外部预编译依赖的历史 march 不作额外声明。
- 输出包含commands.json（每条命令和rc）、每步独立stdout/stderr、最终ELF/BIN及hash、oracle/source身份、NEMU日志与结果JSON。

## 覆盖

1. 每fn每m1/m2全量历史MPFR512语料；Nx输出完整VLMAX，末块重复最后oracle输入填满，不让填充lane读取越界。ordinary域不允许scalar fallback，pow沿用115422条normal-domain边界。
2. 独立汇编caller先载入完整VLMAX实参，随后分别设incoming vl=0、1、VLMAX−1、VLMAX及tu/mu，再调用Nx。必须得到全部VLMAX结果，不能偷用入站vl。
3. v1-v7、v24-v31共15个向量callee-saved寄存器，各寄存器所有vlenb字节初始化不同的常量哨兵；广播历史special、轮转mixed special与normal sticky调用后全字节检查，因此覆盖normal/cold。最大支持vlenb=2048且m2 lanes≤512；超出显式报unsupported并失败，不默默跳过。
4. 返回vstart必须0、frm必须RNE。返回vl/vtype仅采样，不要求保持（ABI允许clobber）。入站tu/mu是状态扰动，不把tail-undisturbed误当Nx契约。
5. historical special包含signed zero、subnormal、normal边界、Inf、qNaN/sNaN、阈值边界；对真实scalar逐lane计算，检查值、累积fflags与最后errno。special返回值按原unit规则：trig正常范围容许原1ULP，tiny/cold位相同；其他函数special严格位相同。NaN在MPFR oracle部分按原pow测试类别规则处理，special语义部分仍严格位相同。
6. safeTLS检查tp==__tls_base、anchor地址和值、最终ELF提取errno TLS offset；非零errno写后scalar/GOT正常访问必须不变；sticky flags和errno不清除。
7. 复用同一entry_rne源码构建探针，五个合法frm=0..4分别确认进入RNE并恢复原mode。数学核直接调用只在RNE运行；不宣称direct非RNE精度得到验证。frm5/6保留值及动态7不是本测试合法调用mode。
8. array部分直接复用冻结unit及MPFR oracle、tail长度/canary、inplace、special flags errno，链接新archive，未更改容差。

## 范围限制

- 这是七函数×m1/m2的**14个unmasked Nx**门禁，不是full36ABI；不涵盖masked入口、其他函数、所有可能VLEN、所有特殊值或数学全域证明。
- strict TU指测试caller不启用fast-math；并不擅自更改数学核获批的编译选项。
- 返回寄存器哨兵验证是动态样本证据，不是完整静态ABI证明。普通oracle允许原1ULP，不能称正确舍入。
- five-mode测试是外层环境管理验证，不是数学核在五模式下精度测试；不支持的直接非RNE调用明确保留为不支持。
- 结果只在NEMU最终good trap、完成行failed=0、没有ERROR、BIN未变化时标pass。任一case失败立即停止后续，不通过重复重试或放宽oracle绕过。

## 只读审计

```sh
python3 -B single/libmvec/tests/newmath/audit_receipts.py \
  --gate-dir /绝对路径/结果目录 \
  --output /绝对路径/新receipt.json
```

`--gate-dir` 可以重复。审计只核验传入结果中记录的 hash、命令和日志，不启动仿真，也不使用历史任务的隐式初始身份记录。
