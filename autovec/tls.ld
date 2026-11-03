/* Single-hart TLS required by the toolchain's static libm errno. */
SECTIONS {
  .tdata : ALIGN(16) { __tls_base = .; *(.tdata .tdata.*) }
  .tbss : { *(.tbss .tbss.*) }
}
INSERT BEFORE .bss;
