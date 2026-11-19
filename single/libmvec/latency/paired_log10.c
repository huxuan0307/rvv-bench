/* Paired exact-input probes for log10 (protocol=2).
 *
 * The frozen corpus header of this function is the only input source; the
 * probe bodies and the case table live in paired_probe_impl.inc so the four
 * functions cannot drift apart in protocol details.
 */
#define PAIRED_FN log10
#define PAIRED_FN_STR "log10"
#define PAIRED_BINARY 0
#define PAIRED_DATA_HEADER "log10.h"

#define PAIRED_GROUP pair_group_log10
#define PAIRED_TABLE pair_log10_cases
#define PAIRED_SEED_INIT pair_log10_seed_init
#define PAIRED_SEED0 pair_log10_seed0_ptr
#define PAIRED_SEED1 pair_log10_seed1_ptr
#define PAIRED_SEED_COUNT pair_log10_seed_count
#define PAIRED_SEED_FNV pair_log10_seed_fnv
#define PAIRED_PAIR_FNV pair_log10_pair_fnv

#include "paired_probe_impl.inc"
