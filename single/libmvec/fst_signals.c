/* List FST hierarchy for instruction-level waveform investigation. */
#include <stdio.h>
#include <string.h>
#include "fstapi.h"
int main(int argc,char **argv) {
    if(argc!=2)return 2;
    fstReaderContext *r=fstReaderOpen(argv[1]);if(!r)return 1;
    char scope[32768]="";size_t lengths[256];unsigned depth=0;
    struct fstHier *h;
    while((h=fstReaderIterateHier(r))) {
        if(h->htyp==FST_HT_SCOPE) {
            if(depth>=256)return 3;
            lengths[depth++]=strlen(scope);
            if(strlen(scope)+strlen(h->u.scope.name)+2>=sizeof scope)return 3;
            strcat(scope,h->u.scope.name);strcat(scope,".");
        } else if(h->htyp==FST_HT_UPSCOPE) {
            if(depth)scope[lengths[--depth]]=0;
        } else if(h->htyp==FST_HT_VAR) {
            printf("%u\t%u\t%s%s\n",h->u.var.handle,h->u.var.length,scope,h->u.var.name);
        }
    }
    fstReaderClose(r);return 0;
}
