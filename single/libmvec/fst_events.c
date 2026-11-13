/* Export selected FST handles as time/handle/binary-value events. */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include "fstapi.h"
static void event(void *unused,uint64_t t,fstHandle h,const unsigned char *v) {
    (void)unused;printf("%llu\t%u\t%s\n",(unsigned long long)t,h,v);
}
int main(int argc,char **argv) {
    if(argc<5)return 2;
    fstReaderContext *r=fstReaderOpen(argv[1]);if(!r)return 1;
    fstReaderSetLimitTimeRange(r,strtoull(argv[2],0,0),strtoull(argv[3],0,0));
    for(int i=4;i<argc;++i)fstReaderSetFacProcessMask(r,strtoul(argv[i],0,0));
    int ok=fstReaderIterBlocks(r,event,0,0);fstReaderClose(r);return ok?0:1;
}
