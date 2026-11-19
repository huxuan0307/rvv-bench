.POSIX:
.DEFAULT_GOAL := all

include ../config.mk

VECLIBM_FUNCS = exp exp2 expm1 log log10 log2 log1p sqrt cbrt \
                sin cos tan asin acos atan \
                sinh cosh tanh asinh acosh atanh \
                erf erfc tgamma lgamma

# Optional uniform-size build. Default `all` uses calibrated veclibm-quick.
# Restore the historical array size with VECLIBM_N=131072 veclibm-all.
VECLIBM_N ?= 4096
VECLIBM_REPEATS ?= 3
NEMU ?= /nfs/home/huxuan/opt/bin/riscv64-nemu-interpreter
VECLIBM_RUN_DIR ?= build/veclibm-runs/$(shell date +%Y%m%d-%H%M%S)
VECLIBM_QUICK_DIR ?= build/veclibm-quick

# Include repeats in artifact names so changed parameters never reuse old ELFs.
VECLIBM_ELFS  = $(VECLIBM_FUNCS:%=build/veclibm-%-n$(VECLIBM_N)-r$(VECLIBM_REPEATS).elf)
EXECS         = build/uarch.elf build/veclibm-n$(VECLIBM_N)-r$(VECLIBM_REPEATS).elf ${VECLIBM_ELFS}
DASMS         = $(EXECS:.elf=.dasm)
BINS          = $(EXECS:.elf=.bin)

# veclibm static library
VECLIBM_SRC_DIR = ../thirdparty/veclibm/src
VECLIBM_INC_DIR = ../thirdparty/veclibm/include
VECLIBM_SRC     = $(wildcard $(VECLIBM_SRC_DIR)/*.c)
VECLIBM_OBJ     = $(VECLIBM_SRC:$(VECLIBM_SRC_DIR)/%.c=build/veclibm-obj/%.o)

VECLIBM_CFLAGS = -march=rv64gcv -mabi=lp64d -mcmodel=medany -O2 -g ${WARN} \
                  -I $(VECLIBM_INC_DIR) -Wno-unused -Wno-maybe-uninitialized \
                  -fno-builtin -ffreestanding -fno-pie -fno-stack-protector
VECLIBM_LINK = -nostdlib -static -no-pie -Wl,--wrap=_trm_init -Wl,--build-id=none \
               -T ../autovec/tls.ld -T ../thirdparty/link.ld \
               build/veclibm.a build/veclibm-runtime.o ../thirdparty/am-riscv64-xs.a \
               -Wl,--start-group ../thirdparty/klib-riscv64-xs.a -lm -lc -lgcc -Wl,--end-group

all: build/uarch.elf veclibm-quick

${EXECS}: ../config.mk

# --- uarch ---
build/uarch.elf: uarch.S uarch.c
	@mkdir -p $(@D)
	@echo "  CC+LD $@"
	@${CC} ${CFLAGS} -o $@ uarch.c uarch.S \
		-T ../thirdparty/link.ld \
		../thirdparty/am-riscv64-xs.a \
		../thirdparty/klib-riscv64-xs.a
	@echo "  DUMP  $(@:.elf=.dasm)"
	@${DUMP} -S -d $@ > $(@:.elf=.dasm)
	@echo "  OBJCP $(@:.elf=.bin)"
	@${OBJCOPY} -S --set-section-flags .bss=alloc,contents -O binary $@ $(@:.elf=.bin)

# --- veclibm static library (86 → 1 archive) ---
build/veclibm.a: $(VECLIBM_OBJ)
	@echo "  AR    $@"
	@${TOOL_CHAIN_PREFIX}ar rcs $@ $^

build/veclibm-obj/%.o: $(VECLIBM_SRC_DIR)/%.c $(wildcard $(VECLIBM_INC_DIR)/*.h) Makefile ../config.mk
	@mkdir -p $(@D)
	@echo "  CC    $@"
	@${CC} $(VECLIBM_CFLAGS) -c -o $@ $<

build/veclibm-runtime.o: common/am_runtime.c ../autovec/av_runtime.c ../autovec/av_runtime.h Makefile ../config.mk
	@mkdir -p $(@D)
	@${CC} $(VECLIBM_CFLAGS) -fno-tree-vectorize -c $< -o $@

# --- fixed-size full / per-function programs, linked to real static libm ---
build/veclibm-n$(VECLIBM_N)-r$(VECLIBM_REPEATS).elf: veclibm.c build/veclibm.a build/veclibm-runtime.o Makefile ../config.mk ../autovec/tls.ld ../thirdparty/link.ld
	@echo "  LD    $@"
	@${CC} $(VECLIBM_CFLAGS) -DVECLIBM_N=$(VECLIBM_N) -DVECLIBM_REPEATS=$(VECLIBM_REPEATS) \
		-o $@ $< $(VECLIBM_LINK)
	@${DUMP} -S -d $@ > $(@:.elf=.dasm)
	@${OBJCOPY} -S --set-section-flags .bss=alloc,contents -O binary $@ $(@:.elf=.bin)

build/veclibm-%-n$(VECLIBM_N)-r$(VECLIBM_REPEATS).elf: veclibm.c build/veclibm.a build/veclibm-runtime.o Makefile ../config.mk ../autovec/tls.ld ../thirdparty/link.ld
	@echo "  LD    $@ (-DTEST_FUNC=$*)"
	@${CC} $(VECLIBM_CFLAGS) -DVECLIBM_N=$(VECLIBM_N) -DVECLIBM_REPEATS=$(VECLIBM_REPEATS) -DTEST_FUNC=$* \
		-o $@ $< $(VECLIBM_LINK)
	@${DUMP} -S -d $@ > $(@:.elf=.dasm)
	@${OBJCOPY} -S --set-section-flags .bss=alloc,contents -O binary $@ $(@:.elf=.bin)

# --- convenience ---
# build all per-function tests at current data size
veclibm-all: ${VECLIBM_ELFS}
	@true

# data sizes for sweep: 4^0 .. 4^8 (max <= 128K)
VECLIBM_SIZES = 1 4 16 64 256 1024 4096 16384 65536

# build all functions for ALL data sizes
veclibm-sweep:
	@for n in $(VECLIBM_SIZES); do \
		echo "=== VECLIBM_N=$$n ==="; \
		$(MAKE) VECLIBM_N=$$n veclibm-all || exit 1; \
	done
	@echo "=== sweep done: $(words $(VECLIBM_SIZES)) sizes x $(words $(VECLIBM_FUNCS)) funcs ==="

.PHONY: veclibm-all veclibm-quick run-veclibm run-veclibm-quick veclibm-accuracy run-veclibm-accuracy

# Calibrated sizes target ~10M total NEMU guest instructions per function.
veclibm-quick:
	python3 build_veclibm.py --gcc $(CC) --config veclibm-quick.json --output $(VECLIBM_QUICK_DIR)

run-veclibm: veclibm-all
	python3 run_veclibm.py --nemu $(NEMU) --bins $(VECLIBM_ELFS:.elf=.bin) --output $(VECLIBM_RUN_DIR)

run-veclibm-quick: veclibm-quick
	python3 run_veclibm.py --nemu $(NEMU) --manifest $(VECLIBM_QUICK_DIR)/manifest.json --output $(VECLIBM_RUN_DIR) --range-min 5000000 --range-max 15000000 --require-budget

veclibm-accuracy:
	$(MAKE) -C veclibm-accuracy all

run-veclibm-accuracy:
	$(MAKE) -C veclibm-accuracy run NEMU=$(NEMU)

dasm: ${EXECS}
bin:  ${EXECS}

clean:
	rm -rf build/

.PRECIOUS: $(VECLIBM_OBJ) build/veclibm.a