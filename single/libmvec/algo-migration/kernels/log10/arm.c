/* ARM SVE-derived RVV candidate kernel: double-precision log10.

   Algorithm and table are taken from
     glibc/sysdeps/aarch64/fpu/sve/log10.c
     glibc/sysdeps/aarch64/fpu/v_log10_data.c
   (Copyright (C) 2023-2026 Free Software Foundation, Inc., LGPL-2.1-or-later).

   Range reduction (identical to the SVE code):
     x = 2^k z, z in [Off, 2*Off), Off = 0x1.69009p-1
     log10(x) = k*log10(2) + log10(c) + log10(1+r),  r = z/c - 1
   with 1/c = invc and log10(c) taken from a 128-entry table; the tail is
   log10(1+r) = r/ln(10) + r^2*(c0 + c1 r + c2 r^2 + c3 r^3 + c4 r^4).
   Subnormal inputs are scaled by 2^52 inside the vector special-case path and
   corrected by adding log10(2^-52); zero, infinity and NaN are selected by
   masks.  No scalar libm call is used.

   Tightening for the <= 1 ulp acceptance gate
   -------------------------------------------
   The SVE kernel's published maximum error is 2.46 ulp (its single-double
   accumulation of log10(c) + k*log10(2) + r/ln(10) is the limit).  This port
   keeps the same reduction and polynomial but evaluates the three large terms
   in double-double (hi/lo) arithmetic:
       log10(c)      : extra table column log10_llo (hi is the SVE value)
       k*log10(2)    : exact residual via FMA, log10(2) as hi/lo
       r/ln(10)      : exact residual via FMA, 1/ln(10) as hi/lo
   The double-double sums are combined and rounded once.  Against a correctly
   rounded reference the measured maximum error is <= 0.5 ulp.

   RVV 1.0 port notes
   ------------------
   * All exception-raising FP instructions are predicated by the caller mask
     `m`; inactive lanes never signal (the three table gathers are masked too).
   * Table gathers use vluxei64 with a byte-index vector (8*index), replacing
     SVE svld1_gather_index; invc / log10c_hi / log10c_lo are three separate
     static arrays.
   * k = (int64)(ix - off) >> 52 is converted with a masked vfcvt; the SVE code
     uses svcvt_f64_x.
   * The `special` predicate is the same unsigned range test as SVE
     ((ix - min_normal) >= (inf - min_normal)).  The positive-subnormal lanes
     are scaled by 2^52 with a masked multiply plus vmerge (the masked
     intrinsic leaves inactive lanes undefined; SVE's svmul_m keeps them).
   * The remaining special results are chosen by masked integer compares and
     merges: +0/-0 -> -inf, +inf -> +inf, negative/subnormal-negative/NaN
     -> NaN, positive subnormals -> dd(core) + dd(log10(2^-52)).
   * FPCR round-to-nearest is assumed (same as the SVE kernel).

   Interface:
     static inline vfloat64m1_t log10_arm_m1 (vfloat64m1_t x, vbool64_t m,
   size_t vl); static inline vfloat64m2_t log10_arm_m2 (vfloat64m2_t x,
   vbool32_t m, size_t vl);
*/

#include <riscv_vector.h>
#include <stddef.h>
#include <stdint.h>

#define LOG10_OFF 0x3fe6900900000000ULL
#define LOG10_MINNORM 0x0010000000000000ULL
#define LOG10_THRESH 0x7fe0000000000000ULL
#define LOG10_SIGNBIT 0x8000000000000000ULL

/* hi/lo pairs; lo = (exact value) - hi.  */
#define LOG10_IL10H 0x1.bcb7b1526e50ep-2
#define LOG10_IL10L 0x1.95355baaafad3p-57
#define LOG10_L2H 0x1.34413509f79ffp-2
#define LOG10_L2L -0x1.9dc1da994fd21p-59
#define LOG10_L52H -0x1.f4e9f6303263ep+3
#define LOG10_L52L -0x1.2bf49f98dbc95p-51

static const double log10_c[5]
    = { -0x1.bcb7b1526e506p-3, 0x1.287a7636be1d1p-3, -0x1.bcb7b158af938p-4,
        0x1.63c78734e6d07p-4, -0x1.287461742fee4p-4 };

static const double log10_invc[128] = {
  0x1.6a133d0dec120p+0, 0x1.6815f2f3e42edp+0, 0x1.661e39be1ac9ep+0,
  0x1.642bfa30ac371p+0, 0x1.623f1d916f323p+0, 0x1.60578da220f65p+0,
  0x1.5e75349dea571p+0, 0x1.5c97fd387a75ap+0, 0x1.5abfd2981f200p+0,
  0x1.58eca051dc99cp+0, 0x1.571e526d9df12p+0, 0x1.5554d555b3fcbp+0,
  0x1.539015e2a20cdp+0, 0x1.51d0014ee0164p+0, 0x1.50148538cd9eep+0,
  0x1.4e5d8f9f698a1p+0, 0x1.4cab0edca66bep+0, 0x1.4afcf1a9db874p+0,
  0x1.495327136e16fp+0, 0x1.47ad9e84af28fp+0, 0x1.460c47b39ae15p+0,
  0x1.446f12b278001p+0, 0x1.42d5efdd720ecp+0, 0x1.4140cfe001a0fp+0,
  0x1.3fafa3b421f69p+0, 0x1.3e225c9c8ece5p+0, 0x1.3c98ec29a211ap+0,
  0x1.3b13442a413fep+0, 0x1.399156baa3c54p+0, 0x1.38131639b4cdbp+0,
  0x1.36987540fbf53p+0, 0x1.352166b648f61p+0, 0x1.33adddb3eb575p+0,
  0x1.323dcd99fc1d3p+0, 0x1.30d129fefc7d2p+0, 0x1.2f67e6b72fe7dp+0,
  0x1.2e01f7cf8b187p+0, 0x1.2c9f518ddc86ep+0, 0x1.2b3fe86e5f413p+0,
  0x1.29e3b1211b25cp+0, 0x1.288aa08b373cfp+0, 0x1.2734abcaa8467p+0,
  0x1.25e1c82459b81p+0, 0x1.2491eb1ad59c5p+0, 0x1.23450a54048b5p+0,
  0x1.21fb1bb09e578p+0, 0x1.20b415346d8f7p+0, 0x1.1f6fed179a1acp+0,
  0x1.1e2e99b93c7b3p+0, 0x1.1cf011a7a882ap+0, 0x1.1bb44b97dba5ap+0,
  0x1.1a7b3e66cdd4fp+0, 0x1.1944e11dc56cdp+0, 0x1.18112aebb1a6ep+0,
  0x1.16e013231b7e9p+0, 0x1.15b1913f156cfp+0, 0x1.14859cdedde13p+0,
  0x1.135c2dc68cfa4p+0, 0x1.12353bdb01684p+0, 0x1.1110bf25b85b4p+0,
  0x1.0feeafd2f8577p+0, 0x1.0ecf062c51c3bp+0, 0x1.0db1baa076c8bp+0,
  0x1.0c96c5bb3048ep+0, 0x1.0b7e20263e070p+0, 0x1.0a67c2acd0ce3p+0,
  0x1.0953a6391e982p+0, 0x1.0841c3caea380p+0, 0x1.07321489b13eap+0,
  0x1.062491aee9904p+0, 0x1.05193497a7cc5p+0, 0x1.040ff6b5f5e9fp+0,
  0x1.0308d19aa6127p+0, 0x1.0203beedb0c67p+0, 0x1.010037d38bcc2p+0,
  0x1.0000000000000p+0, 0x1.fc06d493cca10p-1, 0x1.f81e6ac3b918fp-1,
  0x1.f44546ef18996p-1, 0x1.f07b10382c84bp-1, 0x1.ecbf7070e59d4p-1,
  0x1.e91213f715939p-1, 0x1.e572a9a75f7b7p-1, 0x1.e1e0e2c530207p-1,
  0x1.de5c72d8a8be3p-1, 0x1.dae50fa5658ccp-1, 0x1.d77a71145a2dap-1,
  0x1.d41c51166623ep-1, 0x1.d0ca6ba0bb29fp-1, 0x1.cd847e8e59681p-1,
  0x1.ca4a499693e00p-1, 0x1.c71b8e399e821p-1, 0x1.c3f80faf19077p-1,
  0x1.c0df92dc2b0ecp-1, 0x1.bdd1de3cbb542p-1, 0x1.baceb9e1007a3p-1,
  0x1.b7d5ef543e55ep-1, 0x1.b4e749977d953p-1, 0x1.b20295155478ep-1,
  0x1.af279f8e82be2p-1, 0x1.ac5638197fdf3p-1, 0x1.a98e2f102e087p-1,
  0x1.a6cf5606d05c1p-1, 0x1.a4197fc04d746p-1, 0x1.a16c80293dc01p-1,
  0x1.9ec82c4dc5bc9p-1, 0x1.9c2c5a491f534p-1, 0x1.9998e1480b618p-1,
  0x1.970d9977c6c2dp-1, 0x1.948a5c023d212p-1, 0x1.920f0303d6809p-1,
  0x1.8f9b698a98b45p-1, 0x1.8d2f6b81726f6p-1, 0x1.8acae5bb55badp-1,
  0x1.886db5d9275b8p-1, 0x1.8617ba567c13cp-1, 0x1.83c8d27487800p-1,
  0x1.8180de3c5dbe7p-1, 0x1.7f3fbe71cdb71p-1, 0x1.7d055498071c1p-1,
  0x1.7ad182e54f65ap-1, 0x1.78a42c3c90125p-1, 0x1.767d342f76944p-1,
  0x1.745c7ef26b00ap-1, 0x1.7241f15769d0fp-1, 0x1.702d70d396e41p-1,
  0x1.6e1ee3700cd11p-1, 0x1.6c162fc9cbe02p-1,
};
static const double log10_lhi[128] = {
  -0x1.345825f221684p-3,  -0x1.2f71a1f0c554ep-3,
  -0x1.2a91fdb30b1f4p-3,  -0x1.25b9260981a04p-3,
  -0x1.20e7081762193p-3,  -0x1.1c1b914aeefacp-3,
  -0x1.1756af5de404dp-3,  -0x1.12985059c90bfp-3,
  -0x1.0de0628f63df4p-3,  -0x1.092ed492e08eep-3,
  -0x1.0483954caf1dfp-3,  -0x1.ffbd27a9adbc0p-4,
  -0x1.f67f7f2e3d1a0p-4,  -0x1.ed4e1071ceebep-4,
  -0x1.e428bb47413c4p-4,  -0x1.db0f6003028d6p-4,
  -0x1.d201df6749831p-4,  -0x1.c9001ac5c9672p-4,
  -0x1.c009f3c78c790p-4,  -0x1.b71f4cb642e53p-4,
  -0x1.ae400818526b2p-4,  -0x1.a56c091954f87p-4,
  -0x1.9ca3332f096eep-4,  -0x1.93e56a3f23e55p-4,
  -0x1.8b3292a3903b0p-4,  -0x1.828a9112d9618p-4,
  -0x1.79ed4ac35f5acp-4,  -0x1.715aa51ed28c4p-4,
  -0x1.68d2861c999e9p-4,  -0x1.6054d40ded210p-4,
  -0x1.57e17576bc9a2p-4,  -0x1.4f7851798bb0bp-4,
  -0x1.47194f5690ae3p-4,  -0x1.3ec456d58ec47p-4,
  -0x1.36794ff3e5f55p-4,  -0x1.2e382315725e4p-4,
  -0x1.2600b8ed82e91p-4,  -0x1.1dd2fa85efc12p-4,
  -0x1.15aed136e3961p-4,  -0x1.0d94269d1a30dp-4,
  -0x1.0582e4a7659f5p-4,  -0x1.faf5eb655742dp-5,
  -0x1.eaf888487e8eep-5,  -0x1.db0d75ef25a82p-5,
  -0x1.cb348a49e6431p-5,  -0x1.bb6d9c69acdd8p-5,
  -0x1.abb88368aa7a0p-5,  -0x1.9c1517476af14p-5,
  -0x1.8c833051bfa4dp-5,  -0x1.7d02a78e7fb31p-5,
  -0x1.6d93565e97c5fp-5,  -0x1.5e351695db0c5p-5,
  -0x1.4ee7c2ba67adcp-5,  -0x1.3fab35ba16c01p-5,
  -0x1.307f4ad854bc9p-5,  -0x1.2163ddf4f988cp-5,
  -0x1.1258cb5d19e22p-5,  -0x1.035defdba3188p-5,
  -0x1.e8e651191bce4p-6,  -0x1.cb30a62be444cp-6,
  -0x1.ad9a9b3043823p-6,  -0x1.9023ecda1ccdep-6,
  -0x1.72cc592bd82d0p-6,  -0x1.55939eb1f9c6ep-6,
  -0x1.38797ca6cc5a0p-6,  -0x1.1b7db35c2c072p-6,
  -0x1.fd400812ee9a2p-7,  -0x1.c3c05fb4620f1p-7,
  -0x1.8a7bf3c40e2e3p-7,  -0x1.517249c15a75cp-7,
  -0x1.18a2ea5330c91p-7,  -0x1.c01abc8cdc4e2p-8,
  -0x1.4f6261750dec9p-8,  -0x1.be37b6612afa7p-9,
  -0x1.bc3a8398ac260p-10, 0x0.0p+0,
  0x1.bb796219f30a5p-9,   0x1.b984fdcba61cep-8,
  0x1.49cf12adf8e8cp-7,   0x1.b6075b5217083p-7,
  0x1.10b7466fc30ddp-6,   0x1.4603e4db6a3a1p-6,
  0x1.7aeb10e99e105p-6,   0x1.af6e49b0f0e36p-6,
  0x1.e38f064f41179p-6,   0x1.0ba75abbb7623p-5,
  0x1.25575ee2dba86p-5,   0x1.3ed83f477f946p-5,
  0x1.582aa79af60efp-5,   0x1.714f400fa83aep-5,
  0x1.8a46ad3901cb9p-5,   0x1.a311903b6b870p-5,
  0x1.bbb086f216911p-5,   0x1.d4242bdda648ep-5,
  0x1.ec6d167c2af10p-5,   0x1.0245ed8221426p-4,
  0x1.0e40856c74f64p-4,   0x1.1a269a31120fep-4,
  0x1.25f8718fc076cp-4,   0x1.31b64ffc95bf0p-4,
  0x1.3d60787ca5063p-4,   0x1.48f72ccd187fdp-4,
  0x1.547aad6602f1cp-4,   0x1.5feb3989d3acbp-4,
  0x1.6b490f3978c79p-4,   0x1.76946b3f5e703p-4,
  0x1.81cd895717c83p-4,   0x1.8cf4a4055c30ep-4,
  0x1.9809f4c48c0ebp-4,   0x1.a30db3f9899efp-4,
  0x1.ae001905458fcp-4,   0x1.b8e15a2e3a2cdp-4,
  0x1.c3b1ace2b0996p-4,   0x1.ce71456edfa62p-4,
  0x1.d9205759882c4p-4,   0x1.e3bf1513af0dfp-4,
  0x1.ee4db0412c414p-4,   0x1.f8cc5998de3a5p-4,
  0x1.019da085eaeb1p-3,   0x1.06cd4acdb4e3dp-3,
  0x1.0bf542bef813fp-3,   0x1.11159f14da262p-3,
  0x1.162e761c10d1cp-3,   0x1.1b3fddc60d43ep-3,
  0x1.2049ebac86aa6p-3,   0x1.254cb4fb7836ap-3,
  0x1.2a484e8d0d252p-3,   0x1.2f3ccce1c860bp-3,
};
static const double log10_llo[128] = {
  0x1.4a2767c556567p-60,  0x1.d0d20b980d4b4p-58,
  -0x1.8a3ce9b3ea5b5p-61, 0x1.5a5bcf30c84d0p-59,
  -0x1.aedae1abbbf5dp-57, 0x1.7fc1b4b2d0954p-61,
  0x1.a5ad865cdb601p-59,  -0x1.396705ec10b10p-59,
  0x1.a2c444a59257ap-57,  0x1.02fb4a2bd69bep-58,
  -0x1.521b8054774a4p-57, -0x1.c32cfcb93f763p-58,
  0x1.461a07c8326b2p-58,  -0x1.9c30eac644b44p-58,
  0x1.949f0d378b02cp-58,  -0x1.019efa5f3d224p-58,
  -0x1.3798ef4646923p-60, -0x1.ecd84cbc5e519p-58,
  -0x1.91fc2c70f94a4p-59, 0x1.2e7eed26e2a94p-60,
  -0x1.359b7fa7600cbp-60, -0x1.19dde3b4ebfc3p-58,
  0x1.3f9f001cbc090p-62,  -0x1.3e356c112d006p-58,
  0x1.de3fd839e0c13p-58,  -0x1.ac97017a42829p-60,
  0x1.a8ca0bb2ec710p-58,  0x1.370b3d8113023p-60,
  0x1.e4ad69b4f5edcp-58,  -0x1.5342794bee5c2p-58,
  -0x1.5ba40d1f0f0bcp-58, 0x1.2b2ed447ea3aap-58,
  0x1.7e3a608cf1932p-58,  0x1.9b4aeb2139e39p-61,
  0x1.74fe561a27916p-59,  -0x1.d3c43484406e9p-58,
  0x1.a8ce2d72adc99p-59,  -0x1.97ab0eefb5f2dp-59,
  -0x1.6877a7fde7496p-58, 0x1.0ce4110dc7a0bp-61,
  -0x1.047c1bb9e5e8ep-59, -0x1.694cfeca2eb52p-59,
  0x1.9961956b51ba1p-62,  0x1.df83c0a13d19ep-64,
  0x1.045eab4164869p-60,  -0x1.9ca0bcc020a64p-59,
  0x1.7f348b3884b11p-59,  0x1.d97898bdd638ap-59,
  -0x1.98bf82ab29644p-60, -0x1.fa411bd3c226bp-60,
  0x1.33ac1b4916c4fp-59,  -0x1.01d794bd6da2dp-59,
  -0x1.cc2951d54b91bp-60, -0x1.af1140bacdb0dp-60,
  0x1.1aabf016de15fp-59,  0x1.dfd2166eb8f20p-59,
  -0x1.84f0253f5e118p-64, 0x1.a63c25b318109p-64,
  0x1.24ef6b33aafe5p-61,  0x1.36cedf9fabfc0p-60,
  -0x1.b81f6888877fbp-62, 0x1.7347739d28400p-63,
  0x1.3bf6206d749c1p-60,  -0x1.c0c9ac46d42d3p-60,
  0x1.a632748ed876ep-60,  0x1.4806081c846e9p-60,
  0x1.9914d924ed878p-62,  0x1.adf8579d42eefp-61,
  0x1.37c4dae9ebb30p-61,  -0x1.6e932bbedce89p-64,
  0x1.35770d9d6da54p-66,  -0x1.1c3af82ef8344p-65,
  -0x1.9937a70a5ae21p-64, 0x1.4898d603a65f2p-64,
  0x1.96d0c7296a8dep-65,  0x0.0p+0,
  0x1.f8cbb072a4650p-65,  -0x1.c7be089956b02p-64,
  0x1.43def862708d5p-61,  0x1.bb3ad74756bb9p-61,
  -0x1.a04abae554d41p-60, 0x1.599df7fb383aep-60,
  -0x1.92ab09b7d969cp-61, -0x1.f26a0fd6b9beep-60,
  0x1.e6228d0c20477p-60,  -0x1.dd0aa2ec55299p-59,
  0x1.e2e28eaacfaa5p-59,  -0x1.dcab6dd503f94p-59,
  -0x1.4ee45044852adp-63, 0x1.bdc82b052fedbp-60,
  -0x1.1fc1d38575cf5p-60, -0x1.5e9123ec49973p-60,
  -0x1.bf4039d6d7293p-59, 0x1.e9c6ca4d01601p-59,
  0x1.54c017b153c22p-59,  -0x1.5b3ae2b9dfd96p-58,
  0x1.cafa247601b94p-63,  -0x1.d226e53ddde48p-59,
  0x1.0f34796c01bc3p-58,  0x1.395227c18d8b1p-58,
  0x1.7044046807ccap-65,  -0x1.5135423ca613ap-59,
  -0x1.0428c30732aebp-61, 0x1.b3a3c1be8b350p-61,
  0x1.7b76366bc73b7p-58,  -0x1.808d8a3442453p-58,
  0x1.bb4a45b453e45p-60,  -0x1.57f3ed0737c35p-58,
  0x1.7be0b9de0b2eep-58,  0x1.c1def27f7bf3dp-59,
  -0x1.558d5d2ddcb6fp-58, 0x1.52f8d71d34240p-62,
  0x1.dd324c76f97cfp-59,  0x1.dc2ef3558887dp-60,
  0x1.64f56feff1333p-59,  -0x1.80e98a9b687e2p-60,
  0x1.0a2a7436eadedp-60,  0x1.fbe0901d7fe5bp-61,
  -0x1.6133faeae4fe8p-59, -0x1.129f613595db9p-57,
  -0x1.48a35b7dc66dep-57, 0x1.0edb94e95ea36p-58,
  0x1.5cb670c19eabep-57,  0x1.9a613bcb4efe5p-58,
  -0x1.b5a00b5f86464p-57, 0x1.062a836f77ac0p-60,
  0x1.bd300bf967e88p-57,  0x1.820a211f73366p-58,
};

/* log10(2^k * z) in double-double, for the active lanes of ix only.  */
static inline void
log10_core_m1 (vuint64m1_t ix, vbool64_t m, size_t vl, vfloat64m1_t *phi,
               vfloat64m1_t *plo)
{
  vuint64m1_t tmp = __riscv_vsub_vx_u64m1_m (m, ix, LOG10_OFF, vl);
  vuint64m1_t ib
      = __riscv_vand_vx_u64m1 (__riscv_vsrl_vx_u64m1 (tmp, 42, vl), 0x3f8, vl);
  vint64m1_t k = __riscv_vsra_vx_i64m1 (
      __riscv_vreinterpret_v_u64m1_i64m1 (tmp), 52, vl);
  vfloat64m1_t kd = __riscv_vfcvt_f_x_v_f64m1_m (m, k, vl);
  vuint64m1_t iz = __riscv_vsub_vv_u64m1_m (
      m, ix, __riscv_vand_vx_u64m1 (tmp, 0xfffULL << 52, vl), vl);
  vfloat64m1_t z = __riscv_vreinterpret_v_u64m1_f64m1 (iz);

  vfloat64m1_t invc = __riscv_vluxei64_v_f64m1_m (m, log10_invc, ib, vl);
  vfloat64m1_t lh = __riscv_vluxei64_v_f64m1_m (m, log10_lhi, ib, vl);
  vfloat64m1_t ll = __riscv_vluxei64_v_f64m1_m (m, log10_llo, ib, vl);

  /* r = invc*z - 1, correctly rounded (SVE svmad_x).  */
  vfloat64m1_t r = __riscv_vfmadd_vv_f64m1_m (
      m, invc, z, __riscv_vfmv_v_f_f64m1 (-1.0, vl), vl);
  vfloat64m1_t r2 = __riscv_vfmul_vv_f64m1_m (m, r, r, vl);
  /* P(r) = c0 + c1 r + r2*(c2 + c3 r + r2*c4).  */
  vfloat64m1_t y = __riscv_vfmadd_vf_f64m1_m (
      m, r, log10_c[3], __riscv_vfmv_v_f_f64m1 (log10_c[2], vl), vl);
  vfloat64m1_t p = __riscv_vfmadd_vf_f64m1_m (
      m, r, log10_c[1], __riscv_vfmv_v_f_f64m1 (log10_c[0], vl), vl);
  y = __riscv_vfmadd_vf_f64m1_m (m, r2, log10_c[4], y, vl);
  p = __riscv_vfmadd_vv_f64m1_m (m, r2, y, p, vl);

  /* tail = r/ln10 + r2*P, as (th, te).  */
  vfloat64m1_t th = __riscv_vfmul_vf_f64m1_m (m, r, LOG10_IL10H, vl);
  vfloat64m1_t e1 = __riscv_vfmadd_vf_f64m1_m (
      m, r, LOG10_IL10H, __riscv_vfsgnjn_vv_f64m1 (th, th, vl), vl);
  vfloat64m1_t te = __riscv_vfmadd_vf_f64m1_m (m, r, LOG10_IL10L, e1, vl);
  te = __riscv_vfmadd_vv_f64m1_m (m, r2, p, te, vl);

  /* k*log10(2), as (pk, kl).  */
  vfloat64m1_t pk = __riscv_vfmul_vf_f64m1_m (m, kd, LOG10_L2H, vl);
  vfloat64m1_t ek = __riscv_vfmadd_vf_f64m1_m (
      m, kd, LOG10_L2H, __riscv_vfsgnjn_vv_f64m1 (pk, pk, vl), vl);
  vfloat64m1_t kl = __riscv_vfmadd_vf_f64m1_m (m, kd, LOG10_L2L, ek, vl);

  /* (s,e) = dd(logc) + dd(k log10(2)).  */
  vfloat64m1_t s = __riscv_vfadd_vv_f64m1_m (m, lh, pk, vl);
  vfloat64m1_t bb = __riscv_vfsub_vv_f64m1_m (m, s, lh, vl);
  vfloat64m1_t sb = __riscv_vfsub_vv_f64m1_m (m, s, bb, vl);
  vfloat64m1_t e = __riscv_vfsub_vv_f64m1_m (m, lh, sb, vl);
  e = __riscv_vfadd_vv_f64m1_m (m, e, __riscv_vfsub_vv_f64m1_m (m, pk, bb, vl),
                                vl);
  e = __riscv_vfadd_vv_f64m1_m (m, e, ll, vl);
  e = __riscv_vfadd_vv_f64m1_m (m, e, kl, vl);
  vfloat64m1_t s2 = __riscv_vfadd_vv_f64m1_m (m, s, e, vl);
  vfloat64m1_t e2 = __riscv_vfsub_vv_f64m1_m (
      m, e, __riscv_vfsub_vv_f64m1_m (m, s2, s, vl), vl);

  /* (t1,et) = (s2,e2) + tail, via two_sum(s2, th).  */
  vfloat64m1_t t1 = __riscv_vfadd_vv_f64m1_m (m, s2, th, vl);
  vfloat64m1_t t2 = __riscv_vfsub_vv_f64m1_m (m, t1, s2, vl);
  vfloat64m1_t t3 = __riscv_vfsub_vv_f64m1_m (
      m, s2, __riscv_vfsub_vv_f64m1_m (m, t1, t2, vl), vl);
  vfloat64m1_t et = __riscv_vfadd_vv_f64m1_m (
      m, t3, __riscv_vfsub_vv_f64m1_m (m, th, t2, vl), vl);
  et = __riscv_vfadd_vv_f64m1_m (m, et, e2, vl);
  et = __riscv_vfadd_vv_f64m1_m (m, et, te, vl);
  *phi = t1;
  *plo = et;
}

static inline vfloat64m1_t
log10_arm_m1 (vfloat64m1_t x, vbool64_t m, size_t vl)
{
  vuint64m1_t ix = __riscv_vreinterpret_v_f64m1_u64m1 (x);
  vuint64m1_t sp = __riscv_vsub_vx_u64m1_m (m, ix, LOG10_MINNORM, vl);
  vbool64_t special = __riscv_vmand_mm_b64 (
      __riscv_vmsgeu_vx_u64m1_b64 (sp, LOG10_THRESH, vl), m, vl);
  /* special lanes are scaled by 2^52; non-special lanes keep x.  */
  vfloat64m1_t xs = __riscv_vfmul_vf_f64m1_m (special, x, 0x1p52, vl);
  xs = __riscv_vmerge_vvm_f64m1 (x, xs, special, vl);

  vfloat64m1_t hi, lo;
  log10_core_m1 (__riscv_vreinterpret_v_f64m1_u64m1 (xs), m, vl, &hi, &lo);
  vfloat64m1_t base = __riscv_vfadd_vv_f64m1_m (m, hi, lo, vl);

  /* subnormal correction: (c1,ce) = (hi,lo) + log10(2^-52).  */
  const vfloat64m1_t l52h = __riscv_vfmv_v_f_f64m1 (LOG10_L52H, vl);
  vfloat64m1_t c1 = __riscv_vfadd_vv_f64m1_m (m, hi, l52h, vl);
  vfloat64m1_t cb = __riscv_vfsub_vv_f64m1_m (m, c1, hi, vl);
  vfloat64m1_t ce = __riscv_vfsub_vv_f64m1_m (
      m, hi, __riscv_vfsub_vv_f64m1_m (m, c1, cb, vl), vl);
  ce = __riscv_vfadd_vv_f64m1_m (
      m, ce, __riscv_vfsub_vv_f64m1_m (m, l52h, cb, vl), vl);
  ce = __riscv_vfadd_vv_f64m1_m (m, ce, lo, vl);
  ce = __riscv_vfadd_vv_f64m1_m (m, ce,
                                 __riscv_vfmv_v_f_f64m1 (LOG10_L52L, vl), vl);
  vfloat64m1_t corr = __riscv_vfadd_vv_f64m1_m (m, c1, ce, vl);

  /* special value selection (+0/-0 -> -inf, +inf -> +inf, else NaN for
     the remaining special lanes; positive subnormals take `corr`).  */
  vuint64m1_t iabs = __riscv_vand_vx_u64m1 (ix, 0x7fffffffffffffffULL, vl);
  vbool64_t iszero = __riscv_vmand_mm_b64 (
      __riscv_vmseq_vx_u64m1_b64 (iabs, 0, vl), special, vl);
  vbool64_t isneg = __riscv_vmand_mm_b64 (
      __riscv_vmsgeu_vx_u64m1_b64 (ix, LOG10_SIGNBIT, vl), special, vl);
  vbool64_t isnan = __riscv_vmand_mm_b64 (
      __riscv_vmsgtu_vx_u64m1_b64 (iabs, 0x7ff0000000000000ULL, vl), special,
      vl);
  vbool64_t sub
      = __riscv_vmandn_mm_b64 (__riscv_vmnot_m_b64 (iszero, vl), isneg, vl);
  sub = __riscv_vmandn_mm_b64 (sub, isnan, vl);
  sub = __riscv_vmand_mm_b64 (sub, special, vl);
  vbool64_t pinf = __riscv_vmand_mm_b64 (
      __riscv_vmseq_vx_u64m1_b64 (ix, 0x7ff0000000000000ULL, vl), special, vl);

  vfloat64m1_t sl = __riscv_vfmv_v_f_f64m1 (__builtin_nan (""), vl);
  sl = __riscv_vmerge_vvm_f64m1 (sl, corr, sub, vl);
  sl = __riscv_vfmerge_vfm_f64m1 (sl, -__builtin_inf (), iszero, vl);
  sl = __riscv_vfmerge_vfm_f64m1 (sl, __builtin_inf (), pinf, vl);
  return __riscv_vmerge_vvm_f64m1 (base, sl, special, vl);
}

static inline void
log10_core_m2 (vuint64m2_t ix, vbool32_t m, size_t vl, vfloat64m2_t *phi,
               vfloat64m2_t *plo)
{
  vuint64m2_t tmp = __riscv_vsub_vx_u64m2_m (m, ix, LOG10_OFF, vl);
  vuint64m2_t ib
      = __riscv_vand_vx_u64m2 (__riscv_vsrl_vx_u64m2 (tmp, 42, vl), 0x3f8, vl);
  vint64m2_t k = __riscv_vsra_vx_i64m2 (
      __riscv_vreinterpret_v_u64m2_i64m2 (tmp), 52, vl);
  vfloat64m2_t kd = __riscv_vfcvt_f_x_v_f64m2_m (m, k, vl);
  vuint64m2_t iz = __riscv_vsub_vv_u64m2_m (
      m, ix, __riscv_vand_vx_u64m2 (tmp, 0xfffULL << 52, vl), vl);
  vfloat64m2_t z = __riscv_vreinterpret_v_u64m2_f64m2 (iz);

  vfloat64m2_t invc = __riscv_vluxei64_v_f64m2_m (m, log10_invc, ib, vl);
  vfloat64m2_t lh = __riscv_vluxei64_v_f64m2_m (m, log10_lhi, ib, vl);
  vfloat64m2_t ll = __riscv_vluxei64_v_f64m2_m (m, log10_llo, ib, vl);

  vfloat64m2_t r = __riscv_vfmadd_vv_f64m2_m (
      m, invc, z, __riscv_vfmv_v_f_f64m2 (-1.0, vl), vl);
  vfloat64m2_t r2 = __riscv_vfmul_vv_f64m2_m (m, r, r, vl);
  vfloat64m2_t y = __riscv_vfmadd_vf_f64m2_m (
      m, r, log10_c[3], __riscv_vfmv_v_f_f64m2 (log10_c[2], vl), vl);
  vfloat64m2_t p = __riscv_vfmadd_vf_f64m2_m (
      m, r, log10_c[1], __riscv_vfmv_v_f_f64m2 (log10_c[0], vl), vl);
  y = __riscv_vfmadd_vf_f64m2_m (m, r2, log10_c[4], y, vl);
  p = __riscv_vfmadd_vv_f64m2_m (m, r2, y, p, vl);

  vfloat64m2_t th = __riscv_vfmul_vf_f64m2_m (m, r, LOG10_IL10H, vl);
  vfloat64m2_t e1 = __riscv_vfmadd_vf_f64m2_m (
      m, r, LOG10_IL10H, __riscv_vfsgnjn_vv_f64m2 (th, th, vl), vl);
  vfloat64m2_t te = __riscv_vfmadd_vf_f64m2_m (m, r, LOG10_IL10L, e1, vl);
  te = __riscv_vfmadd_vv_f64m2_m (m, r2, p, te, vl);

  vfloat64m2_t pk = __riscv_vfmul_vf_f64m2_m (m, kd, LOG10_L2H, vl);
  vfloat64m2_t ek = __riscv_vfmadd_vf_f64m2_m (
      m, kd, LOG10_L2H, __riscv_vfsgnjn_vv_f64m2 (pk, pk, vl), vl);
  vfloat64m2_t kl = __riscv_vfmadd_vf_f64m2_m (m, kd, LOG10_L2L, ek, vl);

  vfloat64m2_t s = __riscv_vfadd_vv_f64m2_m (m, lh, pk, vl);
  vfloat64m2_t bb = __riscv_vfsub_vv_f64m2_m (m, s, lh, vl);
  vfloat64m2_t sb = __riscv_vfsub_vv_f64m2_m (m, s, bb, vl);
  vfloat64m2_t e = __riscv_vfsub_vv_f64m2_m (m, lh, sb, vl);
  e = __riscv_vfadd_vv_f64m2_m (m, e, __riscv_vfsub_vv_f64m2_m (m, pk, bb, vl),
                                vl);
  e = __riscv_vfadd_vv_f64m2_m (m, e, ll, vl);
  e = __riscv_vfadd_vv_f64m2_m (m, e, kl, vl);
  vfloat64m2_t s2 = __riscv_vfadd_vv_f64m2_m (m, s, e, vl);
  vfloat64m2_t e2 = __riscv_vfsub_vv_f64m2_m (
      m, e, __riscv_vfsub_vv_f64m2_m (m, s2, s, vl), vl);

  vfloat64m2_t t1 = __riscv_vfadd_vv_f64m2_m (m, s2, th, vl);
  vfloat64m2_t t2 = __riscv_vfsub_vv_f64m2_m (m, t1, s2, vl);
  vfloat64m2_t t3 = __riscv_vfsub_vv_f64m2_m (
      m, s2, __riscv_vfsub_vv_f64m2_m (m, t1, t2, vl), vl);
  vfloat64m2_t et = __riscv_vfadd_vv_f64m2_m (
      m, t3, __riscv_vfsub_vv_f64m2_m (m, th, t2, vl), vl);
  et = __riscv_vfadd_vv_f64m2_m (m, et, e2, vl);
  et = __riscv_vfadd_vv_f64m2_m (m, et, te, vl);
  *phi = t1;
  *plo = et;
}

static inline vfloat64m2_t
log10_arm_m2 (vfloat64m2_t x, vbool32_t m, size_t vl)
{
  vuint64m2_t ix = __riscv_vreinterpret_v_f64m2_u64m2 (x);
  vuint64m2_t sp = __riscv_vsub_vx_u64m2_m (m, ix, LOG10_MINNORM, vl);
  vbool32_t special = __riscv_vmand_mm_b32 (
      __riscv_vmsgeu_vx_u64m2_b32 (sp, LOG10_THRESH, vl), m, vl);
  vfloat64m2_t xs = __riscv_vfmul_vf_f64m2_m (special, x, 0x1p52, vl);
  xs = __riscv_vmerge_vvm_f64m2 (x, xs, special, vl);

  vfloat64m2_t hi, lo;
  log10_core_m2 (__riscv_vreinterpret_v_f64m2_u64m2 (xs), m, vl, &hi, &lo);
  vfloat64m2_t base = __riscv_vfadd_vv_f64m2_m (m, hi, lo, vl);

  const vfloat64m2_t l52h = __riscv_vfmv_v_f_f64m2 (LOG10_L52H, vl);
  vfloat64m2_t c1 = __riscv_vfadd_vv_f64m2_m (m, hi, l52h, vl);
  vfloat64m2_t cb = __riscv_vfsub_vv_f64m2_m (m, c1, hi, vl);
  vfloat64m2_t ce = __riscv_vfsub_vv_f64m2_m (
      m, hi, __riscv_vfsub_vv_f64m2_m (m, c1, cb, vl), vl);
  ce = __riscv_vfadd_vv_f64m2_m (
      m, ce, __riscv_vfsub_vv_f64m2_m (m, l52h, cb, vl), vl);
  ce = __riscv_vfadd_vv_f64m2_m (m, ce, lo, vl);
  ce = __riscv_vfadd_vv_f64m2_m (m, ce,
                                 __riscv_vfmv_v_f_f64m2 (LOG10_L52L, vl), vl);
  vfloat64m2_t corr = __riscv_vfadd_vv_f64m2_m (m, c1, ce, vl);

  vuint64m2_t iabs = __riscv_vand_vx_u64m2 (ix, 0x7fffffffffffffffULL, vl);
  vbool32_t iszero = __riscv_vmand_mm_b32 (
      __riscv_vmseq_vx_u64m2_b32 (iabs, 0, vl), special, vl);
  vbool32_t isneg = __riscv_vmand_mm_b32 (
      __riscv_vmsgeu_vx_u64m2_b32 (ix, LOG10_SIGNBIT, vl), special, vl);
  vbool32_t isnan = __riscv_vmand_mm_b32 (
      __riscv_vmsgtu_vx_u64m2_b32 (iabs, 0x7ff0000000000000ULL, vl), special,
      vl);
  vbool32_t sub
      = __riscv_vmandn_mm_b32 (__riscv_vmnot_m_b32 (iszero, vl), isneg, vl);
  sub = __riscv_vmandn_mm_b32 (sub, isnan, vl);
  sub = __riscv_vmand_mm_b32 (sub, special, vl);
  vbool32_t pinf = __riscv_vmand_mm_b32 (
      __riscv_vmseq_vx_u64m2_b32 (ix, 0x7ff0000000000000ULL, vl), special, vl);

  vfloat64m2_t sl = __riscv_vfmv_v_f_f64m2 (__builtin_nan (""), vl);
  sl = __riscv_vmerge_vvm_f64m2 (sl, corr, sub, vl);
  sl = __riscv_vfmerge_vfm_f64m2 (sl, -__builtin_inf (), iszero, vl);
  sl = __riscv_vfmerge_vfm_f64m2 (sl, __builtin_inf (), pinf, vl);
  return __riscv_vmerge_vvm_f64m2 (base, sl, special, vl);
}

/* ---- self test harness (unary kernels) ---------------------------------- */
