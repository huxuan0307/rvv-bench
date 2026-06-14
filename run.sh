#!/bin/sh

# configure `make run` to work with your setup

# local execution
#./$@

# using qemu (linux-user)
/nfs/home/huxuan/repos/qemu/build-riscv64-linux-user-plugin/qemu-riscv64 \
	-cpu rv64,v=on,vext_spec=v1.0,vlen=256,rvv_ta_all_1s=on,rvv_ma_all_1s=on,zfh=true \
	$@
