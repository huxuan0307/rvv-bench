/* LMUL8 shell for a flt-32 kernel.  A single LMUL8 f32 work vector would need
   an LMUL16 f64 intermediate, which RVV does not provide, so the shell splits
   the LMUL8 input into two LMUL4 halves and runs the native LMUL4 core on each.
   Register pressure stays at the LMUL4 level; the only gain is one wider
   load/store pair per iteration. */
#include <riscv_vector.h>

__attribute__((noinline,noclone,aligned(64)))
void LOGFVEC_M8_ENTRY(size_t n, const float *x, const float *unused, float *y)
{
    (void)unused;
    for (size_t pos=0; pos<n;) {
        size_t vl=__riscv_vsetvl_e32m8(n-pos);
        vfloat32m8_t input=__riscv_vle32_v_f32m8(x+pos,vl);
        size_t vh=__riscv_vsetvlmax_e32m4();
        size_t v0=vl<vh?vl:vh, v1=vl>vh?vl-vh:0;
        vfloat32m8_t output=input;
        output=__riscv_vset_v_f32m4_f32m8(output,0,
            LOGFVEC_CORE_M4(__riscv_vget_v_f32m8_f32m4(input,0),v0));
        if (v1)
            output=__riscv_vset_v_f32m4_f32m8(output,1,
                LOGFVEC_CORE_M4(__riscv_vget_v_f32m8_f32m4(input,1),v1));
        __riscv_vse32_v_f32m8(y+pos,output,vl);
        pos+=vl;
    }
}

__attribute__((noinline,noclone,riscv_vector_cc))
vfloat32m8_t LOGFVEC_M8_SYMBOL(vfloat32m8_t input)
{
    size_t vh=__riscv_vsetvlmax_e32m4();
    vfloat32m8_t output=input;
    output=__riscv_vset_v_f32m4_f32m8(output,0,
        LOGFVEC_CORE_M4(__riscv_vget_v_f32m8_f32m4(input,0),vh));
    output=__riscv_vset_v_f32m4_f32m8(output,1,
        LOGFVEC_CORE_M4(__riscv_vget_v_f32m8_f32m4(input,1),vh));
    return output;
}
