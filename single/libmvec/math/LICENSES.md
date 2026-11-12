# 许可证与来源

本目录是独立benchmark实验快照，不修改已安装库。数学实现、表数据及改编依据LGPL-2.1-or-later发布；完整许可见[COPYING.LIB](<COPYING.LIB>)。各源文件保留GNU libc及IBM Accurate Mathematical Library的版权注释。

- double log：GNU libc dbl-64 e_log.c、e_log_data.c，Copyright (C) 2018–2026 Free Software Foundation, Inc.；冻结来源为2251任务batch05。
- double exp、pow：GNU libc dbl-64 e_exp.c、e_exp_data.c、e_pow.c、e_pow_log_data.c，Copyright (C) 2018–2026 Free Software Foundation, Inc.；冻结来源分别为batch03、batch04。
- binary32 logf、expf：GNU libc flt-32 e_logf.c、e_logf_data.c、e_expf.c、e_exp2f_data.c，Copyright (C) 2017–2026 Free Software Foundation, Inc.；冻结来源为batch03。
- double sin、cos：GNU libc IBM Accurate Mathematical Library s_sin.c、usncs.h、sincostab.c，Copyright (C) 2001–2026 Free Software Foundation, Inc.；冻结来源为batch04。

本轮改编把原array内部每chunk算法提取为共享内联寄存器核，新增unmasked Nx向量PCS入口；表数据、分类、FMA、范围约简与数学表达式保留。cold分支返回局部结果，数组壳stripmine，Nx壳设VLMAX。详细变更与限制见[源审说明](<README.zh.md>)，逐原文件及新快照SHA见[来源身份](<source-identity.json>)。
