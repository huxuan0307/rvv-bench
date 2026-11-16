/* Control the benchmark environment: check/set/restore RNE once outside the
   measured main, never once per timed array or per VLMAX batch. This wrapper
   does not define the math library's final caller rounding-mode contract. */
extern int __real_main(void);
int __wrap_main(void){
 unsigned old;__asm__ volatile("frrm %0":"=r"(old)::"memory");
 if(old)__asm__ volatile("fsrmi 0":::"memory");
 int result=__real_main();
 if(old)__asm__ volatile("fsrm %0"::"r"(old):"memory");
 return result;
}
