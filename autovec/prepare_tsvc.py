"""Split upstream TSVC into one kernel and one driver per executable."""
from pathlib import Path
import re

FUNCTION = re.compile(r'^(?:real_t|void|int)\s+(\w+)\([^;]*?\)\s*\{.*?^}', re.M | re.S)


def prepare(source: Path, output: Path) -> list[dict]:
    source = source / 'src' if (source / 'src').is_dir() else source
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    text = (source / 'tsvc.c').read_text()
    functions = {m.group(1): m for m in FUNCTION.finditer(text)}
    main = functions['main'].group()
    calls = re.findall(r'^\s*time_function\(&(\w+), (.+)\);$', main, re.M)
    kernels = {name for name, m in functions.items() if re.match(r'real_t \w+\(struct args_t', m.group())}
    if len(calls) != 151 or len({name for name, _ in calls}) != len(calls) or kernels != {n for n, _ in calls}:
        raise ValueError('Upstream TSVC entry points changed; review splitter')
    include = '#include "av_runtime.h"\n#include "common.h"\n#include "array_defs.h"\n'
    globals_text = text[text.index('// array definitions'):min(m.start() for m in functions.values())]
    (output / 'arrays.c').write_text(include + globals_text)
    for name in ('common.h', 'array_defs.h', 'common.c', 'dummy.c'):
        adapted = (source / name).read_text()
        adapted = re.sub(r'^#include <[^>]+>\s*$', '', adapted, flags=re.M)
        if name == 'common.h':
            adapted = adapted.replace('#define iterations 100000', '#define iterations 32')
            adapted = adapted.replace('#define LEN_1D 32000', '#define LEN_1D 320')
            adapted = adapted.replace('#define LEN_2D 256', '#define LEN_2D 32')
        (output / name).write_text('#include "av_runtime.h"\n' + adapted)
    helpers = set(functions) - kernels - {'main', 'time_function'}
    records = []
    for name, argument in calls:
        selected = {name}
        pending = [name]
        while pending:
            body = functions[pending.pop()].group()
            for helper in helpers - selected:
                if re.search(r'\b' + re.escape(helper) + r'\s*\(', body):
                    selected.add(helper)
                    pending.append(helper)
        # Preserve upstream definition order and source line attribution.
        body = include
        for fn, match in sorted(functions.items(), key=lambda item: item[1].start()):
            if fn in selected:
                line = text[:match.start()].count('\n') + 1
                body += f'\n#line {line} "tsvc.c"\n{match.group()}\n'
        kernel = output / f'{name}.c'
        kernel.write_text(body)
        driver = output / f'{name}_main.c'
        driver.write_text(include + f'''
real_t {name}(struct args_t *);
int main(void) {{
    int n1 = 1, n3 = 1;
    int *ip;
    real_t s1, s2;
    init(&ip, &s1, &s2);
    struct args_t args = {{.arg_info = {argument}}};
    double result = {name}(&args);
    unsigned long long cycles = (unsigned long long)(args.t2.tv_usec - args.t1.tv_usec);
    printf("AV cycles=%llu checksum=%.9g\\n", cycles, result);
    return EXIT_SUCCESS;
}}
''')
        records.append({'suite': 'tsvc', 'name': name,
                        'sources': [str(kernel), str(driver)] + [str(output / f) for f in ('arrays.c', 'common.c', 'dummy.c')],
                        'include_dirs': [str(output)], 'defines': [],
                        'kernel_sources': [kernel.name], 'kernel_functions': [name],
                        'helper_functions': sorted(selected - {name}), 'upstream_argument': argument})
    return records
