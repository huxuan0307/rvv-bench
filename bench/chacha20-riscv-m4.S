.text
.balign 4
.global riscv_chacha20_m4_blocks
.type riscv_chacha20_m4_blocks, @function

/* Internal leaf: n is a multiple of 256; byte pointers and key/nonce are word aligned.
 * Work rows: v0/v4/v8/v12 (m4), temporary: v16 (m4).
 * Gather indices: v20/v22/v24 (m2), column indices: v26 (m2).
 * v28 (m4) is scratch. v20 becomes I/O offsets after the rounds.
 */
.macro CHACHA_M4_QR a, b, c, d
	vadd.vv \a, \a, \b
	vxor.vv \d, \d, \a
	vror.vi \d, \d, 16
	vadd.vv \c, \c, \d
	vxor.vv \b, \b, \c
	vror.vi \b, \b, 20
	vadd.vv \a, \a, \b
	vxor.vv \d, \d, \a
	vror.vi \d, \d, 24
	vadd.vv \c, \c, \d
	vxor.vv \b, \b, \c
	vror.vi \b, \b, 25
.endm

/* Output roles are (a,d,t,b), with c free: pair calls to restore the mapping. */
.macro CHACHA_M4_DOUBLE a, b, c, d, t
	CHACHA_M4_QR \a, \b, \c, \d
	vrgatherei16.vv \t, \b, v20
	vrgatherei16.vv \b, \c, v22
	vrgatherei16.vv \c, \d, v24
	CHACHA_M4_QR \a, \t, \b, \c
	vrgatherei16.vv \d, \t, v24
	vrgatherei16.vv \t, \b, v22
	vrgatherei16.vv \b, \c, v20
.endm

.macro CHACHA_M4_REPEAT dst, base
	vsetivli zero, 4, e32, m4, ta, ma
	vle32.v v16, (\base)
	vsetivli zero, 16, e32, m4, ta, ma
	vrgatherei16.vv \dst, v16, v26
.endm

/* Construct counter+block, nonce[0..2] without retaining an original row. */
.macro CHACHA_M4_COUNTER dst
	vid.v \dst
	vsrl.vi \dst, \dst, 2
	vadd.vx \dst, \dst, a5
	vzext.vf2 v28, v26
	lw t0, 0(a4)
	vmseq.vi v0, v28, 1
	vmerge.vxm \dst, \dst, t0, v0
	lw t0, 4(a4)
	vmseq.vi v0, v28, 2
	vmerge.vxm \dst, \dst, t0, v0
	lw t0, 8(a4)
	vmseq.vi v0, v28, 3
	vmerge.vxm \dst, \dst, t0, v0
.endm

.macro CHACHA_M4_WRITE row, offset
	addi t0, a1, \offset
	addi t1, a0, \offset
	vluxei16.v v16, (t0), v20
	vxor.vv \row, \row, v16
	vsuxei16.v \row, (t1), v20
.endm

riscv_chacha20_m4_blocks:
	beqz a2, .Lchacha_m4_done
	lla a6, .Lchacha_m4_sigma
	vsetivli zero, 16, e16, m2, ta, ma
	vid.v v26
	vand.vi v28, v26, -4
	vadd.vi v22, v26, 2
	vand.vi v22, v22, 3
	vor.vv v22, v22, v28
	vadd.vi v24, v26, 3
	vand.vi v24, v24, 3
	vor.vv v24, v24, v28
	vand.vi v26, v26, 3
.Lchacha_m4_batch:
	/* Restore idx1, whose group is reused for I/O after each batch. */
	vsetivli zero, 16, e16, m2, ta, ma
	vid.v v20
	vand.vi v28, v20, -4
	vadd.vi v20, v20, 1
	vand.vi v20, v20, 3
	vor.vv v20, v20, v28
	vsetivli zero, 16, e32, m4, ta, ma
	CHACHA_M4_COUNTER v12
	CHACHA_M4_REPEAT v0, a6
	CHACHA_M4_REPEAT v4, a3
	addi t0, a3, 16
	CHACHA_M4_REPEAT v8, t0
	.rept 5
	CHACHA_M4_DOUBLE v0, v4, v8, v12, v16
	CHACHA_M4_DOUBLE v0, v12, v16, v4, v8
	.endr
	/* row i's word j comes from file offset 64*(j/4)+4*(j%4)+16*i. */
	vsetivli zero, 16, e16, m2, ta, ma
	vid.v v20
	vand.vi v28, v20, 3
	vsll.vi v28, v28, 2
	vsrl.vi v20, v20, 2
	vsll.vi v20, v20, 6
	vor.vv v20, v20, v28
	CHACHA_M4_REPEAT v28, a6
	vadd.vv v0, v0, v28
	CHACHA_M4_WRITE v0, 0
	CHACHA_M4_REPEAT v28, a3
	vadd.vv v4, v4, v28
	CHACHA_M4_WRITE v4, 16
	addi t0, a3, 16
	CHACHA_M4_REPEAT v28, t0
	vadd.vv v8, v8, v28
	CHACHA_M4_WRITE v8, 32
	/* va has been written, so v0 can now hold the nonce merge masks. */
	CHACHA_M4_COUNTER v16
	vadd.vv v12, v12, v16
	CHACHA_M4_WRITE v12, 48
	addi a0, a0, 256
	addi a1, a1, 256
	addi a2, a2, -256
	addi a5, a5, 4
	bnez a2, .Lchacha_m4_batch
.Lchacha_m4_done:
	ret
.size riscv_chacha20_m4_blocks, .-riscv_chacha20_m4_blocks

.pushsection .rodata
.balign 16
.Lchacha_m4_sigma:
	.word 0x61707865, 0x3320646e, 0x79622d32, 0x6b206574
.popsection
