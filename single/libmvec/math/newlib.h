/* Experimental scalar-derived RVV math, LGPL-2.1-or-later.
   RNE is a caller precondition; no entry accesses frm.
   Array n==0 accepts null pointers. Exact in-place arrays are supported;
   partial overlap is not. Unary entries ignore the third argument.
   Nx entries process the complete VLMAX regardless of incoming vl. */
#ifndef EXPERIMENTAL_SHARED_NEWLIB_H
#define EXPERIMENTAL_SHARED_NEWLIB_H
#include <stddef.h>
#include <riscv_vector.h>
#ifdef __cplusplus
extern "C" {
#endif
#define NEWLIB_ARRAY(name,type) \
void name##vec_m1(size_t,const type*,const type*,type*); \
void name##vec_m2(size_t,const type*,const type*,type*)
NEWLIB_ARRAY(log,double);
NEWLIB_ARRAY(exp,double);
NEWLIB_ARRAY(sin,double);
NEWLIB_ARRAY(cos,double);
NEWLIB_ARRAY(pow,double);
NEWLIB_ARRAY(expf,float);
NEWLIB_ARRAY(logf,float);
#undef NEWLIB_ARRAY
#define NEWLIB_NX_UNARY(name,type1,type2) \
__attribute__((riscv_vector_cc)) type1 _ZGV1Nxv_##name(type1); \
__attribute__((riscv_vector_cc)) type2 _ZGV2Nxv_##name(type2)
NEWLIB_NX_UNARY(log,vfloat64m1_t,vfloat64m2_t);
NEWLIB_NX_UNARY(exp,vfloat64m1_t,vfloat64m2_t);
NEWLIB_NX_UNARY(sin,vfloat64m1_t,vfloat64m2_t);
NEWLIB_NX_UNARY(cos,vfloat64m1_t,vfloat64m2_t);
NEWLIB_NX_UNARY(logf,vfloat32m1_t,vfloat32m2_t);
NEWLIB_NX_UNARY(expf,vfloat32m1_t,vfloat32m2_t);
#undef NEWLIB_NX_UNARY
__attribute__((riscv_vector_cc)) vfloat64m1_t _ZGV1Nxvv_pow(vfloat64m1_t,vfloat64m1_t);
__attribute__((riscv_vector_cc)) vfloat64m2_t _ZGV2Nxvv_pow(vfloat64m2_t,vfloat64m2_t);
#ifdef __cplusplus
}
#endif
#endif
