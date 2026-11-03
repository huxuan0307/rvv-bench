.SUFFIXES:

include ../config.mk

NEMU ?= riscv64-nemu-interpreter
BUILD = build
CHECK_DIR = $(BUILD)/check
RUN_DIR = $(BUILD)/run
MAX_DEST ?= 262144
MEMCPY_INIT ?= 1
NEXT_ALGO ?= ORIGIN
DEST_TAG = d$(MAX_DEST)-mi$(MEMCPY_INIT)-na$(NEXT_ALGO)

ifeq ($(NEXT_ALGO),ORIGIN)
NEXT_ALGO_DEFINE = -DNEXT_ALGO_ORIGIN
else ifeq ($(NEXT_ALGO),FIBONACCI)
NEXT_ALGO_DEFINE = -DNEXT_ALGO_FIBONACCI
else
$(error NEXT_ALGO must be ORIGIN or FIBONACCI, got '$(NEXT_ALGO)')
endif

LINK = -T ../thirdparty/link.ld \
	../thirdparty/am-riscv64-xs.a \
	../thirdparty/klib-riscv64-xs.a

BENCHES = memcpy memset memreverse utf8_count strlen mergelines \
	mandelbrot chacha20 poly1305 ascii_to_utf16 ascii_to_utf32 \
	byteswap LUT4 LUT6 hist base64_encode trans8x8e8 trans8x8e16

memcpy_REF = scalar
memcpy_IMPLS = musl scalar scalar_autovec \
	rvv_m1 rvv_m2 rvv_m4 rvv_m8 \
	rvv_align_dest_m1 rvv_align_dest_m2 rvv_align_dest_m4 rvv_align_dest_m8 \
	rvv_align_src_m1 rvv_align_src_m2 rvv_align_src_m4 rvv_align_src_m8 \
	rvv_align_dest_hybrid_m1 rvv_align_dest_hybrid_m2 rvv_align_dest_hybrid_m4 rvv_align_dest_hybrid_m8 \
	rvv_vlmax_m1 rvv_vlmax_m2 rvv_vlmax_m4 rvv_vlmax_m8 \
	rvv_tail_m1 rvv_tail_m2 rvv_tail_m4 rvv_tail_m8 \
	rvv_128_m1 rvv_128_m2 rvv_128_m4 rvv_128_m8

memset_REF = scalar
memset_IMPLS = musl scalar scalar_autovec \
	rvv_m1 rvv_m2 rvv_m4 rvv_m8 \
	rvv_align_m1 rvv_align_m2 rvv_align_m4 rvv_align_m8 \
	rvv_vlmax_m1 rvv_vlmax_m2 rvv_vlmax_m4 rvv_vlmax_m8 \
	rvv_tail_m1 rvv_tail_m2 rvv_tail_m4 rvv_tail_m8 \
	rvv_tail_4x_m1 rvv_tail_4x_m2 rvv_tail_4x_m4 rvv_tail_4x_m8

memreverse_REF = scalar
memreverse_IMPLS = scalar scalar_autovec \
	rvv_vsse_m1 rvv_vsse_m2 rvv_vsse_m4 rvv_vsse_m8 \
	rvv_vlse_m1 rvv_vlse_m2 rvv_vlse_m4 rvv_vlse_m8 \
	rvv_vrgatherei16_m1 rvv_vrgatherei16_m2 rvv_vrgatherei16_m4 \
	rvv_m1_vrgatherei16_m1 rvv_m1_vrgatherei16_m2 rvv_m1_vrgatherei16_m4 rvv_m1_vrgatherei16_m8

utf8_count_REF = scalar
utf8_count_IMPLS = scalar scalar_autovec \
	SWAR_popc SWAR_popc_autovec \
	SWAR_popc_bithack SWAR_popc_bithack_autovec \
	rvv_m1 rvv_m2 rvv_m4 rvv_m8 \
	rvv_align_m1 rvv_align_m2 rvv_align_m4 rvv_align_m8 \
	rvv_vlmax_m1 rvv_vlmax_m2 rvv_vlmax_m4 rvv_vlmax_m8 \
	rvv_tail_m1 rvv_tail_m2 rvv_tail_m4 rvv_tail_m8 \
	rvv_128_m1 rvv_128_m2 rvv_128_m4 rvv_128_m8 \
	rvv_4x_m1 rvv_4x_m2 rvv_4x_m4 rvv_4x_m8 \
	rvv_4x_tail_m1 rvv_4x_tail_m2 rvv_4x_tail_m4 rvv_4x_tail_m8

strlen_REF = scalar
strlen_IMPLS = scalar scalar_autovec musl \
	rvv_page_aligned_m1 rvv_page_aligned_m2 rvv_page_aligned_m4 rvv_page_aligned_m8 \
	rvv_m1 rvv_m2 rvv_m4 rvv_m8

mergelines_REF = scalar
mergelines_IMPLS = scalar \
	rvv_vslide_m1 rvv_vslide_m2 rvv_vslide_m4 rvv_vslide_m8 \
	rvv_vslide_skip_m1 rvv_vslide_skip_m2 rvv_vslide_skip_m4 rvv_vslide_skip_m8 \
	rvv_mshift_m1 rvv_mshift_m2 rvv_mshift_m4 rvv_mshift_m8 \
	rvv_mshift_skip_m1 rvv_mshift_skip_m2 rvv_mshift_skip_m4 rvv_mshift_skip_m8

mandelbrot_REF = scalar_f32
mandelbrot_IMPLS = scalar_f32 scalar_f64 \
	rvv_f32_m1 rvv_f32_m2 \
	rvv_f64_m1 rvv_f64_m2

chacha20_REF = boring
chacha20_IMPLS = boring rvv riscv riscv_xor riscv_m4
chacha20_DEPS = chacha20-riscv-opt.h chacha20-riscv-m4.S ../nolibc.h \
	../thirdparty/rvv-chacha-poly/boring.h \
	../thirdparty/rvv-chacha-poly/boring.c \
	../thirdparty/rvv-chacha-poly/vchacha.s \
	../thirdparty/chacha20-riscv/src/chacha20.h \
	../thirdparty/chacha20-riscv/src/chacha20.s

poly1305_REF = boring
poly1305_IMPLS = boring rvv

ascii_to_utf16_REF = scalar
ascii_to_utf16_IMPLS = scalar scalar_autovec \
	rvv_ext_m1 rvv_ext_m2 rvv_ext_m4 \
	rvv_vsseg_m1 rvv_vsseg_m2 rvv_vsseg_m4 \
	rvv_vss_m1 rvv_vss_m2 rvv_vss_m4

ascii_to_utf32_REF = scalar
ascii_to_utf32_IMPLS = scalar scalar_autovec \
	rvv_ext_m1 rvv_ext_m2 \
	rvv_vsseg_m1 rvv_vsseg_m2 \
	rvv_vss_m1 rvv_vss_m2

byteswap_REF = scalar
byteswap_IMPLS = scalar scalar_autovec SWAR_rev8 \
	rvv_vrev8_m1 rvv_vrev8_m2 rvv_vrev8_m4 rvv_vrev8_m8 \
	rvv_seg4_m1 rvv_seg4_m2 \
	rvv_vrgatherei16_m1 rvv_vrgatherei16_m2 rvv_vrgatherei16_m4 \
	rvv_m1_vrgatherei16s_m2 rvv_m1_vrgatherei16s_m4 rvv_m1_vrgatherei16s_m8

LUT4_REF = scalar
LUT4_IMPLS = scalar scalar_autovec \
	rvv_vrgather_m1 rvv_vrgather_m2 rvv_vrgather_m4 rvv_vrgather_m8 \
	rvv_m1_vrgathers_m2 rvv_m1_vrgathers_m4 rvv_m1_vrgathers_m8 \
	rvv_vluxei8_m1 rvv_vluxei8_m2 rvv_vluxei8_m4 rvv_vluxei8_m8 \
	rvv_vloxei8_m1 rvv_vloxei8_m2 rvv_vloxei8_m4 rvv_vloxei8_m8

LUT6_REF = scalar
LUT6_IMPLS = scalar scalar_autovec \
	rvv_vrgather_m4 rvv_m1m2m4_vrgathers_m4 \
	rvv_vluxei8_m4 rvv_vloxei8_m4

hist_REF = scalar
hist_IMPLS = scalar \
	rvv_slidedown_m1 rvv_slidedown_m2 rvv_slidedown_m4 rvv_slidedown_m8 \
	rvv_assume_no_conflict_m1 rvv_assume_no_conflict_m2 rvv_assume_no_conflict_m4 rvv_assume_no_conflict_m8 \
	rvv_dup_entries_m1 rvv_dup_entries_m2 rvv_dup_entries_m4

base64_encode_REF = scalar
base64_encode_IMPLS = scalar rvv_LUT64 rvv_LUT16 rvv_seg_LUT64 rvv_seg_LUT16

trans8x8e8_REF = seq_scalar
trans8x8e8_IMPLS = seq_scalar seq_scalar_autovec \
	zip_rvv_vslide seq_rvv_vslide_single \
	seq_rvv_vlseg8_single seq_rvv_vsseg8_single \
	seq_rvv_vls_single seq_rvv_vss_single \
	zip_rvv_vzip_fake

trans8x8e16_REF = seq_scalar
trans8x8e16_IMPLS = seq_scalar seq_scalar_autovec \
	zip_rvv_vrgather zip_rvv_vslide \
	seq_rvv_vslide_single seq_rvv_vlseg8_single seq_rvv_vsseg8_single \
	seq_rvv_vls_single seq_rvv_vss_single \
	zip_rvv_vzip_fake seq_rvv_vzip_fake_single

CHECK_ELFS = $(foreach b,$(BENCHES),$(foreach i,$($(b)_IMPLS),$(CHECK_DIR)/$(b)-$(i)-$(DEST_TAG).elf))
RUN_ELFS   = $(foreach b,$(BENCHES),$(foreach i,$($(b)_IMPLS),$(RUN_DIR)/$(b)-$(i)-$(DEST_TAG).elf))
CHECK_BINS = $(CHECK_ELFS:.elf=.bin)
RUN_BINS   = $(RUN_ELFS:.elf=.bin)
CHECK_DASMS = $(CHECK_ELFS:.elf=.dasm)
RUN_DASMS   = $(RUN_ELFS:.elf=.dasm)

.PHONY: all check run bins dasm clean list run-nemu-check run-nemu test-chacha20 profile-chacha20

all: check run

check: $(CHECK_ELFS) $(CHECK_BINS) $(CHECK_DASMS)
run: $(RUN_ELFS) $(RUN_BINS) $(RUN_DASMS)
bins: $(CHECK_BINS) $(RUN_BINS)
dasm: $(CHECK_DASMS) $(RUN_DASMS)

list:
	@echo "MAX_DEST=$(MAX_DEST)  MEMCPY_INIT=$(MEMCPY_INIT)  NEXT_ALGO=$(NEXT_ALGO)  check: $(words $(CHECK_ELFS))  run: $(words $(RUN_ELFS))"
	@printf '%s\n' $(CHECK_ELFS) $(RUN_ELFS)

$(CHECK_DIR) $(RUN_DIR):
	mkdir -p $@

define BENCH_RULES
$(CHECK_DIR)/$(1)-%-$(DEST_TAG).elf: $(1).c $(1).S config.h bench.h template.S ../config.mk $($(1)_DEPS) | $(CHECK_DIR)
	@echo "  CC  $$@"
	@$${CC} $${CFLAGS} -DCHECK=1 -DONLY_IMPL=$$* -DREF_IMPL=$($(1)_REF) -DMAX_DEST=$(MAX_DEST) -DMEMCPY_INIT=$(MEMCPY_INIT) $(NEXT_ALGO_DEFINE) \
		-o $$@ $$< -DINC=$(1).S template.S $${LINK}

$(RUN_DIR)/$(1)-%-$(DEST_TAG).elf: $(1).c $(1).S config.h bench.h template.S ../config.mk $($(1)_DEPS) | $(RUN_DIR)
	@echo "  CC  $$@"
	@$${CC} $${CFLAGS} -DCHECK=0 -DONLY_IMPL=$$* -DMAX_DEST=$(MAX_DEST) -DMEMCPY_INIT=$(MEMCPY_INIT) $(NEXT_ALGO_DEFINE) \
		-o $$@ $$< -DINC=$(1).S template.S $${LINK}
endef
$(foreach b,$(BENCHES),$(eval $(call BENCH_RULES,$(b))))

$(CHECK_DIR)/chacha20-api-$(DEST_TAG).elf: chacha20-test.c chacha20.c chacha20.S config.h bench.h template.S ../config.mk $(chacha20_DEPS) | $(CHECK_DIR)
	@echo "  CC  $@"
	@${CC} ${CFLAGS} -DCHECK=1 -DMAX_DEST=$(MAX_DEST) -DMEMCPY_INIT=$(MEMCPY_INIT) $(NEXT_ALGO_DEFINE) \
		-o $@ $< -DINC=chacha20.S template.S ${LINK}

test-chacha20: $(CHECK_DIR)/chacha20-api-$(DEST_TAG).bin
	@$(NEMU) -b $< >$(CHECK_DIR)/chacha20-api-$(DEST_TAG).log 2>&1 || { cat $(CHECK_DIR)/chacha20-api-$(DEST_TAG).log; exit 1; }
	@cat $(CHECK_DIR)/chacha20-api-$(DEST_TAG).log
	@grep -q 'OK: chacha20 direct riscv==rvv==riscv_m4' $(CHECK_DIR)/chacha20-api-$(DEST_TAG).log
	@grep -q 'OK: chacha20 stream API' $(CHECK_DIR)/chacha20-api-$(DEST_TAG).log
	@grep -q 'HIT GOOD TRAP' $(CHECK_DIR)/chacha20-api-$(DEST_TAG).log
	@! grep -Eq 'ERROR:|HIT BAD TRAP|HIT CRITICAL ERROR' $(CHECK_DIR)/chacha20-api-$(DEST_TAG).log

$(CHECK_DIR)/chacha20-profile-$(DEST_TAG).elf: chacha20-profile.c chacha20.c chacha20.S config.h bench.h template.S ../config.mk $(chacha20_DEPS) | $(CHECK_DIR)
	@echo "  CC  $@"
	@${CC} ${CFLAGS} -DCHECK=0 -DMAX_DEST=$(MAX_DEST) -DMEMCPY_INIT=$(MEMCPY_INIT) $(NEXT_ALGO_DEFINE) \
		-o $@ $< -DINC=chacha20.S template.S ${LINK}

profile-chacha20: $(CHECK_DIR)/chacha20-profile-$(DEST_TAG).bin $(CHECK_DIR)/chacha20-profile-$(DEST_TAG).dasm
	@$(NEMU) -b $< >$(CHECK_DIR)/chacha20-profile-$(DEST_TAG).log 2>&1 || { cat $(CHECK_DIR)/chacha20-profile-$(DEST_TAG).log; exit 1; }
	@cat $(CHECK_DIR)/chacha20-profile-$(DEST_TAG).log
	@grep -q 'OK: chacha20 instruction profile' $(CHECK_DIR)/chacha20-profile-$(DEST_TAG).log
	@grep -q 'HIT GOOD TRAP' $(CHECK_DIR)/chacha20-profile-$(DEST_TAG).log
	@! grep -Eq 'ERROR:|HIT BAD TRAP|HIT CRITICAL ERROR' $(CHECK_DIR)/chacha20-profile-$(DEST_TAG).log

$(CHECK_DIR)/%.bin: $(CHECK_DIR)/%.elf
	@echo "  BIN $@"
	@${OBJCOPY} -S --set-section-flags .bss=alloc,contents -O binary $< $@

$(RUN_DIR)/%.bin: $(RUN_DIR)/%.elf
	@echo "  BIN $@"
	@${OBJCOPY} -S --set-section-flags .bss=alloc,contents -O binary $< $@

$(CHECK_DIR)/%.dasm: $(CHECK_DIR)/%.elf
	@echo "  DUMP $@"
	@${DUMP} -S -d $< > $@

$(RUN_DIR)/%.dasm: $(RUN_DIR)/%.elf
	@echo "  DUMP $@"
	@${DUMP} -S -d $< > $@

NEMU_LOG ?= ../logs/run/nemu-bench
NEMU_JOBS ?= 32

run-nemu-check: check
	@mkdir -p $(NEMU_LOG)/check
	@printf '%s\n' $(CHECK_BINS) | xargs -P $(NEMU_JOBS) -I{} sh -c ' \
		name=$$(basename {} .bin); \
		$(NEMU) -b {} >$(NEMU_LOG)/check/$$name.out 2>$(NEMU_LOG)/check/$$name.err; \
		ec=$$?; \
		if grep -q "^ERROR:" $(NEMU_LOG)/check/$$name.err 2>/dev/null; then echo FAIL $$name; exit 1; fi; \
		if ! grep -q "HIT GOOD TRAP" $(NEMU_LOG)/check/$$name.out 2>/dev/null; then echo FAIL $$name; exit 1; fi; \
		if [ $$ec -ne 0 ]; then echo FAIL $$name; exit 1; fi; \
		echo PASS $$name'

run-nemu: run
	@mkdir -p $(NEMU_LOG)/run
	@printf '%s\n' $(RUN_BINS) | xargs -P $(NEMU_JOBS) -I{} sh -c ' \
		name=$$(basename {} .bin); \
		$(NEMU) -b {} >$(NEMU_LOG)/run/$$name.out 2>$(NEMU_LOG)/run/$$name.err; \
		ec=$$?; \
		if ! grep -q "HIT GOOD TRAP" $(NEMU_LOG)/run/$$name.out 2>/dev/null; then echo FAIL $$name; exit 1; fi; \
		if [ $$ec -ne 0 ]; then echo FAIL $$name; exit 1; fi; \
		echo PASS $$name'

clean:
	rm -rf $(BUILD)
	rm -f $(BENCHES)
