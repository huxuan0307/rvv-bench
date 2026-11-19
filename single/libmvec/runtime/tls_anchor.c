/* A real initialized TLS byte gives .tdata SHF_TLS and a nonempty anchor.
   Do not replace this with a linker BYTE: that does not set SHF_TLS. */
__thread unsigned char scalar_vec_tls_anchor=1;
