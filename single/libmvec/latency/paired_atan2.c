/* Paired exact-input probes for atan2 (protocol=2).
 *
 * The frozen corpus header of this function is the only input source; the
 * probe bodies and the case table live in paired_probe_impl.inc so the four
 * functions cannot drift apart in protocol details.
 */
#define PAIRED_FN atan2
#define PAIRED_FN_STR "atan2"
#define PAIRED_BINARY 1
#define PAIRED_DATA_HEADER "atan2.h"

#define PAIRED_GROUP pair_group_atan2
#define PAIRED_TABLE pair_atan2_cases
#define PAIRED_SEED_INIT pair_atan2_seed_init
#define PAIRED_SEED0 pair_atan2_seed0_ptr
#define PAIRED_SEED1 pair_atan2_seed1_ptr
#define PAIRED_SEED_COUNT pair_atan2_seed_count
#define PAIRED_SEED_FNV pair_atan2_seed_fnv
#define PAIRED_PAIR_FNV pair_atan2_pair_fnv

#include "paired_probe_impl.inc"
