#ifndef AV_RUNTIME_H
#define AV_RUNTIME_H
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <sys/time.h>
#ifdef __cplusplus
extern "C" {
#endif
int printf_(const char *, ...);
void av_begin(const char *name);
void av_end(const char *name);
uint64_t av_ticks(void);
void av_observe(double value);
#ifdef __cplusplus
}
#endif
#define printf printf_
#endif
