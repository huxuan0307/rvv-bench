# 工作区内运行环境与Git更新

## 当前部署

本地运行副本位于工作区的`.dsh/rvv-bench`。该副本拥有完整独立`.git`、工作树和子模块，不能使用依赖旧本地仓库的worktree、objects alternates或符号链接。后续只在此副本操作，不访问旧rvv-bench目录。

本次工作区为`/nfs/home/huxuan/repos/riscv-gnu-toolchain`，进入方式：

```sh
cd /nfs/home/huxuan/repos/riscv-gnu-toolchain/.dsh/rvv-bench
pwd
git status --short
git remote -v
```

外部工具链、NEMU、gsim、refmodel仍使用共享安装目录，可由各脚本参数覆盖；它们不是旧checkout依赖。AM/klib静态支持库、链接脚本和适配源码属于本仓。

## 远程同步

- origin：`git@github.com:huxuan0307/rvv-bench.git`
- upstream：`git@github.com:camel-cdr/rvv-bench.git`

仅设置remote或迁移本地commit不代表已经push。推送需用户确认，SSH认证不可用时先配置访问权限，不自动改写历史或回退旧目录。

```sh
git status --short
git fetch origin
git log --oneline --left-right HEAD...origin/main
# 仅在工作树干净且可以快进时更新，拒绝隐式merge/rebase。
git merge --ff-only origin/main
git submodule update --init --recursive

# 如需查看原项目更新，先fetch/审阅，不自动覆盖本地适配。
git fetch upstream
git log --oneline HEAD..upstream/main
```

如果本地有未推送提交、与origin分叉，`--ff-only`失败是保护行为：先审查差异，再决定merge/rebase或push，不使用reset --hard丢弃本地工作。

## autovec输入依赖

TSVC_2、PolyBenchC、llvm-test-suite输入checkout放在`.dsh/deps/benchmarks`，各自保留Git历史、origin和许可证。源码定位顺序为显式`--sources`/Make `SOURCES=`、环境变量`RVV_BENCH_SOURCES`、本仓本地依赖，最后兼容旧同级compiler-experiment布局。正常迁移后不需最后一项。

```sh
python3 -B -m unittest discover -s autovec -p 'test_*.py'
python3 -B autovec/build.py --suite tsvc --case s000 \
  --output-root .dsh/runs/tsvc-smoke/build \
  --generated-root .dsh/runs/tsvc-smoke/generated
```

各输入版本应保持固定；如确需更新，分别fetch并核对SHA、源码适配和golden值，不盲目pull到最新。新机器可按[autovec说明](<../autovec/README.md>)指定已准备好的源码根。

## 脚本入口与验证

- [libmvec完整使用说明](<../single/libmvec/README.zh.md>)：构建、NEMU、非static gsim、严格验收、部分CSV回收、性能汇总、证据打包。
- [veclibm使用说明](<../single/README.md>)：25函数性能smoke、141个上游精度/诊断程序及gsim批量运行。
- [autovec使用说明](<../autovec/README.md>)：TSVC/PolyBench/LLVM生成、构建及运行。

迁移验证至少包括新路径主机测试、新构建、镜像审计及NEMU小用例；昂贵RTL全参数运行另行安排。历史通过记录不能替代新环境验证。

## 历史证据与临时文件

旧的build、日志、任务目录已按原字节保留在本地忽略路径，用于追溯，不提交Git。历史manifest/run-metadata内的绝对路径是**当时的身份来源**，不能直接在新目录执行它们的runner/collector：那会访问旧位置。不要把这些路径批量改写后冒充原始证据。

后续构建必须新建输出目录，生成指向新checkout的manifest；新运行器对新manifest建新run目录。阅读历史CSV/JSON可以使用普通文件读取，不通过历史路径重放。只有需要离线回收时，libmvec recover工具可用迁移后的manifest文件路径，且不会核验/访问历史BIN，仅按其配置解析新指定日志。

`.gitignore`排除`.dsh`、build、缓存、logs、开发图片和本地临时数据。提交时按显式源码清单分批stage，检查`git diff --cached --stat`与`git diff --cached --check`，不使用`git add .`。现有已跟踪的历史报告不因ignore而删除。
