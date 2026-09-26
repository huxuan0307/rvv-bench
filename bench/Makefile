.SUFFIXES:

include ../config.mk

NEMU ?= riscv64-nemu-interpreter
BUILD = build
CHECK_DIR = $(BUILD)/check
RUN_DIR = $(BUILD)/run

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
chacha20_IMPLS = boring rvv

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

CHECK_ELFS = $(foreach b,$(BENCHES),$(foreach i,$($(b)_IMPLS),$(CHECK_DIR)/$(b)-$(i).elf))
RUN_ELFS   = $(foreach b,$(BENCHES),$(foreach i,$($(b)_IMPLS),$(RUN_DIR)/$(b)-$(i).elf))
CHECK_BINS = $(CHECK_ELFS:.elf=.bin)
RUN_BINS   = $(RUN_ELFS:.elf=.bin)

.PHONY: all check run bins clean list run-nemu-check run-nemu

all: check run

check: $(CHECK_ELFS) $(CHECK_BINS)
run: $(RUN_ELFS) $(RUN_BINS)
bins: $(CHECK_BINS) $(RUN_BINS)

list:
	@echo "check: $(words $(CHECK_ELFS))  run: $(words $(RUN_ELFS))"
	@printf '%s\n' $(CHECK_ELFS) $(RUN_ELFS)

$(CHECK_DIR) $(RUN_DIR):
	mkdir -p $@

define BENCH_RULES
$(CHECK_DIR)/$(1)-%.elf: $(1).c $(1).S config.h bench.h template.S ../config.mk | $(CHECK_DIR)
	@echo "  CC  $$@"
	@$${CC} $${CFLAGS} -DCHECK=1 -DONLY_IMPL=$$* -DREF_IMPL=$($(1)_REF) \
		-o $$@ $$< -DINC=$(1).S template.S $${LINK}

$(RUN_DIR)/$(1)-%.elf: $(1).c $(1).S config.h bench.h template.S ../config.mk | $(RUN_DIR)
	@echo "  CC  $$@"
	@$${CC} $${CFLAGS} -DCHECK=0 -DONLY_IMPL=$$* \
		-o $$@ $$< -DINC=$(1).S template.S $${LINK}
endef
$(foreach b,$(BENCHES),$(eval $(call BENCH_RULES,$(b))))

$(CHECK_DIR)/%.bin: $(CHECK_DIR)/%.elf
	@echo "  BIN $@"
	@${OBJCOPY} -S --set-section-flags .bss=alloc,contents -O binary $< $@

$(RUN_DIR)/%.bin: $(RUN_DIR)/%.elf
	@echo "  BIN $@"
	@${OBJCOPY} -S --set-section-flags .bss=alloc,contents -O binary $< $@

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




