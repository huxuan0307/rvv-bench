.POSIX:

include ../../config.mk

INSNS = $(shell cat insns.list)

# QUICK=1: fewer guest insns for NEMU/smoke (default keeps WARMUP/LOOP/RUNS)
ifdef QUICK
BENCH_DEFS = -DWARMUP=64 -DUNROLL=8 -DLOOP=16 -DRUNS=8
endif

# ENABLE_MASK=1: also emit v0.t variants (default: unmasked only)
ifdef ENABLE_MASK
M4_DEFS = -DENABLE_MASK
endif

all: rvv

rvv: gen.S main.c config.h ../../config.mk
	m4 ${M4_DEFS} gen.S > main.S
	${CC} ${CFLAGS} ${BENCH_DEFS} -fno-tree-vectorize -Wl,--relax main.S main.c -o $@ \
		-T ../../thirdparty/link.ld \
		../../thirdparty/am-riscv64-xs.a \
		../../thirdparty/klib-riscv64-xs.a
	rm main.S

build/rvv-%.elf: gen.S main.c config.h ../../config.mk
	mkdir -p build
	m4 ${M4_DEFS} -DBENCH_INSN=$* gen.S > build/$*.S
	${CC} ${CFLAGS} ${BENCH_DEFS} -fno-tree-vectorize -Wl,--relax build/$*.S main.c -o $@ \
		-T ../../thirdparty/link.ld \
		../../thirdparty/am-riscv64-xs.a \
		../../thirdparty/klib-riscv64-xs.a
	${OBJCOPY} -S --set-section-flags .bss=alloc,contents -O binary $@ $(@:.elf=.bin)

insn: $(INSNS:%=build/rvv-%.elf)

run-%: build/rvv-%.elf
	../../run.sh ./build/rvv-$*.elf

clean:
	rm -f rvv main.S
	rm -rf build

run: rvv
	../../run.sh ./rvv
