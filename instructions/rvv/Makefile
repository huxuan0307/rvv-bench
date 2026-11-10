.POSIX:

include ../../config.mk

NEMU ?= /nfs/home/huxuan/opt/bin/riscv64-nemu-interpreter
LATENCY_CASE ?= ./build/latency.bin
LATENCY_LOG ?= ./build/latency.log

INSNS = $(shell cat insns.list)
# Derive forms from the same m4/CPP output that is compiled, so ISA guards
# follow the active -march instead of producing empty executables.
INSN_FORMS = $(shell m4 ${M4_DEFS} gen.S | ${CC} ${CFLAGS} -E -x assembler-with-cpp - | \
	awk '/^[[:space:]]*\.string "/ { sub(/^.*\.string "/, "", $$0); sub(/ .*/, "", $$0); print }' | sort -u)

# QUICK=1: fewer guest insns for NEMU/smoke (default keeps WARMUP/LOOP/RUNS)
ifdef QUICK
BENCH_DEFS = -DWARMUP=16 -DUNROLL=8 -DLOOP=16 -DRUNS=2
endif

# Latency runs use the same knobs as the throughput driver, but keep their
# defaults and sample count independent from ordinary benchmark builds.
LATENCY_WARMUP ?= $(if $(WARMUP),$(WARMUP),$(if $(QUICK),16,1000))
LATENCY_UNROLL ?= $(if $(UNROLL),$(UNROLL),8)
LATENCY_LOOP ?= $(if $(LOOP),$(LOOP),$(if $(QUICK),16,64))
LATENCY_RUNS ?= $(if $(RUNS),$(RUNS),$(if $(QUICK),2,8))
LATENCY_DEFS = -DWARMUP=$(LATENCY_WARMUP) -DUNROLL=$(LATENCY_UNROLL) \
	-DLOOP=$(LATENCY_LOOP) -DLATENCY_RUNS=$(LATENCY_RUNS)

# Emit both unmasked and v0.t variants by default; ENABLE_MASK=0 disables v0.t.
ENABLE_MASK ?= 1
ifeq ($(ENABLE_MASK),1)
M4_DEFS = -DENABLE_MASK
endif

all: rvv

rvv: gen.S main.c config.h Makefile ../../config.mk
	m4 ${M4_DEFS} gen.S > main.S
	${CC} ${CFLAGS} ${BENCH_DEFS} -fno-tree-vectorize -Wl,--relax main.S main.c -o $@ \
		-T ../../thirdparty/link.ld \
		../../thirdparty/am-riscv64-xs.a \
		../../thirdparty/klib-riscv64-xs.a
	rm main.S

build/rvv-%.elf: gen.S main.c config.h Makefile ../../config.mk
	mkdir -p build
	m4 ${M4_DEFS} -DBENCH_INSN=$(if $(filter vcpop.m,$*),vcpopm,$(if $(filter vcpop.v,$*),vcpopv,$(firstword $(subst ., ,$*)))) $(if $(findstring .,$*),-DBENCH_CASE=$*) gen.S > build/$*.S
	$(if $(findstring .,$*),@grep -Fq '.string "$* ' build/$*.S || { echo 'No benchmark for $*' >&2; exit 1; })
	${CC} ${CFLAGS} ${BENCH_DEFS} -fno-tree-vectorize -Wl,--relax -I. build/$*.S main.c -o $@ \
		-T ../../thirdparty/link.ld \
		../../thirdparty/am-riscv64-xs.a \
		../../thirdparty/klib-riscv64-xs.a
	${OBJCOPY} -S --set-section-flags .bss=alloc,contents -O binary $@ $(@:.elf=.bin)
	${DUMP} -d $@ > $(@:.elf=.dump)

insn: $(INSNS:%=build/rvv-%.elf)

insn-forms: $(INSN_FORMS:%=build/rvv-%.elf)

run-%: build/rvv-%.elf
	../../run.sh ./build/rvv-$*.elf

# RAW latency benchmarks are generated independently from the throughput
# driver.  The form census still comes from the active gen.S/CPP output.
build/latency.S: gen.S latency_gen.py config.h Makefile ../../config.mk
	mkdir -p build
	m4 ${M4_DEFS} gen.S | ${CC} ${CFLAGS} -E -x assembler-with-cpp - | \
		awk '/^[[:space:]]*\.string "/ { sub(/^.*\.string "/, "", $$0); sub(/ .*/, "", $$0); print }' | \
		sort -u | python3 latency_gen.py > $@

build/latency.elf: build/latency.S latency_main.c config.h Makefile ../../config.mk
	${CC} ${CFLAGS} ${BENCH_DEFS} ${LATENCY_DEFS} -fno-tree-vectorize -Wl,--relax build/latency.S latency_main.c -o $@ \
		-T ../../thirdparty/link.ld \
		../../thirdparty/am-riscv64-xs.a \
		../../thirdparty/klib-riscv64-xs.a
	${OBJCOPY} -S --set-section-flags .bss=alloc,contents -O binary $@ $(@:.elf=.bin)

latency: build/latency.elf

run-latency: build/latency.elf
	${NEMU} -b ${LATENCY_CASE} > ${LATENCY_LOG} 2>&1

clean:
	rm -f rvv main.S
	rm -rf build

run: rvv
	../../run.sh ./rvv

# Explicit P1 workload matrix with per-sample correctness and coverage.
include p1.mk
