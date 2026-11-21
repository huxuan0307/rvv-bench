/* Paired exact-input probes for binary64 exp (protocol 2). */
#define PAIRED_FN exp
#define PAIRED_FN_STR "exp"
#define PAIRED_BINARY 0
#define PAIRED_DATA_HEADER "exp.h"

#define PAIRED_GROUP pair_group_exp
#define PAIRED_TABLE pair_exp_cases
#define PAIRED_SEED_INIT pair_exp_seed_init
#define PAIRED_SEED0 pair_exp_seed0_ptr
#define PAIRED_SEED1 pair_exp_seed1_ptr
#define PAIRED_SEED_COUNT pair_exp_seed_count
#define PAIRED_SEED_FNV pair_exp_seed_fnv
#define PAIRED_PAIR_FNV pair_exp_pair_fnv

#include "paired_probe_impl.inc"
