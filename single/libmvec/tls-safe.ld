/* Single-hart TLS. Empty .tdata must not anchor tp inside an orphan GOT.
   The frozen runtime object still initializes tp using __tls_base. */
SECTIONS {
 .tdata : ALIGN(16) { __tls_base = .; *(.tdata .tdata.*) }
 .tbss : { *(.tbss .tbss.*) }
}
INSERT BEFORE .bss;
