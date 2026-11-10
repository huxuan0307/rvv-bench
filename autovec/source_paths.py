"""定位可迁移的基准输入；优先显式环境，再本地依赖，最后旧同级布局。"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def default_sources():
    configured = os.environ.get('RVV_BENCH_SOURCES')
    if configured:
        return Path(configured).expanduser().resolve()
    local = ROOT / '.dsh/deps/benchmarks'
    if local.is_dir():
        return local
    return ROOT.parent / 'compiler-experiment/benchmarks'
