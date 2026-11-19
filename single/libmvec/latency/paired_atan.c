/* Paired exact-input probes for atan (protocol=2).
 *
 * The frozen corpus header of this function is the only input source; the
 * probe bodies and the case table live in paired_probe_impl.inc so the four
 * functions cannot drift apart in protocol details.
 */
#define PAIRED_FN atan
#define PAIRED_FN_STR "atan"
#define PAIRED_BINARY 0
#define PAIRED_DATA_HEADER "atan.h"

#define PAIRED_GROUP pair_group_atan
#define PAIRED_TABLE pair_atan_cases
#define PAIRED_SEED_INIT pair_atan_seed_init
#define PAIRED_SEED0 pair_atan_seed0_ptr
#define PAIRED_SEED1 pair_atan_seed1_ptr
#define PAIRED_SEED_COUNT pair_atan_seed_count
#define PAIRED_SEED_FNV pair_atan_seed_fnv
#define PAIRED_PAIR_FNV pair_atan_pair_fnv

#include "paired_probe_impl.inc"
