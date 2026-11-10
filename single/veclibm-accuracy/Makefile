# Standalone AM build of the actual upstream veclibm precision tests.
.DEFAULT_GOAL := all
PYTHON ?= python3
GCC ?= /nfs/share/riscv-toolchain-gcc16.1.0-260715-nightly/bin/riscv64-unknown-linux-gnu-gcc
PROFILE ?= quick
BUILD ?= build
POINTS ?= $(if $(filter full,$(PROFILE)),100000,16)
GRID_POINTS ?= $(if $(filter full,$(PROFILE)),100000,4)
REPEATS ?= 1
JOBS ?= 8
CASE ?=
# Explicit POINTS/GRID_POINTS select a uniform cap unless CONFIG is also set.
CONFIG ?= $(if $(filter full,$(PROFILE)),,$(if $(filter command line,$(origin POINTS) $(origin GRID_POINTS)),,quick.json))
NEMU ?= /nfs/home/huxuan/opt/bin/riscv64-nemu-interpreter
RUN_JOBS ?= 4
RUN_OUTPUT ?= $(BUILD)/run-$(shell date +%Y%m%d-%H%M%S)
.PHONY: all quick full run test list
all:
	$(PYTHON) build.py --gcc $(GCC) --build $(BUILD) --points $(POINTS) --grid-points $(GRID_POINTS) --repeats $(REPEATS) --jobs $(JOBS) $(if $(CASE),--case $(CASE)) $(if $(CONFIG),--config $(CONFIG))
quick:
	$(MAKE) all PROFILE=quick
full:
	$(MAKE) all PROFILE=full BUILD=build-full
run: all
	$(PYTHON) ../run_veclibm.py --manifest $(BUILD)/manifest.json --nemu $(NEMU) --jobs $(RUN_JOBS) --max-instr 50000000 --range-max 15000000 --output $(RUN_OUTPUT)
test:
	$(PYTHON) -B test_adapter.py
list:
	$(PYTHON) build.py --list
