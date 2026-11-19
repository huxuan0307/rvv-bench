/* Paired exact-input probes for acos (protocol=2).
 *
 * The frozen corpus header of this function is the only input source; the
 * probe bodies and the case table live in paired_probe_impl.inc so the four
 * functions cannot drift apart in protocol details.
 */
#define PAIRED_FN acos
#define PAIRED_FN_STR "acos"
#define PAIRED_BINARY 0
#define PAIRED_DATA_HEADER "acos.h"

#define PAIRED_GROUP pair_group_acos
#define PAIRED_TABLE pair_acos_cases
#define PAIRED_SEED_INIT pair_acos_seed_init
#define PAIRED_SEED0 pair_acos_seed0_ptr
#define PAIRED_SEED1 pair_acos_seed1_ptr
#define PAIRED_SEED_COUNT pair_acos_seed_count
#define PAIRED_SEED_FNV pair_acos_seed_fnv
#define PAIRED_PAIR_FNV pair_acos_pair_fnv

#include "paired_probe_impl.inc"
