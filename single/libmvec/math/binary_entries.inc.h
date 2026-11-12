/* Shared binary register core shells. Exact array y==x or y==z is safe. */
__attribute__((noinline,noclone))
void ENTRY(size_t n, const double *x, const double *z, double *y)
{
    for (size_t pos=0; pos<n;) {
        size_t vl=SETVL(n-pos);
        FV input_x=ENTRY_LOAD(x+pos,vl);
        FV input_z=ENTRY_LOAD(z+pos,vl);
        FV output=CORE(input_x,input_z,vl);
        ENTRY_STORE(y+pos,output,vl);
        pos+=vl;
    }
}
__attribute__((noinline,noclone,riscv_vector_cc))
FV NX(FV input_x, FV input_z)
{
    size_t vl=SETVLMAX();
    return CORE(input_x,input_z,vl);
}
