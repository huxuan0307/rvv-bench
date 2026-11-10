.DEFAULT_GOAL := all
PYTHON ?= python3
GCC ?= /nfs/share/riscv-toolchain-gcc16.1.0-260715-nightly/bin/riscv64-unknown-linux-gnu-gcc
SOURCES ?= $(shell $(PYTHON) -B -c 'from source_paths import default_sources; print(default_sources())')
OPT ?= O3
JOBS ?= 8
NEMU ?= /nfs/home/huxuan/opt/bin/riscv64-nemu-interpreter
CASE ?= s000
GSIM_JOBS ?= 32
GSIM_TIMEOUT ?= 0
.PHONY: gsim
gsim:
	$(PYTHON) run_gsim.py --manifest build/$(OPT)/manifest.json --jobs $(GSIM_JOBS) --timeout $(GSIM_TIMEOUT)
.PHONY: all run report compare tsvc tsvc-one run-tsvc-one
tsvc:
	$(PYTHON) build.py --sources $(SOURCES) --gcc $(GCC) --opt $(OPT) --jobs $(JOBS) --suite tsvc
tsvc-one:
	$(PYTHON) build.py --sources $(SOURCES) --gcc $(GCC) --opt $(OPT) --suite tsvc --case $(CASE)
run-tsvc-one:
	$(PYTHON) run.py --build build/$(OPT)/selected/tsvc/$(CASE) --nemu $(NEMU) --jobs 1
all:
	$(PYTHON) build.py --sources $(SOURCES) --gcc $(GCC) --opt $(OPT) --jobs $(JOBS)
run:
	$(PYTHON) run.py --build build/$(OPT) --nemu $(NEMU) --jobs $(JOBS)
compare:
	$(PYTHON) compare.py --vector build/$(OPT) --scalar build/$(OPT)-scalar
report: compare
	$(PYTHON) report.py --build build/$(OPT) --output reports/gcc-default-$(OPT).md
