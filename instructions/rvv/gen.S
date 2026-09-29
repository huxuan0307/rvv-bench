#define VL s3

#define t7 s4
#define t8 s5

#if __riscv_xlen == 32
#define IF64(...)
#define lx lw
#define sx sw
#else
#define IF64(...) __VA_ARGS__
#define lx ld
#define sx sd
#endif

#if __riscv_flen == 64
#define IF_F64(...) __VA_ARGS__
#else
#define IF_F64(...)
#endif

#if __riscv_v_elen_fp == 64
#define IF_VF64(...) __VA_ARGS__
#else
#define IF_VF64(...)
#endif

#include "config.h"

changecom(`#', `')

.macro m_nop
.endm


define(`shift2',`shift(shift($*))')
define(`shift3',`shift(shift(shift($*)))')
define(`shift4',`shift(shift(shift(shift($*))))')

define(`m_mask',
	`m_$1($2, shift2($*))
ifdef(`ENABLE_MASK',
	`m_$1( $2`'m, shift2($*), v0.t)')')

/* Keep m_unimpl placeholders to preserve the LMUL-indexed pointer tables. */
define(`m_emit_case',
`ifelse(`$1',`m_unimpl',`$2',`ifdef(`BENCH_CASE',
`ifelse(BENCH_CASE,`$1',`$2')',
`$2')')')

define(`m_bench_vx',
	`m_mask($1, bench_$3`'vv, $2, m_nop, $3.vv, v8, v16, v24)
	m_mask( $1, bench_$3`'vx, $2, m_nop, $3.vx, v8, v16, t0)')

define(`m_bench_xv',
	`m_mask($1, bench_$3`'vv, $2, m_nop, $3.vv, v8, v16, v24)
	m_mask( $1, bench_$3`'vx, $2, m_nop, $3.vx, v8, t0,  v16)')

define(`m_bench_xi',
	`m_mask($1, bench_$3`'vx, $2, m_nop, $3.vx, v8, v16, t0)
	m_mask( $1, bench_$3`'vi, $2, m_nop, $3.vi, v8, v16, 13)')

define(`m_bench_xi',
	`m_mask($1, bench_$3`'vx, $2, m_nop, $3.vx, v8, v16, t0)
	m_mask( $1, bench_$3`'vi, $2, m_nop, $3.vi, v8, v16, 13)')

define(`m_bench_vxi',
	`m_emit_case($3.vv, `m_mask($1, bench_$3`'vv, $2, m_nop, $3.vv, v8, v16, v24)')
	m_emit_case($3.vx, `m_mask( $1, bench_$3`'vx, $2, m_nop, $3.vx, v8, v16, t0)')
	m_emit_case($3.vi, `m_mask( $1, bench_$3`'vi, $2, m_nop, $3.vi, v8, v16, 13)')')

define(`m_bench_vxm',
	`m_$1(bench_$3`'vvm, $2, m_nop, $3.vvm, v8, v16, v24, v0)
	 m_$1(bench_$3`'vxm, $2, m_nop, $3.vxm, v8, v16, t0, v0)')

define(`m_bench_vxim',
	`m_$1(bench_$3`'vvm, $2, m_nop, $3.vvm, v8, v16, v24, v0)
	 m_$1(bench_$3`'vxm, $2, m_nop, $3.vxm, v8, v16, t0, v0)
	 m_$1(bench_$3`'vim, $2, m_nop, $3.vim, v8, v16, 13, v0)')

define(`m_bench_vf',
	`m_mask($1, bench_$3`'vv, $2, m_nop, $3.vv, v8, v16, v24)
	m_mask( $1, bench_$3`'vf, $2, m_nop, $3.vf, v8, v16, ft0)')

define(`m_bench_fv',
	`m_mask($1, bench_$3`'vv, $2, m_nop, $3.vv, v8, v16, v24)
	m_mask( $1, bench_$3`'vf, $2, m_nop, $3.vf, v8, ft0, v16)')

define(`m_bench_wx',
	`m_mask($1, bench_$3`'wv, $2, m_nop, $3.wv, v8, v16, v24)
	m_mask( $1, bench_$3`'wx, $2, m_nop, $3.wx, v8, v16, t0)')

define(`m_bench_wf',
	`m_mask($1, bench_$3`'wv, $2, m_nop, $3.wv, v8, v16, v24)
	m_mask( $1, bench_$3`'wf, $2, m_nop, $3.wf, v8, v16, ft0)')

define(`m_bench_wxi',
	`m_mask($1, bench_$3`'wv, $2, m_nop, $3.wv, v8, v16, v24)
	m_mask( $1, bench_$3`'wx, $2, m_nop, $3.wx, v8, v16, t0)
	m_mask( $1, bench_$3`'wi, $2, m_nop, $3.wi, v8, v16, 13)')

define(`m_loadff',
	`m_emit(vl$3`'8ff,  mask, $1, bench_vl$3`'8ffv,  $2`'8,  m_mem8,  vl$3`'8ff.v,  shift3($*))
	m_emit(vl$3`'16ff, mask, $1, bench_vl$3`'16ffv, $2`'16, m_mem16, vl$3`'16ff.v, shift3($*))
	m_emit(vl$3`'32ff, mask, $1, bench_vl$3`'32ffv, $2`'32, m_mem32, vl$3`'32ff.v, shift3($*))
#if __riscv_v_elen >= 64
	m_emit(vl$3`'64ff, mask, $1, bench_vl$3`'64ffv, $2`'64, m_mem64, vl$3`'64ff.v, shift3($*))
#endif')

define(`m_stride',
	`m_mask($1, $2`'t3, shift2($*), t3)
	m_mask( $1, $2`'x0, shift2($*), x0)
	m_mask( $1, $2`'t1, shift2($*), t1/*-1*/)')

define(`m_loadstore',
	`m_emit(vl$4`'8,  $1, $2, bench_vl$4`'8v,  $3`'8,  m_mem8,  vl$4`'8.v,  shift4($*))
	m_emit(vl$4`'16, $1, $2, bench_vl$4`'16v, $3`'16, m_mem16, vl$4`'16.v, shift4($*))
	m_emit(vl$4`'32, $1, $2, bench_vl$4`'32v, $3`'32, m_mem32, vl$4`'32.v, shift4($*))
#if __riscv_v_elen >= 64
	m_emit(vl$4`'64, $1, $2, bench_vl$4`'64v, $3`'64, m_mem64, vl$4`'64.v, shift4($*))
#endif
	m_emit(vs$4`'8,  $1, $2, bench_vs$4`'8v,  $3`'8,  m_mem8,  vs$4`'8.v,  shift4($*))
	m_emit(vs$4`'16, $1, $2, bench_vs$4`'16v, $3`'16, m_mem16, vs$4`'16.v, shift4($*))
	m_emit(vs$4`'32, $1, $2, bench_vs$4`'32v, $3`'32, m_mem32, vs$4`'32.v, shift4($*))
#if __riscv_v_elen >= 64
	m_emit(vs$4`'64, $1, $2, bench_vs$4`'64v, $3`'64, m_mem64, vs$4`'64.v, shift4($*))
#endif')



.macro m_1bit
	vid.v v0
	remu t0, t0, VL
	vmseq.vx v8, v0, t0
.endm

.macro m_mod_v24_e8_vl
	csrr t8, vtype
	vsetvli t0, x0, e8, m8, ta, ma
	vremu.vx v24, v24, VL
	vsetvl x0, VL, t8
.endm

.macro m_mod_v24_e16_vl
	csrr t8, vtype
	vsetvli t0, x0, e16, m8, ta, ma
	vremu.vx v24, v24, VL
	vsetvl x0, VL, t8
.endm

.macro m_mod_t0_vl
	remu t0, t0, VL
.endm

/* we want to roughly access the memory range of vlenb*8*4 */
.macro m_mem shift
	csrr t8, vlenb
	la t1, aligned
	lx t1, (t1) /* index base */

	slli t7, t8, 4
	addi t7, t7, -1
	and t0, t0, t7
	add t0, t0, t1 /* unit-stride base */

	slli t7, t8, 3
	addi t7, t7, -1
	and t2, t2, t7
	add t2, t2, t1   /* strided base */
	li t3, 3<<\shift /* strided stride */

	srli t0, t0, \shift
	slli t0, t0, \shift

	srli t2, t2, \shift
	slli t2, t2, \shift

	li t1, -1<<\shift /* negative stride 1 */
.endm

.macro m_mem8
	m_mem 0
.endm
.macro m_mem16
	m_mem 1
.endm
.macro m_mem32
	m_mem 2
.endm
.macro m_mem64
	m_mem 3
.endm


.macro m_unimpl
	li a0, -1
	ret
.endm

define(`m_ifmx_t', `m_$3(shift3($*))')
define(`m_ifmx_f', `m_$3($4, 0, m_nop, m_unimpl)')
define(`m_ifmx_lmul_to_i', `ifelse(regexp($1,f), -1, $1, 0)')
define(`m_ifmx',`ifelse(eval($1 <= m_ifmx_lmul_to_i(m_LMUL) && $2 >= m_ifmx_lmul_to_i(m_LMUL)),1,`m_ifmx_t($*)',`m_ifmx_f($*)')')

/* m_emit(key, helper, args...) -> m_helper(args...) if BENCH_INSN unset or equals key */
define(`m_emit',
`ifdef(`BENCH_INSN',
`ifelse(BENCH_INSN,`$1',`m_$2(shift2($*))')',
`m_$2(shift2($*))')')

/* calls $1 with: name type setup code:vararg */
define(`m_bench_all',`
	m_emit(add, $1, bench_add, T_A, m_nop, add, t0, t1, t2)

	m_emit(mul, $1, bench_mul, T_A, m_nop, mul, t0, t1, t2)
	m_emit(vadd, bench_vxi, $1, T_A, vadd)
	m_emit(vsub, bench_vx,  $1, T_A, vsub)
	m_emit(vrsub, bench_xi, $1, T_A, vrsub)
	m_emit(vminu, bench_vx, $1, T_A, vminu)
	m_emit(vmin,  bench_vx, $1, T_A, vmin)
	m_emit(vmaxu, bench_vx, $1, T_A, vmaxu)
	m_emit(vmax,  bench_vx, $1, T_A, vmax)
	m_emit(vand, bench_vxi, $1, T_A, vand)
	m_emit(vor,  bench_vxi, $1, T_A, vor)
	m_emit(vxor, bench_vxi, $1, T_A, vxor)

	m_emit(vrgather, mask, $1, bench_vrgathervv,     T_A,  m_mod_v24_e8_vl,  vrgather.vv,     v8, v16, v24)
	m_emit(vrgather, mask, $1, bench_vrgathervx,     T_A,  m_mod_t0_vl,      vrgather.vx,     v8, v16, t0)
	m_emit(vrgather, mask, $1, bench_vrgathervi,     T_A,  m_nop,            vrgather.vi,     v8, v16, 3)
	m_emit(vslideup, mask, $1, bench_vslideupvx,     T_A,  m_mod_t0_vl,      vslideup.vx,     v8, v16, t0)
	m_emit(vslideup, mask, $1, bench_vslideupvi,     T_A,  m_nop,            vslideup.vi,     v8, v16, 3)
	m_emit(vrgatherei16, mask, $1, bench_vrgatherei16vv, T_ei16, m_mod_v24_e16_vl, vrgatherei16.vv, v8, v16, v24)

	m_emit(vslidedown, mask, $1, bench_vslidedownvx, T_A, m_mod_t0_vl,  vslidedown.vx, v8, v16, t0)
	m_emit(vslidedown, mask, $1, bench_vslidedownvi, T_A, m_nop,        vslidedown.vi, v8, v16, 3)

	m_emit(vredsum,  mask, $1, bench_vredsumvs,  T_A, m_nop, vredsum.vs,  v8, v16, v24)
	m_emit(vredand,  mask, $1, bench_vredandvs,  T_A, m_nop, vredand.vs,  v8, v16, v24)
	m_emit(vredor,   mask, $1, bench_vredorvs,   T_A, m_nop, vredor.vs,   v8, v16, v24)
	m_emit(vredxor,  mask, $1, bench_vredxorvs,  T_A, m_nop, vredxor.vs,  v8, v16, v24)
	m_emit(vredminu, mask, $1, bench_vredminuvs, T_A, m_nop, vredminu.vs, v8, v16, v24)
	m_emit(vredmin,  mask, $1, bench_vredminvs,  T_A, m_nop, vredmin.vs,  v8, v16, v24)
	m_emit(vredmaxu, mask, $1, bench_vredmaxuvs, T_A, m_nop, vredmaxu.vs, v8, v16, v24)
	m_emit(vredmax,  mask, $1, bench_vredmaxvs,  T_A, m_nop, vredmax.vs,  v8, v16, v24)

	m_emit(vaaddu, bench_vx, $1, T_A, vaaddu)
	m_emit(vaadd,  bench_vx, $1, T_A, vaadd)
	m_emit(vasubu, bench_vx, $1, T_A, vasubu)
	m_emit(vasub,  bench_vx, $1, T_A, vasub)

	m_emit(vslide1up,   mask, $1, bench_vslide1upvx,   T_A, m_nop, vslide1up.vx,   v8, v16, t0)
	m_emit(vslide1down, mask, $1, bench_vslide1downvx, T_A, m_nop, vslide1down.vx, v8, v16, t0)

	m_emit(vadc,  bench_vxim, $1, T_A, vadc)
	m_emit(vmadc, bench_vxim, $1, T_A, vmadc)
	m_emit(vmadc, $1, bench_vmadcvv, T_A, m_nop, vmadc.vv, v8, v16, v24)
	m_emit(vmadc, $1, bench_vmadcvx, T_A, m_nop, vmadc.vx, v8, v16, t0)
	m_emit(vmadc, $1, bench_vmadcvi, T_A, m_nop, vmadc.vi, v8, v16, 13)
	m_emit(vsbc,  bench_vxm, $1, T_A, vsbc)
	m_emit(vmsbc, bench_vxm, $1, T_A, vmsbc)
	m_emit(vmsbc, $1, bench_vmsbcvv, T_A, m_nop, vmsbc.vv, v8, v16, v24)
	m_emit(vmsbc, $1, bench_vmsbcvx, T_A, m_nop, vmsbc.vx, v8, v16, t0)

	m_emit(vmerge, bench_vxim, $1, T_A, vmerge)
	m_emit(vmv, $1, bench_vmvvv, T_A, m_nop, vmv.v.v, v8, v16)
	m_emit(vmv, $1, bench_vmvvx, T_A, m_nop, vmv.v.x, v8, t0)
	m_emit(vmv, $1, bench_vmvvi, T_A, m_nop, vmv.v.i, v8, 13)
	m_emit(vmseq,  bench_vxi, $1, T_A, vmseq)
	m_emit(vmsne,  bench_vxi, $1, T_A, vmsne)
	m_emit(vmsltu, bench_vx,  $1, T_A, vmsltu)
	m_emit(vmslt,  bench_vx,  $1, T_A, vmslt)
	m_emit(vmsleu, bench_vxi, $1, T_A, vmsleu)
	m_emit(vmsle,  bench_vxi, $1, T_A, vmsle)
	m_emit(vmsgtu, bench_xi,  $1, T_A, vmsgtu)
	m_emit(vmsgt,  bench_xi,  $1, T_A, vmsgt)

	m_emit(vcompress, $1, bench_vcompressvm, T_A, m_nop, vcompress.vm, v8, v16, v24)

	m_emit(vmandn, $1, bench_vmandnmm, T_m1, m_nop, vmandn.mm, v8, v16, v24)
	m_emit(vmand,  $1, bench_vmandmm,  T_m1, m_nop, vmand.mm,  v8, v16, v24)
	m_emit(vmor,   $1, bench_vmormm,   T_m1, m_nop, vmor.mm,   v8, v16, v24)
	m_emit(vmxor,  $1, bench_vmxormm,  T_m1, m_nop, vmxor.mm,  v8, v16, v24)
	m_emit(vmorn,  $1, bench_vmornmm,  T_m1, m_nop, vmorn.mm,  v8, v16, v24)
	m_emit(vmnand, $1, bench_vmnandmm, T_m1, m_nop, vmnand.mm, v8, v16, v24)
	m_emit(vmnor,  $1, bench_vmnormm,  T_m1, m_nop, vmnor.mm,  v8, v16, v24)
	m_emit(vmxnor, $1, bench_vmxnormm, T_m1, m_nop, vmxnor.mm, v8, v16, v24)

	m_emit(vsaddu, bench_vxi, $1, T_A, vsaddu)
	m_emit(vsadd,  bench_vxi, $1, T_A, vsadd)
	m_emit(vssubu, bench_vx,  $1, T_A, vssubu)
	m_emit(vssub,  bench_vx,  $1, T_A, vssub)
	m_emit(vsll,   bench_vxi, $1, T_A, vsll)
	m_emit(vsmul,  bench_vx,  $1, T_A, vsmul)
	m_emit(vmv1r, ifmx, 1,8,$1, bench_vmv1rv, T_A, m_nop, vmv1r.v, v8, v16)
	m_emit(vmv2r, ifmx, 2,8,$1, bench_vmv2rv, T_A, m_nop, vmv2r.v, v8, v16)
	m_emit(vmv4r, ifmx, 4,8,$1, bench_vmv4rv, T_A, m_nop, vmv4r.v, v8, v16)
	m_emit(vmv8r, ifmx, 8,8,$1, bench_vmv8rv, T_A, m_nop, vmv8r.v, v8, v16)
	m_emit(vsrl,  bench_vxi, $1, T_A, vsrl)
	m_emit(vsra,  bench_vxi, $1, T_A, vsra)
	m_emit(vssrl, bench_vxi, $1, T_A, vssrl)
	m_emit(vssra, bench_vxi, $1, T_A, vssra)

	m_emit(vdivu,   bench_vx, $1, T_A, vdivu)
	m_emit(vdiv,    bench_vx, $1, T_A, vdiv)
	m_emit(vremu,   bench_vx, $1, T_A, vremu)
	m_emit(vrem,    bench_vx, $1, T_A, vrem)
	m_emit(vmulhu,  bench_vx, $1, T_A, vmulhu)
	m_emit(vmul,    bench_vx, $1, T_A, vmul)
	m_emit(vmulhsu, bench_vx, $1, T_A, vmulhsu)
	m_emit(vmulh,   bench_vx, $1, T_A, vmulh)
	m_emit(vmadd,   bench_xv, $1, T_A, vmadd)
	m_emit(vmacc,   bench_xv, $1, T_A, vmacc)
	m_emit(vnmsub,  bench_xv, $1, T_A, vnmsub)
	m_emit(vnmsac,  bench_xv, $1, T_A, vnmsac)

	m_emit(vnsrl,   bench_wxi, $1, T_N, vnsrl)
	m_emit(vnsra,   bench_wxi, $1, T_N, vnsra)
	m_emit(vnclipu, bench_wxi, $1, T_N, vnclipu)
	m_emit(vnclip,  bench_wxi, $1, T_N, vnclip)

	m_emit(vwaddu, bench_vx, $1, T_W, vwaddu)
	m_emit(vwadd,  bench_vx, $1, T_W, vwadd)
	m_emit(vwsubu, bench_vx, $1, T_W, vwsubu)
	m_emit(vwsub,  bench_vx, $1, T_W, vwsub)
	m_emit(vwaddu, bench_wx, $1, T_W, vwaddu)
	m_emit(vwadd,  bench_wx, $1, T_W, vwadd)
	m_emit(vwsubu, bench_wx, $1, T_W, vwsubu)
	m_emit(vwsub,  bench_wx, $1, T_W, vwsub)
	m_emit(vwmulu,  bench_vx, $1, T_W, vwmulu)
	m_emit(vwmulsu, bench_vx, $1, T_W, vwmulsu)
	m_emit(vwmul,   bench_vx, $1, T_W, vwmul)
	m_emit(vwmaccu,  bench_xv, $1, T_W, vwmaccu)
	m_emit(vwmacc,   bench_xv, $1, T_W, vwmacc)
	m_emit(vwmaccsu, bench_xv, $1, T_W, vwmaccsu)
	m_emit(vwmaccus, mask, $1, bench_vwmaccusvx,  T_W, m_nop, vwmaccus.vx,  v8, t0, v16)

	m_emit(vfadd,   bench_vf, $1, T_F, vfadd)
	m_emit(vfsub,   bench_vf, $1, T_F, vfsub)
	m_emit(vfmin,   bench_vf, $1, T_F, vfmin)
	m_emit(vfmax,   bench_vf, $1, T_F, vfmax)
	m_emit(vfsgnj,  bench_vf, $1, T_F, vfsgnj)
	m_emit(vfsgnjn, bench_vf, $1, T_F, vfsgnjn)
	m_emit(vfsgnjx, bench_vf, $1, T_F, vfsgnjx)
	m_emit(vfslide1up,   mask, $1, bench_vfslide1upvf,   T_F, m_nop, vfslide1up.vf,   v8, v16, ft0)
	m_emit(vfslide1down, mask, $1, bench_vfslide1downvf, T_F, m_nop, vfslide1down.vf, v8, v16, ft0)

	m_emit(vfredusum, mask, $1, bench_vfredusumvs, T_F, m_nop, vfredusum.vs, v8, v16, v24)
	m_emit(vfredosum, mask, $1, bench_vfredosumvs, T_F, m_nop, vfredosum.vs, v8, v16, v24)
	m_emit(vfredmin,  mask, $1, bench_vfredminvs,  T_F, m_nop, vfredmin.vs,  v8, v16, v24)
	m_emit(vfredmax,  mask, $1, bench_vfredmaxvs,  T_F, m_nop, vfredmax.vs,  v8, v16, v24)

	m_emit(vfmerge, $1, bench_vfmergevfm, T_F, m_nop, vfmerge.vfm, v8,  v16, ft0, v0)
	m_emit(vfmv,    $1, bench_vfmvvf,     T_F, m_nop, vfmv.v.f,    v8,  ft0)

	m_emit(vmfeq, bench_vf, $1, T_F, vmfeq)
	m_emit(vmfle, bench_vf, $1, T_F, vmfle)
	m_emit(vmflt, bench_vf, $1, T_F, vmflt)
	m_emit(vmfne, bench_vf, $1, T_F, vmfne)
	m_emit(vmfgt, bench_vf, $1, T_F, vmfgt)
	m_emit(vmfge, bench_vf, $1, T_F, vmfge)

	m_emit(vfdiv,  bench_vf, $1, T_F, vfdiv)
	m_emit(vfrdiv, mask, $1, bench_vfrdivvf, T_F, m_nop, vfrdiv.vf, v8, v16, ft0)
	m_emit(vfmul,  bench_vf, $1, T_F, vfmul)
	m_emit(vfrsub, mask, $1, bench_vfrsubvf, T_F, m_nop, vfrsub.vf, v8, v16, ft0)
	m_emit(vfmadd, bench_fv, $1, T_F, vfmadd)
	m_emit(vfmsub, bench_fv, $1, T_F, vfmsub)
	m_emit(vfmacc, bench_fv, $1, T_F, vfmacc)
	m_emit(vfmsac, bench_fv, $1, T_F, vfmsac)

	m_emit(vfnmsac, bench_fv, $1, T_F, vfnmsac)
	m_emit(vfnmacc, bench_fv, $1, T_F, vfnmacc)
	m_emit(vfnmsub, bench_fv, $1, T_F, vfnmsub)
	m_emit(vfnmadd, bench_fv, $1, T_F, vfnmadd)

	m_emit(vwredsumu, mask, $1, bench_vwredsumuvs, T_WR, m_nop, vwredsumu.vs, v8, v16, v24)
	m_emit(vwredsum,  mask, $1, bench_vwredsumvs,  T_WR, m_nop, vwredsum.vs,  v8, v16, v24)

	m_emit(vfwadd, bench_vf, $1, T_FW, vfwadd)
	m_emit(vfwsub, bench_vf, $1, T_FW, vfwsub)
	m_emit(vfwadd, bench_wf, $1, T_FW, vfwadd)
	m_emit(vfwsub, bench_wf, $1, T_FW, vfwsub)
	m_emit(vfwmul, bench_vf, $1, T_FW, vfwmul)
	m_emit(vfwmacc,  bench_fv, $1, T_FW, vfwmacc)
	m_emit(vfwnmacc, bench_fv, $1, T_FW, vfwnmacc)
	m_emit(vfwmsac,  bench_fv, $1, T_FW, vfwmsac)
	m_emit(vfwnmsac, bench_fv, $1, T_FW, vfwnmsac)

	m_emit(vfwredosum, mask, $1, bench_vfwredosumvs, T_FWR, m_nop, vfwredosum.vs, v8, v16, v24)
	m_emit(vfwredusum, mask, $1, bench_vfwredusumvs, T_FWR, m_nop, vfwredusum.vs, v8, v16, v24)

	m_emit(vmv, $1, bench_vmvsx, T_A, m_nop, vmv.s.x, v8, t0)
	m_emit(vmv, $1, bench_vmvxs, T_A, m_nop, vmv.x.s, t0, v8)

	m_emit(vcpopm, mask, $1, bench_vcpopm,   T_m1,  m_nop,  vcpop.m,   t0, v8)
	m_emit(vfirst, mask, $1, bench_vfirstm,  T_m1,  m_1bit, vfirst.m,  t0, v8)
	m_emit(vzext,  mask, $1, bench_vzextvf2, T_VF2, m_1bit, vzext.vf2, v8, v16)
	m_emit(vsext,  mask, $1, bench_vsextvf2, T_VF2, m_1bit, vsext.vf2, v8, v16)
	m_emit(vzext,  mask, $1, bench_vzextvf4, T_VF4, m_1bit, vzext.vf4, v8, v16)
	m_emit(vsext,  mask, $1, bench_vsextvf4, T_VF4, m_1bit, vsext.vf4, v8, v16)
	m_emit(vzext,  mask, $1, bench_vzextvf8, T_VF8, m_1bit, vzext.vf8, v8, v16)
	m_emit(vsext,  mask, $1, bench_vsextvf8, T_VF8, m_1bit, vsext.vf8, v8, v16)

	m_emit(vfmv, $1, bench_vfmvfs, T_F, m_nop, vfmv.f.s, ft0, v8)
	m_emit(vfmv, $1, bench_vfmvsf, T_F, m_nop, vfmv.s.f, v8,  ft0)

	m_emit(vfcvt, mask, $1, bench_vfcvtxufv,    T_F, m_nop, vfcvt.xu.f.v,     v8, v16)
	m_emit(vfcvt, mask, $1, bench_vfcvtxfv,     T_F, m_nop, vfcvt.x.f.v,      v8, v16)
	m_emit(vfcvt, mask, $1, bench_vfcvtfxuv,    T_F, m_nop, vfcvt.f.xu.v,     v8, v16)
	m_emit(vfcvt, mask, $1, bench_vfcvtfxv,     T_F, m_nop, vfcvt.f.x.v,      v8, v16)
	m_emit(vfcvt, mask, $1, bench_vfcvtrtzxfv,  T_F, m_nop, vfcvt.rtz.x.f.v,  v8, v16)
	m_emit(vfcvt, mask, $1, bench_vfcvtrtzxufv, T_F, m_nop, vfcvt.rtz.xu.f.v, v8, v16)

	m_emit(vfwcvt, mask, $1, bench_vfwcvtxufv,    T_FW, m_nop, vfwcvt.xu.f.v,     v8, v16)
	m_emit(vfwcvt, mask, $1, bench_vfwcvtxfv,     T_FW, m_nop, vfwcvt.x.f.v,      v8, v16)
	m_emit(vfwcvt, mask, $1, bench_vfwcvtfxuv,    T_FW, m_nop, vfwcvt.f.xu.v,     v8, v16)
	m_emit(vfwcvt, mask, $1, bench_vfwcvtfxv,     T_FW, m_nop, vfwcvt.f.x.v,      v8, v16)
	m_emit(vfwcvt, mask, $1, bench_vfwcvtffv,     T_FW, m_nop, vfwcvt.f.f.v,      v8, v16)
	m_emit(vfwcvt, mask, $1, bench_vfwcvtrtzxufv, T_FW, m_nop, vfwcvt.rtz.xu.f.v, v8, v16)
	m_emit(vfwcvt, mask, $1, bench_vfwcvtrtzxfv,  T_FW, m_nop, vfwcvt.rtz.x.f.v,  v8, v16)

	m_emit(vfncvt, mask, $1, bench_vfncvtxufw,       T_FN, m_nop, vfncvt.xu.f.w,     v8, v16)
	m_emit(vfncvt, mask, $1, bench_vfncvtxfw,        T_FN, m_nop, vfncvt.x.f.w,      v8, v16)
	m_emit(vfncvt, mask, $1, bench_vfncvtfxuw,       T_FN, m_nop, vfncvt.f.xu.w,     v8, v16)
	m_emit(vfncvt, mask, $1, bench_vfncvtfxw,        T_FN, m_nop, vfncvt.f.x.w,      v8, v16)
	m_emit(vfncvt, mask, $1, bench_vfncvtffw,        T_FN, m_nop, vfncvt.f.f.w,      v8, v16)
	m_emit(vfncvt, mask, $1, bench_vfncvtrtzxfw,     T_FN, m_nop, vfncvt.rtz.x.f.w,  v8, v16)
	m_emit(vfncvt, mask, $1, bench_vfncvtrtzxufw,    T_FN, m_nop, vfncvt.rtz.xu.f.w, v8, v16)
	m_emit(vfncvt, mask, $1, bench_vfncvt.rod.f.f.w, T_FN, m_nop, vfncvt.rod.f.f.w,  v8, v16)

	m_emit(vfsqrt,   mask, $1, bench_vfsqrtv,   T_F, m_nop, vfsqrt.v,   v8, v16)
	m_emit(vfrsqrt7, mask, $1, bench_vfrsqrt7v, T_F, m_nop, vfrsqrt7.v, v8, v16)
	m_emit(vfrec7,   mask, $1, bench_vfrec7v,   T_F, m_nop, vfrec7.v,   v8, v16)
	m_emit(vfclass,  mask, $1, bench_vfclassv,  T_F, m_nop, vfclass.v,  v8, v16)

	m_emit(vmsbf, mask, $1, bench_vmsbfm,   T_A, m_1bit, vmsbf.m,   v8, v16)
	m_emit(vmsof, mask, $1, bench_vmsofm,   T_A, m_1bit, vmsof.m,   v8, v16)
	m_emit(vmsif, mask, $1, bench_vmsifm,   T_A, m_1bit, vmsif.m,   v8, v16)
	m_emit(viota, mask, $1, bench_viotam,   T_A, m_nop,  viota.m,   v8, v16)
	m_emit(vid,   mask, $1, bench_vidv,     T_A, m_nop,  vid.v,     v8)


	m_loadstore(mask, $1, T_E, e, v8, (t0))
	m_loadff(   $1, T_E, e, v8, (t0))

	m_emit(vlm, $1, bench_vlm, T_E8, m_mem8, vlm.v, v8, (t0))
	m_emit(vsm, $1, bench_vsm, T_E8, m_mem8, vsm.v, v8, (t0))

	m_loadstore(stride, $1, T_E, se, v8, (t2))

	m_loadstore(mask, $1, (T_A>> 4)&T_E, seg2e, v8, (t0))
	m_loadstore(mask, $1, (T_A>> 8)&T_E, seg3e, v8, (t0))
	m_loadstore(mask, $1, (T_A>> 8)&T_E, seg4e, v8, (t0))
	m_loadstore(mask, $1, (T_A>>12)&T_E, seg5e, v8, (t0))
	m_loadstore(mask, $1, (T_A>>12)&T_E, seg6e, v8, (t0))
	m_loadstore(mask, $1, (T_A>>12)&T_E, seg7e, v8, (t0))
	m_loadstore(mask, $1, (T_A>>12)&T_E, seg8e, v8, (t0))

	m_loadff(   $1, (T_A>> 4)&T_E, seg2e, v8, (t0))
	m_loadff(   $1, (T_A>> 8)&T_E, seg3e, v8, (t0))
	m_loadff(   $1, (T_A>> 8)&T_E, seg4e, v8, (t0))
	m_loadff(   $1, (T_A>>12)&T_E, seg5e, v8, (t0))
	m_loadff(   $1, (T_A>>12)&T_E, seg6e, v8, (t0))
	m_loadff(   $1, (T_A>>12)&T_E, seg7e, v8, (t0))
	m_loadff(   $1, (T_A>>12)&T_E, seg8e, v8, (t0))

	m_loadstore(stride, $1, (T_A>> 4)&T_E, sseg2e, v8, (t2))
	m_loadstore(stride, $1, (T_A>> 8)&T_E, sseg3e, v8, (t2))
	m_loadstore(stride, $1, (T_A>> 8)&T_E, sseg4e, v8, (t2))
	m_loadstore(stride, $1, (T_A>>12)&T_E, sseg5e, v8, (t2))
	m_loadstore(stride, $1, (T_A>>12)&T_E, sseg6e, v8, (t2))
	m_loadstore(stride, $1, (T_A>>12)&T_E, sseg7e, v8, (t2))
	m_loadstore(stride, $1, (T_A>>12)&T_E, sseg8e, v8, (t2))


#if __riscv_zvbb
	m_emit(vandn, bench_vx, $1, T_A, vandn)
	m_emit(vbrev,  mask, $1, bench_vbrevv,  T_A, m_nop, vbrev.v,  v8, v16)
	m_emit(vbrev8, mask, $1, bench_vbrev8v, T_A, m_nop, vbrev8.v, v8, v16)
	m_emit(vclz,   mask, $1, bench_vclzv,   T_A, m_nop, vclz.v,   v8, v16)
	m_emit(vctz,   mask, $1, bench_vctzv,   T_A, m_nop, vctz.v,   v8, v16)
	m_emit(vcpopv, mask, $1, bench_vcpopv,  T_A, m_nop, vcpop.v,  v8, v16)
	m_emit(vrol, bench_vx, $1, T_A, vrol)
	m_emit(vror, bench_vx, $1, T_A, vror)
	m_emit(vwsll, bench_vx, $1, T_W, vwsll)
#endif

#if __riscv_zvbc
	m_emit(vclmul,  bench_vx, $1, T_64, vclmul)
	m_emit(vclmulh, bench_vx, $1, T_64, vclmulh)
#endif

#if __riscv_zvkg
	m_emit(vghsh, $1, bench_vghsh, T_GSH, m_nop, vghsh.vv, v8, v16, v24)
	m_emit(vgmul, $1, bench_vgmul, T_GSH, m_nop, vgmul.vv, v8, v16)
#endif

#if __riscv_zvkned
	m_emit(vaesem, $1, bench_vaesemvv,  T_AES,  m_nop, vaesem.vv,  v8, v16)
	m_emit(vaesem, $1, bench_vaesemvs,  T_AES,  m_nop, vaesem.vs,  v8, v16)
	m_emit(vaesdm, $1, bench_vaesdmvv,  T_AES,  m_nop, vaesdm.vv,  v8, v16)
	m_emit(vaesdm, $1, bench_vaesdmvs,  T_AES,  m_nop, vaesdm.vs,  v8, v16)
	m_emit(vaesef, $1, bench_vaesefvv,  T_AES,  m_nop, vaesef.vv,  v8, v16)
	m_emit(vaesef, $1, bench_vaesefvs,  T_AES,  m_nop, vaesef.vs,  v8, v16)
	m_emit(vaesdf, $1, bench_vaesdfvv,  T_AES,  m_nop, vaesdf.vv,  v8, v16)
	m_emit(vaesdf, $1, bench_vaesdfvs,  T_AES,  m_nop, vaesdf.vs,  v8, v16)
	m_emit(vaeskf1, $1, bench_vaeskf1vi, T_AES,  m_nop, vaeskf1.vi, v8, v16, 3)
	m_emit(vaeskf2, $1, bench_vaeskf2vi, T_AES2, m_nop, vaeskf2.vi, v8, v16, 3)
	m_emit(vaesz, $1, bench_vaeszvs,   T_AES,  m_nop, vaesz.vs,   v8, v16)
#endif

#if __riscv_zvknha || __riscv_zvknhb
	m_emit(vsha2ms, $1, bench_vsha2msvv, T_SHA,  m_nop, vsha2ms.vv,  v8, v16, v24)
	m_emit(vsha2ch, $1, bench_vsha2chvv, T_SHA,  m_nop, vsha2ch.vv,  v8, v16, v24)
	m_emit(vsha2cl, $1, bench_vsha2clvv, T_SHA,  m_nop, vsha2cl.vv,  v8, v16, v24)
#endif

')


/* TODO: indexed load/stores */

.data


#if __riscv_xlen == 32
#define defptr .word
#else
#define defptr .dword
#endif

define(`m_gen_function_pointers', `m_emit_case($4, `defptr $1_m`'m_benchLMUL')')
define(`m_gen_types', `m_emit_case($4, `defptr $2')')
define(`m_format',`$2 shift2($*)')
define(`m_gen_strings', `m_emit_case($4, `.string "m_format(,shift3($*))"')')

.balign 8
.global aligned
aligned:
defptr 0
.global misaligned
misaligned:
defptr 0


.balign 8
define(`m_LMUL', f8)
define(`m_benchLMUL', 1)
.global bench_m`'m_LMUL
	bench_m`'m_LMUL:
	m_bench_all(`gen_function_pointers')
	defptr 0

.balign 8
define(`m_LMUL', f4)
define(`m_benchLMUL', 1)
.global bench_m`'m_LMUL
	bench_m`'m_LMUL:
	m_bench_all(`gen_function_pointers')
	defptr 0

.balign 8
define(`m_LMUL', f2)
define(`m_benchLMUL', 1)
.global bench_m`'m_LMUL
	bench_m`'m_LMUL:
	m_bench_all(`gen_function_pointers')
	defptr 0

.balign 8
define(`m_LMUL', 1)
define(`m_benchLMUL', 1)
.global bench_m`'m_LMUL
	bench_m`'m_LMUL:
	m_bench_all(`gen_function_pointers')
	defptr 0

.balign 8
define(`m_LMUL', 2)
define(`m_benchLMUL', 2)
.global bench_m`'m_LMUL
	bench_m`'m_LMUL:
	m_bench_all(`gen_function_pointers')
	defptr 0

.balign 8
define(`m_LMUL', 4)
define(`m_benchLMUL', 4)
.global bench_m`'m_LMUL
	bench_m`'m_LMUL:
	m_bench_all(`gen_function_pointers')
	defptr 0

.balign 8
define(`m_LMUL', 8)
define(`m_benchLMUL', 8)
.global bench_m`'m_LMUL
	bench_m`'m_LMUL:
	m_bench_all(`gen_function_pointers')
	defptr 0

pushdef(`m_ifmx',`m_ifmx_t($*)')

.balign 8
.global bench_types
	bench_types:
	m_bench_all(`gen_types')
	defptr 0

.balign 8
.global bench_names
	bench_names:
	m_bench_all(`gen_strings')
	defptr 0

popdef(`m_ifmx')


.balign 8
u64_cycle:
.dword 0

.text
.balign 8


/* x off */
define(`m_offset1',
	`ifelse(`$1',`t0',t$2,
	 ifelse(`$1',`ft0',ft$2,
	 ifelse(`$1',`v8',v`'eval(8+$2),
	 ifelse(`$1',v16,v`'eval(16+$2),
	 ifelse(`$1',v24,v`'eval(24+$2),$1)))))')
/* acc off code:vararg */
define(`m_offset',
	`ifelse(`$3',,`m_format($1)',
		`m_offset(`$1,m_offset1($3,$2)',$2,$4,$5,$6,$7,$8,$9)')')

define(`m_gen_code_m1',
	`m_format(,$*)
m_offset(,1,$*)
m_offset(,2,$*)
m_offset(,3,$*)
m_offset(,4,$*)
m_offset(,5,$*)
m_offset(,6,$*)
m_offset(,7,$*)')

define(`m_gen_code_m2',
	`m_format(,$*)
m_offset(,2,$*)
m_offset(,4,$*)
m_offset(,6,$*)
m_format(,$*)
m_offset(,2,$*)
m_offset(,4,$*)
m_offset(,6,$*)')

define(`m_gen_code_m4',
	`m_format(,$*)
m_offset(,4,$*)
m_format(,$*)
m_offset(,4,$*)
m_format(,$*)
m_offset(,4,$*)
m_format(,$*)
m_offset(,4,$*)')

define(`m_gen_code_m8',
	`m_format(,$*)
m_format(,$*)
m_format(,$*)
m_format(,$*)
m_format(,$*)
m_format(,$*)
m_format(,$*)
m_format(,$*)')


/* name type setup code:vararg */
define(`m_gen_bench_body',`
$1`'_m`'m_LMUL:	pushdef(`m_code', `shift3($*)')
	$3
	li a0, WARMUP
1:
ifelse(m_LMUL,1,`m_gen_code_m1(m_code)')
ifelse(m_LMUL,2,`m_gen_code_m2(m_code)')
ifelse(m_LMUL,4,`m_gen_code_m4(m_code)')
ifelse(m_LMUL,8,`m_gen_code_m8(m_code)')
	addi a0, a0, -1
	bnez a0, 1b
#if defined(USE_PERF_EVENT_SLOW)
	ld a0, nolibc_perf_event_fd
	la a1, u64_cycle
	li a2, 8
	li a7, 63
	ecall /* clobbers vregs and vtype */
	ld a3, u64_cycle
	vsetvl VL, VL, s2
	bnez VL, 2f
	vsetvl VL, x0, s2
	2:
#elif defined(READ_MCYCLE)
	csrr a3, mcycle
#else
	csrr a3, cycle
#endif
	li a0, LOOP
1:
.rept UNROLL
ifelse(m_LMUL,1,`m_gen_code_m1(m_code)')
ifelse(m_LMUL,2,`m_gen_code_m2(m_code)')
ifelse(m_LMUL,4,`m_gen_code_m4(m_code)')
ifelse(m_LMUL,8,`m_gen_code_m8(m_code)')
.endr
	addi a0, a0, -1
	bnez a0, 1b
#if defined(USE_PERF_EVENT_SLOW)
	ld a0, nolibc_perf_event_fd
	la a1, u64_cycle
	li a2, 8
	li a7, 63
	ecall /* clobbers vregs and vtype */
	ld a0, u64_cycle
	vsetvl VL, VL, s2
	bnez VL, 2f
	vsetvl VL, x0, s2
	2:
#elif defined(READ_MCYCLE)
	csrr a0, mcycle
#else
	csrr a0, cycle
#endif
	sub a0, a0, a3
ret	popdef(`m_code')
')

define(`m_gen_bench', `m_emit_case($4, `m_gen_bench_body($*)')')

define(`m_LMUL', 1)
m_bench_all(`gen_bench')
define(`m_LMUL', 2)
m_bench_all(`gen_bench')
define(`m_LMUL', 4)
m_bench_all(`gen_bench')
define(`m_LMUL', 8)
m_bench_all(`gen_bench')


randomize:
	/* fill v0/v8/v16/v24 with all-ones; seed in a0 unused */
	vsetvli a4, x0, e8, m8, ta, ma
	vmv.v.i v0, -1
	vmv.v.i v8, -1
	vmv.v.i v16, -1
	vmv.v.i v24, -1
	li t0, -1
	li t1, -1
	li t2, -1
	li t3, -1
	li t4, -1
	li t5, -1
	li t6, -1
	li t7, -1
	li t8, -1
	fmv.w.x ft0, t0
	fmv.w.x ft1, t0
	fmv.w.x ft2, t0
	fmv.w.x ft3, t0
	fmv.w.x ft4, t0
	fmv.w.x ft5, t0
	fmv.w.x ft6, t0
	fmv.w.x ft7, t0
	fmv.w.x ft8, t0
	ret

/* u64 f(u64 (*bench)(void), u64 type, u64 vl, u64 seed) */
.global run_bench
run_bench:
	addi sp, sp, -56
	sx ra, 8(sp)
	sx VL, 16(sp)
	sx s1, 24(sp)
	sx s2, 32(sp)
	sx t7, 40(sp)
	sx t8, 48(sp)

	mv s1, a0
	mv s2, a1 /* type */
	mv VL, a2
	mv a0, a3 /* seed */
	call randomize
	vsetvl VL, VL, s2
	bnez VL, 1f
	vsetvl VL, x0, s2
	1:
	jalr s1

	lx ra, 8(sp)
	lx VL, 16(sp)
	lx s1, 24(sp)
	lx s2, 32(sp)
	lx t7, 40(sp)
	lx t8, 48(sp)
	addi sp, sp, 56
	ret
