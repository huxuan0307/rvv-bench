/* Shared register core shells; no array bridge occurs on the Nx hot path.
   Native RVV arguments/return use the vector PCS. Nx always processes VLMAX. */
__attribute__((noinline,noclone))
void ENTRY(size_t n, const REAL_TYPE *x, const REAL_TYPE *unused, REAL_TYPE *y)
{
    (void)unused;
    for (size_t pos=0; pos<n;) {
        size_t vl=SETVL(n-pos);
        FV input=ENTRY_LOAD(x+pos,vl);
        FV output=CORE(input,vl);
        ENTRY_STORE(y+pos,output,vl);
        pos+=vl;
    }
}
__attribute__((noinline,noclone,riscv_vector_cc))
FV NX(FV input)
{
    size_t vl=SETVLMAX();
    return CORE(input,vl);
}
