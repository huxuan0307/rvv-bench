.POSIX:

include ../../config.mk

all: scalar

scalar: main.S main.c config.h ../../config.mk
	${CC} ${CFLAGS} main.S main.c -o $@ \
		-T ../../thirdparty/link.ld \
		../../thirdparty/am-riscv64-xs.a \
		../../thirdparty/klib-riscv64-xs.a

clean:
	rm -f scalar

run: scalar
	../../run.sh ./scalar
