.POSIX:

include ../config.mk

VECLIBM_FUNCS = exp exp2 expm1 log log10 log2 log1p sqrt cbrt \
                sin cos tan asin acos atan \
                sinh cosh tanh asinh acosh atanh \
                erf erfc tgamma lgamma

# data size (number of double-precision elements, ~1MB per array at default)
VECLIBM_N ?= 131072

# all output under build/ — names include data size, e.g. veclibm-sqrt-n131072.elf
VECLIBM_ELFS  = $(VECLIBM_FUNCS:%=build/veclibm-%-n$(VECLIBM_N).elf)
EXECS         = build/uarch.elf build/veclibm-n$(VECLIBM_N).elf ${VECLIBM_ELFS}
DASMS         = $(EXECS:.elf=.dasm)
BINS          = $(EXECS:.elf=.bin)

# veclibm static library
VECLIBM_SRC_DIR = ../thirdparty/veclibm/src
VECLIBM_INC_DIR = ../thirdparty/veclibm/include
VECLIBM_SRC     = $(wildcard $(VECLIBM_SRC_DIR)/*.c)
VECLIBM_OBJ     = $(VECLIBM_SRC:$(VECLIBM_SRC_DIR)/%.c=build/veclibm-obj/%.o)

VECLIBM_CFLAGS = -march=rv64gcv_zba_zbb_zbs_zvbb -mcmodel=medany -O2 -g ${WARN} \
                  -I $(VECLIBM_INC_DIR) -Wno-unused -Wno-maybe-uninitialized \
                  -fno-builtin-printf -ffreestanding

$(shell mkdir -p build/veclibm-obj)

all: ${EXECS}

${EXECS}: ../config.mk

# --- uarch ---
build/uarch.elf: uarch.S uarch.c
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

build/veclibm-obj/%.o: $(VECLIBM_SRC_DIR)/%.c
	@mkdir -p $(@D)
	@echo "  CC    $@"
	@${CC} $(VECLIBM_CFLAGS) -c -o $@ $<

# --- veclibm full (all 25 functions in one binary) ---
# Uses lm_stubs.o (compiled medany) instead of -lm (precompiled medlow can't reach 0x80000000)
build/veclibm-n$(VECLIBM_N).elf: veclibm.c build/veclibm.a build/lm_stubs.o
	@echo "  LD    $@"
	@${CC} -nostdlib $(VECLIBM_CFLAGS) -DVECLIBM_N=$(VECLIBM_N) \
		-o $@ $< build/veclibm.a build/lm_stubs.o \
		-T ../thirdparty/link.ld \
		../thirdparty/am-riscv64-xs.a \
		../thirdparty/klib-riscv64-xs.a
	@echo "  DUMP  $(@:.elf=.dasm)"
	@${DUMP} -S -d $@ > $(@:.elf=.dasm)
	@echo "  OBJCP $(@:.elf=.bin)"
	@${OBJCOPY} -S --set-section-flags .bss=alloc,contents -O binary $@ $(@:.elf=.bin)

# --- veclibm per-function (e.g. make build/veclibm-exp-n131072.elf) ---
build/veclibm-%-n$(VECLIBM_N).elf: veclibm.c build/veclibm.a build/lm_stubs.o
	@echo "  LD    $@ (-DTEST_FUNC=$*)"
	@${CC} -nostdlib $(VECLIBM_CFLAGS) -DVECLIBM_N=$(VECLIBM_N) -DTEST_FUNC=$* \
		-o $@ $< build/veclibm.a build/lm_stubs.o \
		-T ../thirdparty/link.ld \
		../thirdparty/am-riscv64-xs.a \
		../thirdparty/klib-riscv64-xs.a
	@echo "  DUMP  $(@:.elf=.dasm)"
	@${DUMP} -S -d $@ > $(@:.elf=.dasm)
	@echo "  OBJCP $(@:.elf=.bin)"
	@${OBJCOPY} -S --set-section-flags .bss=alloc,contents -O binary $@ $(@:.elf=.bin)

# --- bare-metal scalar math stubs (compiled medany, no libm dependency) ---
build/lm_stubs.o: lm_stubs.c
	@mkdir -p $(@D)
	@echo "  CC    $@"
	@${CC} $(VECLIBM_CFLAGS) -c -o $@ $<

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

dasm: ${EXECS}
bin:  ${EXECS}

clean:
	rm -rf build/

.PRECIOUS: $(VECLIBM_OBJ) build/veclibm.a