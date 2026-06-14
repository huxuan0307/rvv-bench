.POSIX:

include ../../config.mk

all: rvv

rvv: gen.S main.c config.h ../../config.mk
	m4 gen.S > main.S
	${CC} ${CFLAGS} -Wl,--relax main.S main.c -o $@ \
		-T ../../thirdparty/link.ld \
		../../thirdparty/am-riscv64-xs.a \
		../../thirdparty/klib-riscv64-xs.a
	rm main.S

clean:
	rm -f rvv main.S

run: rvv
	../../run.sh ./rvv
