/* The array kernels require RNE. Check/set/restore frm once outside frozen
   benchmark main, never once per timed array or per VLMAX batch. */
extern int __real_main(void);
int __wrap_main(void){
 unsigned old;__asm__ volatile("frrm %0":"=r"(old)::"memory");
 if(old)__asm__ volatile("fsrmi 0":::"memory");
 int result=__real_main();
 if(old)__asm__ volatile("fsrm %0"::"r"(old):"memory");
 return result;
}
