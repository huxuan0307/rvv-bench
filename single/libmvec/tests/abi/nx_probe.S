/* Scalar-ABI test callers for unmasked vector-PCS entry points.
   a0=entry, a1=x, a2=y, a3=out, a4=incoming_vl,
   a5=15*vlenb register snapshot, a6=state[vl,vtype,vstart,frm].
   Every v1-v7 and v24-v31 byte is checked by the C driver. */
.text
.macro NX_PROBE name, sew, lm, second
.globl \name
.type \name,@function
\name:
 addi sp,sp,-80
 sd ra,0(sp)
 sd s0,8(sp)
 sd s1,16(sp)
 sd s2,24(sp)
 sd s3,32(sp)
 sd s4,40(sp)
 sd s5,48(sp)
 sd s6,56(sp)
 mv s0,a0
 mv s1,a1
 mv s2,a2
 mv s3,a3
 mv s4,a4
 mv s5,a5
 mv s6,a6
 vsetvli t0,zero,e8,m1,tu,mu
 .irp r,1,2,3,4,5,6,7,24,25,26,27,28,29,30,31
 li t0,(\r * 7 + 3)
 vmv.v.x v\r,t0
 .endr
 vsetvli t0,zero,e\sew,m\lm,tu,mu
 vle\sew\().v v8,(s1)
 vle\sew\().v v\second,(s2)
 vsetvli zero,s4,e\sew,m\lm,tu,mu
 jalr ra,s0,0
 csrr t0,vl
 sd t0,0(s6)
 csrr t0,vtype
 sd t0,8(s6)
 csrr t0,vstart
 sd t0,16(s6)
 frrm t0
 sd t0,24(s6)
 vsetvli t0,zero,e\sew,m\lm,tu,mu
 vse\sew\().v v8,(s3)
 csrr t1,vlenb
 vsetvli t0,zero,e8,m1,tu,mu
 .irp r,1,2,3,4,5,6,7,24,25,26,27,28,29,30,31
 vse8.v v\r,(s5)
 add s5,s5,t1
 .endr
 ld ra,0(sp)
 ld s0,8(sp)
 ld s1,16(sp)
 ld s2,24(sp)
 ld s3,32(sp)
 ld s4,40(sp)
 ld s5,48(sp)
 ld s6,56(sp)
 addi sp,sp,80
 ret
.size \name,.-\name
.endm
NX_PROBE nx_probe_f64m1,64,1,9
NX_PROBE nx_probe_f64m2,64,2,10
NX_PROBE nx_probe_f32m1,32,1,9
NX_PROBE nx_probe_f32m2,32,2,10
.section .note.GNU-stack,"",@progbits
