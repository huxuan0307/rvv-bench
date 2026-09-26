
WARN=-Wall -Wextra -Wno-unused-function -Wno-unused-parameter

GNU_TOOL_CHAIN=/nfs/home/huxuan/opt/RISCV/bin/
TOOL_CHAIN_PREFIX=${GNU_TOOL_CHAIN}/riscv64-unknown-elf-

# freestanding using any recent clang build
# CC=clang
CC=${TOOL_CHAIN_PREFIX}gcc
DUMP=${TOOL_CHAIN_PREFIX}objdump
OBJCOPY=${TOOL_CHAIN_PREFIX}objcopy

CFLAGS= \
	-march=rv64gcv_zba_zbb_zbs_zvbb -mcmodel=medany \
	-fno-builtin -nostdlib -ffreestanding -D__BYTE_ORDER=__LITTLE_ENDIAN \
	-O2 -g ${WARN} \
	-Wl,--wrap=_trm_init
#CFLAGS=--target=riscv32 -march=rv32gc_zve32f_zba_zbb_zbs -O3 ${WARN} -nostdlib -fno-builtin -ffreestanding

# full cross compilation toolchain
#CC=riscv64-linux-gnu-gcc
#CFLAGS=-march=rv64gcv -O3 ${WARN}

# native build
#CC=cc
#CFLAGS=-march=rv64gcv -O3 ${WARN}
