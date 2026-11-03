# Include this fragment from instructions/rvv/Makefile; it does not replace it.
P1_DIR ?= build/p1
P1_ASM ?= $(P1_DIR)/p1.S
P1_CASES_H ?= $(P1_DIR)/p1_cases.h
P1_MANIFEST ?= $(P1_DIR)/manifest.json
P1_ELF ?= $(P1_DIR)/p1.elf
P1_BIN ?= $(P1_DIR)/p1.bin
P1_LOG ?= $(P1_DIR)/p1.log
P1_RUNS ?= 3
P1_SUITE ?= all
P1_LOOPS ?= 64
NEMU ?= /nfs/home/huxuan/opt/bin/riscv64-nemu-interpreter

.PHONY: p1 p1-run

$(P1_DIR):
	mkdir -p $@

$(P1_ASM) $(P1_CASES_H) $(P1_MANIFEST) &: p1_gen.py | $(P1_DIR)
	python3 p1_gen.py --asm $(P1_ASM) --header $(P1_CASES_H) --manifest $(P1_MANIFEST) --suite $(P1_SUITE) --loops $(P1_LOOPS) --samples $(P1_RUNS)

$(P1_ELF): $(P1_ASM) $(P1_CASES_H) p1_main.c ../../config.mk
	$(CC) $(CFLAGS) \
		-fno-tree-vectorize -I$(P1_DIR) -Wl,--relax $(P1_ASM) p1_main.c -o $@ \
		-T ../../thirdparty/link.ld ../../thirdparty/am-riscv64-xs.a ../../thirdparty/klib-riscv64-xs.a

$(P1_BIN): $(P1_ELF)
	$(OBJCOPY) -S --set-section-flags .bss=alloc,contents -O binary $< $@

p1: $(P1_BIN)

p1-run: $(P1_BIN)
	python3 p1_run.py --build $(P1_DIR) --nemu --emu $(NEMU)

# Keep batches immutable: use a fresh P1_DIR when changing suite/loops/samples.
