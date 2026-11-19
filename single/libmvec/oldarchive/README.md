# 历史 RVV 数学候选归档

这里保存从旧 `math/` 迁移的 log、exp、pow、sin、cos、logf、expf、sinf、cosf、powf 候选。每个函数目录只保存本函数源码；`common/` 单份保存共享入口模板、声明、三角函数表/核和 expf/powf 共用表。算法和表字节保持原样，版权注释及 LGPL-2.1-or-later 许可完整保留，见 [LICENSES.md](<LICENSES.md>) 和 [COPYING.LIB](<COPYING.LIB>)。

本归档不进入默认性能构建链。库入口仍为旧 array m1/m2 与 unmasked fixed/Nx 符号；不存在 masked 入口。m4/m8 扩展源码保留为实验材料，当前构建器仍沿用原 m1/m2 编译单元集合，不把它们自动加入 archive。当前文件存在不表示任意候选通过完整数值、ABI 或性能验收。

显式构建使用 `../script/build_oldarchive.py`，要求 Python 3 和含 GCC、binutils 的 RISC-V 工具链前缀；输入源码必须显式 `--source` 指向本目录，输出目录必须不存在。最小命令如下，所有变量由调用者给出绝对路径；运行 cwd 和 TMP 应在带日期时间且不含 build 的独立目录中，日志与对象分开保存：

```sh
(cd "$RUN" && TMPDIR="$RUN/tmp" TMP="$RUN/tmp" TEMP="$RUN/tmp" \
  python3 -B "$SRC/script/build_oldarchive.py" \
  --prefix "$TOOLCHAIN" --source "$SRC/oldarchive" \
  --functions log --variant baseline --output "$OUTPUT" --log-dir "$RUN/archive-logs")
```

`--help` 安全显示全部选项。输出包含 `libnewmath.a`、原有成员名的对象、反汇编/ELF 属性、构建脚本及所选编译单元的本地 include 闭包和许可证快照，以及命令/依赖/工具/源码身份记录。编译只读取快照，编译前后复核源件、快照、依赖和工具 SHA，拒绝已有输出、外部本地 include 或缺失源码/许可。脚本只构建，不运行模拟器。产物可用显式 archive 参数交给性能和验证入口；LTO archive 需 GCC 驱动最终链接，不能以空中间反汇编推断最终机器码。

变体保持原规则：baseline、cold、lifetime 与 log 的 lifetime-hybrid，sin 可叠加 uniform；也支持 `--select 函数=变体`。这些选择是历史实验因素，不承诺精度或性能。gpr2vr/fpr2vr 成本参数不会保证 intrinsic 常量广播消失，收益需核对最终机器码及同条件测量。

sin/cos 的历史 uniform 候选在核入口读取 frm，非 RNE 时临时切为 RNE，核与冷路径结束后恢复；它绕过原 magic 常数归约对 RNE 的依赖，但不是有向正确舍入实现，不改变 fflags 清除策略。CSR 周围浮点重排、全部非 RNE 精度与完整接口语义仍需独立验证。不能把有限样本观察推广为五模式正确。

验证用途、显式 oracle 和分组规则见 [tests/README.md](<../tests/README.md>)。本次只改变布局与依赖路径，不改写旧测量或旧 manifest 的身份；历史审计信息属于原提交，当前复现应记录本次构建的新来源身份。
