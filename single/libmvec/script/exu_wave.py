#!/usr/bin/env python3
"""从 XiangShan 向量 Exu 的 FST 事件保守地重建动态 uop 生命周期。"""

import argparse
import collections
import csv
import itertools
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


ROOT = 'TOP.SimTop.cpu.l_soc.core_with_l2.core.backend.'
ROB = ROOT + 'inner_ctrlBlock.rob.'
VEC = ROOT + 'inner_vecRegion.'
MEM_PIPES = ('issuePipeLdu', 'issuePipeLdu_1', 'issuePipeLdu_2',
             'issuePipeStaMou', 'issuePipeStaMou_1', 'issuePipeStdMoud', 'issuePipeStdMoud_1')


def pipe_path(index):
    return VEC + ('issuePipes' if index == 0 else f'issuePipes_{index}') + '.exu.'


def signal_names():
    result = {'clock': 'TOP.clock', 'timer': ROB + 'timer',
              'redirect.valid': ROB + 'io_redirect_valid',
              'redirect.flag': ROB + 'io_redirect_bits_robIdx_flag',
              'redirect.value': ROB + 'io_redirect_bits_robIdx_value',
              'redirect.level': ROB + 'io_redirect_bits_level',
              'enq.accept': ROB + 'io_enq_canAccept',
              'commit.is': ROB + 'io_commits_isCommit'}
    for i in range(8):
        for key, suffix in (('valid', 'valid'), ('first', 'bits_firstUop'),
                            ('flag', 'bits_robIdx_flag'), ('value', 'bits_robIdx_value'),
                            ('pc', 'bits_debug_pc')):
            result[f'enq.{i}.{key}'] = ROB + f'io_enq_req_{i}_{suffix}'
        for key, suffix in (('valid', f'commitValid_{i}'),
                            ('flag', f'robIdx_{i}_flag'), ('value', f'robIdx_{i}_value')):
            result[f'commit.{i}.{key}'] = ROB + 'io_commits_' + suffix
    for i in range(6):
        prefix = pipe_path(i)
        fields = {'flush': 'in_flush_valid', 'in.valid': 'in_uop_valid',
                  'in.flag': 'in_uop_bits_ctrl_robIdx_flag',
                  'in.value': 'in_uop_bits_ctrl_robIdx_value',
                  'in.uop': 'in_uop_bits_ctrl_uopIdx',
                  'ex.valid': 'ex_0_valid', 'ex.flag': 'ex_0_bits_ctrl_robIdx_flag',
                  'ex.value': 'ex_0_bits_ctrl_robIdx_value',
                  'ex.uop': 'ex_0_bits_ctrl_uopIdx',
                  'ex.debug_uop': 'ex_0_bits_debug_seqNum_uopIdx',
                  'ex.seq': 'ex_0_bits_debug_seqNum_seqNum',
                  'ex.fu': 'ex_0_bits_ctrl_fuType', 'ex.opcode': 'ex_0_bits_ctrl_opcode',
                  'ex.latency': 'ex_0_bits_ctrl_latency',
                  'out.valid': 'out_uop_valid', 'out.flag': 'out_uop_bits_toRob_robIdx_flag',
                  'out.value': 'out_uop_bits_toRob_robIdx_value',
                  'out.debug_uop': 'out_uop_bits_toRob_debug_seqNum_uopIdx',
                  'out.seq': 'out_uop_bits_toRob_debug_seqNum_seqNum',
                  'div.valid': 'out_divStartResp_valid',
                  'div.accepted': 'out_divStartResp_bits_accepted',
                  'vstd.success': 'out_ex0VStdSuccess'}
        if i == 0:
            fields['vfmac.in'] = 'Vfmac.in_ex_0_valid'
            for n in (1, 2, 3):
                fields[f'vfmac.out{n}'] = f'Vfmac.out_ex_{n}_valid'
        for key, suffix in fields.items():
            result[f'p{i}.{key}'] = prefix + suffix
    for i, name in enumerate(MEM_PIPES):
        base = ROOT + 'inner_intRegion.' + name + '.'
        fields = {'ready': 'in_ex0MemIssueReady', 'valid': 'out_ex0MemIssue_valid',
                  'cancel': 'exu.in_ex0Cancel', 'hold': 'exu.in_ex0Hold',
                  'replace': 'memCandidateCanReplace', 'flush': 'exu.in_flush_valid',
                  'out.flag': 'out_ex0MemIssue_bits_ctrl_robIdx_flag',
                  'out.value': 'out_ex0MemIssue_bits_ctrl_robIdx_value',
                  'out.uop': 'out_ex0MemIssue_bits_ctrl_uopIdx',
                  'ex.valid': 'exu.ex_0_valid', 'ex.flag': 'exu.ex_0_bits_ctrl_robIdx_flag',
                  'ex.value': 'exu.ex_0_bits_ctrl_robIdx_value',
                  'ex.uop': 'exu.ex_0_bits_ctrl_uopIdx',
                  'ex.debug_uop': 'exu.ex_0_bits_debug_seqNum_uopIdx',
                  'ex.seq': 'exu.ex_0_bits_debug_seqNum_seqNum',
                  'ex.fu': 'exu.ex_0_bits_ctrl_fuType',
                  'ex.opcode': 'exu.ex_0_bits_ctrl_opcode',
                  'ex.latency': 'exu.ex_0_bits_ctrl_latency'}
        for key, suffix in fields.items():
            result[f'm{i}.{key}'] = base + suffix
        if i < 5:
            kind, lane = ('lduReq', i) if i < 3 else ('staReq', i - 3)
            target = ROOT + f'inner_vagqMod.io_{kind}_{lane}_'
            for key, suffix in (('valid', 'valid'), ('ready', 'ready'), ('meta', 'bits_meta_valid'),
                                ('flag', 'bits_req_robIdx_flag'), ('value', 'bits_req_robIdx_value'),
                                ('uop', 'bits_req_ctrl_uopIdx'),
                                ('debug_uop', 'bits_req_debug_seqNum_uopIdx')):
                result[f'm{i}.lsu.{key}'] = target + suffix
    return result


def discover(path):
    wanted = signal_names()
    reverse = {v: k for k, v in wanted.items()}
    result = {}
    with open(path, encoding='utf-8') as src:
        for line in src:
            handle, width, name = line.rstrip('\n').split('\t', 2)
            key = reverse.get(name.split(' [', 1)[0])
            if key is not None:
                if key in result:
                    raise ValueError(f'信号出现多次: {name}')
                result[key] = (int(handle), int(width))
    mandatory = ['clock', 'timer', 'redirect.valid', 'redirect.flag', 'redirect.value',
                 'redirect.level', 'enq.accept', 'commit.is']
    mandatory += [f'enq.{i}.{x}' for i in range(8) for x in ('valid', 'first', 'flag', 'value', 'pc')]
    mandatory += [f'commit.{i}.{x}' for i in range(8) for x in ('valid', 'flag', 'value')]
    mandatory += [f'p{i}.{x}' for i in range(6) for x in
                  ('flush', 'ex.valid', 'ex.flag', 'ex.value', 'ex.uop', 'ex.debug_uop',
                   'out.valid', 'out.flag', 'out.value', 'out.debug_uop')]
    mandatory += ['p0.in.valid', 'p0.in.flag', 'p0.in.value', 'p0.in.uop',
                  'p0.ex.latency', 'p0.vfmac.in', 'p0.vfmac.out1']
    mandatory += [f'm{i}.{x}' for i in range(len(MEM_PIPES)) for x in
                  ('ready', 'valid', 'cancel', 'hold', 'replace', 'flush', 'ex.valid',
                   'ex.flag', 'ex.value', 'ex.uop', 'ex.debug_uop',
                   'out.flag', 'out.value', 'out.uop')]
    mandatory += [f'm{i}.lsu.{x}' for i in range(5) for x in
                  ('valid', 'ready', 'meta', 'flag', 'value', 'uop', 'debug_uop')]
    missing = sorted(set(mandatory) - result.keys())
    if missing:
        raise ValueError('缺少必要信号，拒绝猜测: ' + ', '.join(missing))
    return result


def parse_binary(text):
    return int(text, 2) if text and set(text) <= {'0', '1'} else None


def snapshots(binary, fst, mapping, end_time, stats):
    # 一个 handle 可是多个层级信号的别名；必须同步更新所有逻辑字段。
    aliases = collections.defaultdict(list)
    for name, (handle, _) in mapping.items():
        aliases[handle].append(name)
    command = [binary, fst, '0', str(end_time), *map(str, sorted(aliases))]
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, text=True, bufsize=1)
    state = {}
    previous_timer = None
    try:
        for time, group in itertools.groupby(proc.stdout, key=lambda x: int(x.split('\t', 1)[0])):
            for line in group:
                _, handle, value = line.rstrip('\n').split('\t')
                for name in aliases[int(handle)]:
                    state[name] = parse_binary(value)
                    if name == 'clock':
                        stats['clock_events'] += 1
                        stats['clock_values'].add(state[name])
            if time > end_time:
                # fstReaderSetLimitTimeRange 按压缩块裁剪，块尾仍可能超过请求时间。
                continue
            timer = state.get('timer')
            if timer is None:
                stats['timer_unknown_groups'] += 1
                continue
            if timer == previous_timer:
                # 同一时基值有多个时间点时无法证明组合信号的采样相位。
                stats['timer_duplicate_groups'] += 1
                continue
            yield time, timer, state.copy()
            previous_timer = timer
    finally:
        proc.stdout.close()
        rc = proc.wait()
        if rc:
            raise RuntimeError(f'FST 事件导出失败: exit={rc}')


def triple(s, prefix, kind, uop='debug_uop'):
    keys = (f'{prefix}.{kind}.flag', f'{prefix}.{kind}.value', f'{prefix}.{kind}.{uop}')
    values = tuple(s.get(k) for k in keys)
    return values if all(v is not None for v in values) else None


def calibrate(binary, fst, mapping, end_time, min_checks=10):
    stats = collections.Counter()
    stats['clock_values'] = set()
    last = None
    for t, timer, s in snapshots(binary, fst, mapping, end_time, stats):
        stats['samples'] += 1
        if last is not None:
            pt, pc, p = last
            if timer != pc + 1 or t <= pt:
                stats['timer_breaks'] += 1
            if p.get('p0.flush') == 0 and s.get('p0.flush') == 0:
                if p.get('p0.in.valid') == 1:
                    stats['in_checks'] += 1
                    if (s.get('p0.ex.valid') == 1 and
                            triple(p, 'p0', 'in', 'uop') == triple(s, 'p0', 'ex', 'uop') and
                            triple(p, 'p0', 'in', 'uop') is not None):
                        stats['in_matches'] += 1
                if p.get('p0.vfmac.in') == 1 and p.get('p0.ex.latency') == 1:
                    stats['vfmac_checks'] += 1
                    identity = triple(p, 'p0', 'ex')
                    if (identity is not None and s.get('p0.vfmac.out1') == 1 and
                            s.get('p0.out.valid') == 1 and identity == triple(s, 'p0', 'out')):
                        stats['vfmac_matches'] += 1
        last = t, timer, s
    # 同时验证时钟计数、输入到寄存 ex0、固定延迟输出的身份及相位。
    stats['calibrated'] = (stats['timer_breaks'] == 0 and
                           stats['timer_unknown_groups'] == 0 and
                           stats['timer_duplicate_groups'] == 0 and
                           stats['in_checks'] >= min_checks and
                           stats['in_matches'] == stats['in_checks'] and
                           stats['vfmac_checks'] >= min_checks and
                           stats['vfmac_matches'] == stats['vfmac_checks'])
    return stats


def need_flush(ptr, redirect, flush_itself):
    """对应 CircularQueuePtr 的 > 及 RobPtr.needFlush，不假设 ROB 大小为 2 的幂。"""
    if ptr is None or redirect is None or flush_itself is None:
        return None
    flag, value = ptr
    rflag, rvalue = redirect
    return bool((flush_itself and ptr == redirect) or ((flag != rflag) != (value > rvalue)))


@dataclass
class Allocation:
    generation: int
    pc: int
    alloc_cycle: int


@dataclass
class Attempt:
    pipe: int
    flag: int
    value: int
    uop: int
    debug_uop: int
    seq: int
    fu: int
    opcode: int
    latency: int
    entry_time: int
    entry_cycle: int
    generation: int
    pc: int
    attempt: int
    fu_accept: str
    reasons: list = field(default_factory=list)

    @property
    def match_key(self):
        return self.pipe, self.flag, self.value, self.debug_uop


COLUMNS = ['pipe', 'rob_flag', 'rob_value', 'generation', 'pc', 'uop_idx',
           'debug_uop_idx', 'seq_num', 'attempt', 'fu_type', 'opcode', 'fu_latency',
           'entry_fst_time', 'exit_fst_time', 'entry_cycle', 'exit_cycle', 'elapsed_cycles',
           'handoff_fst_time', 'handoff_cycle', 'fu_accept', 'outcome', 'certainty', 'reason']


class Tracker:
    def __init__(self, calibrated):
        self.calibrated = calibrated
        self.active = {}
        self.generation = 0
        self.attempt_numbers = collections.Counter()
        self.pending = collections.defaultdict(list)
        self.memory = {}
        self.previous_memory_hold = {}
        self.rows = []
        self.stats = collections.Counter()

    def emit(self, a, outcome, *, exit_time=None, exit_cycle=None, handoff_time=None,
             handoff_cycle=None, reason=''):
        certain = (not a.reasons and self.calibrated and a.generation is not None and
                   ((outcome == 'completed' and a.fu_accept == 'vfmac_observed') or
                    (outcome == 'lsu_accepted' and a.fu_accept == 'lsu_path')))
        why = list(a.reasons)
        if reason:
            why.append(reason)
        if not self.calibrated:
            why.append('uncalibrated_timebase')
        if a.generation is None:
            why.append('allocation_not_observed')
        if outcome == 'completed' and a.fu_accept != 'vfmac_observed':
            why.append('fu_acceptance_not_validated')
        values = [a.pipe, a.flag, a.value, a.generation,
                  hex(a.pc) if a.pc is not None else '', a.uop, a.debug_uop, a.seq,
                  a.attempt, hex(a.fu) if a.fu is not None else '',
                  hex(a.opcode) if a.opcode is not None else '', a.latency,
                  a.entry_time, exit_time,
                  a.entry_cycle if self.calibrated else '',
                  exit_cycle if self.calibrated else '',
                  exit_cycle - a.entry_cycle if certain else '', handoff_time,
                  handoff_cycle if self.calibrated else '', a.fu_accept, outcome,
                  ('verified_vfmac' if a.fu_accept == 'vfmac_observed' else 'verified_lsu')
                  if certain else 'uncertain', ';'.join(dict.fromkeys(why))]
        self.rows.append(dict(zip(COLUMNS, values)))
        self.stats[outcome] += 1
        if certain:
            self.stats['verified_vfmac' if a.fu_accept == 'vfmac_observed' else 'verified_lsu'] += 1

    def _remove_ptr(self, ptr, outcome, reason):
        for key in list(self.pending):
            if key[1:3] == ptr:
                for attempt in self.pending.pop(key):
                    self.emit(attempt, outcome, reason=reason)
        for pipe, attempt in list(self.memory.items()):
            if (attempt.flag, attempt.value) == ptr:
                self.emit(self.memory.pop(pipe), outcome, reason=reason)

    def step(self, time, cycle, s):
        redirect_valid = s.get('redirect.valid')
        flushed_memory = set()
        ptr = (s.get('redirect.flag'), s.get('redirect.value'))
        redirect = ptr if None not in ptr else None
        if redirect_valid is None:
            flushed_memory = {(a.pipe, a.flag, a.value, a.uop, a.debug_uop)
                              for a in self.memory.values()}
            pointers = set(self.active) | {key[1:3] for key in self.pending}
            pointers.update((a.flag, a.value) for a in self.memory.values())
            for p in pointers:
                self._remove_ptr(p, 'redirect_unknown', 'redirect_valid_unknown')
            self.active.clear()
        elif redirect_valid == 1:
            self.stats['redirect_cycles'] += 1
            flushed_memory = {(a.pipe, a.flag, a.value, a.uop, a.debug_uop)
                              for a in self.memory.values()
                              if need_flush((a.flag, a.value), redirect, s.get('redirect.level')) is not False}
            for p in list(self.active):
                flush = need_flush(p, redirect, s.get('redirect.level'))
                if flush is not False:
                    self._remove_ptr(p, 'flushed' if flush else 'redirect_unknown',
                                     'rob_redirect' if flush else 'redirect_fields_unknown')
                    del self.active[p]
            # 对未见分配的在途 uop 也判断 flush，而不是伪造其世代。
            for key in list(self.pending):
                p = key[1:3]
                flush = need_flush(p, redirect, s.get('redirect.level'))
                if flush is not False:
                    self._remove_ptr(p, 'flushed' if flush else 'redirect_unknown',
                                     'rob_redirect' if flush else 'redirect_fields_unknown')
            for a in list(self.memory.values()):
                p = (a.flag, a.value)
                flush = need_flush(p, redirect, s.get('redirect.level'))
                if flush is not False:
                    self._remove_ptr(p, 'flushed' if flush else 'redirect_unknown',
                                     'rob_redirect' if flush else 'redirect_fields_unknown')

        for i in range(6):
            prefix = f'p{i}'
            if s.get(prefix + '.ex.valid') is None:
                self.stats['unknown_ex_valid'] += 1
                for key in list(self.pending):
                    if key[0] == i:
                        for old in self.pending.pop(key):
                            self.emit(old, 'signal_unknown', reason='ex0_valid_unknown')
                continue
            if s.get(prefix + '.ex.valid') != 1:
                continue
            f, v, uop, dbg = (s.get(prefix + '.ex.' + n) for n in ('flag', 'value', 'uop', 'debug_uop'))
            if None in (f, v, uop, dbg):
                self.stats['unknown_ex_identity'] += 1
                for key in list(self.pending):
                    if key[0] == i:
                        for old in self.pending.pop(key):
                            self.emit(old, 'signal_unknown', reason='ex0_identity_unknown')
                continue
            gen = self.active.get((f, v))
            # 同一 uop 可重发到另一个 Exu，尝试计数不能以 pipe 为界。
            number_key = (f, v, gen.generation if gen else None, uop)
            self.attempt_numbers[number_key] += 1
            accept = 'vfmac_observed' if i == 0 and s.get('p0.vfmac.in') == 1 else 'unverified'
            a = Attempt(i, f, v, uop, dbg, s.get(prefix + '.ex.seq'),
                        s.get(prefix + '.ex.fu'), s.get(prefix + '.ex.opcode'),
                        s.get(prefix + '.ex.latency'), time, cycle,
                        gen.generation if gen else None, gen.pc if gen else None,
                        self.attempt_numbers[number_key], accept)
            flush = need_flush((f, v), redirect, s.get('redirect.level')) if redirect_valid == 1 else False
            if (redirect_valid is None or flush is None or s.get(prefix + '.flush') is None or
                    s.get(prefix + '.flush') != redirect_valid):
                self.emit(a, 'redirect_unknown', reason='flush_state_unknown_or_mismatched')
            elif flush:
                self.emit(a, 'flushed', reason='ex0_suppressed_by_redirect')
            elif s.get(prefix + '.div.valid') == 1 and s.get(prefix + '.div.accepted') == 0:
                self.emit(a, 'rejected', reason='divider_rejected_retry_possible')
            elif i >= 4:
                # VStd 的本地响应仅表示 IssueQueue 的反馈，不能冒充 LSU 接收。
                success = s.get(prefix + '.vstd.success')
                outcome = 'rejected' if success == 0 else 'vstd_response_unresolved'
                reason = 'vstd_rejected_retry_possible' if success == 0 else 'lsu_acceptance_not_observed'
                self.emit(a, outcome, reason=reason)
            else:
                if a.uop != a.debug_uop:
                    a.reasons.append('control_debug_uop_mapping_unverified')
                if a.attempt > 1:
                    a.reasons.append('reissue_or_replay_requires_validation')
                self.pending[a.match_key].append(a)
                if len(self.pending[a.match_key]) > 1:
                    for old in self.pending[a.match_key]:
                        if 'overlapping_attempts' not in old.reasons:
                            old.reasons.append('overlapping_attempts')

        for i in range(6):
            prefix = f'p{i}'
            if s.get(prefix + '.out.valid') is None:
                self.stats['unknown_out_valid'] += 1
                for key in list(self.pending):
                    if key[0] == i:
                        for old in self.pending.pop(key):
                            self.emit(old, 'signal_unknown', reason='output_valid_unknown')
                continue
            if s.get(prefix + '.out.valid') != 1:
                continue
            identity = triple(s, prefix, 'out')
            if identity is None:
                self.stats['unknown_out_identity'] += 1
                for key in list(self.pending):
                    if key[0] == i:
                        for old in self.pending.pop(key):
                            self.emit(old, 'signal_unknown', reason='output_identity_unknown')
                continue
            f, v, debug_uop = identity
            key = (i, f, v, debug_uop)
            candidates = self.pending.get(key, [])
            if len(candidates) != 1:
                # 多个候选不按 FIFO 猜测，整组排除，避免后续输出被误配。
                for a in self.pending.pop(key, []):
                    self.emit(a, 'ambiguous', reason='multiple_output_candidates')
                if i >= 4:
                    self.stats['vstd_output_ignored'] += 1
                else:
                    self.stats['unmatched_output'] += 1
                continue
            a = candidates[0]
            del self.pending[key]
            allocation = self.active.get((f, v))
            if not allocation or allocation.generation != a.generation:
                a.reasons.append('generation_not_live_at_output')
            if i == 0 and a.fu_accept == 'vfmac_observed':
                valid_slot = a.latency in (1, 2, 3) and s.get(f'p0.vfmac.out{a.latency}') == 1
                if not valid_slot or cycle - a.entry_cycle != a.latency:
                    a.reasons.append('vfmac_release_not_validated')
            self.emit(a, 'completed', exit_time=time, exit_cycle=cycle)

        # 先确认 Exu 到 VAGQ 的握手，再独立检查同拍 LSU 端口及身份。
        # 仅对 meta=0 的原生直通可归因；VAGQ 拆分后的输出不能凭 ROB 猜配。
        for i, name in enumerate(MEM_PIPES):
            prefix = f'm{i}'
            live = self.memory.get(i)
            ev = s.get(prefix + '.ex.valid')
            f, v, uop, dbg = (s.get(prefix + '.ex.' + n) for n in ('flag', 'value', 'uop', 'debug_uop'))
            key = (f, v, uop, dbg)
            if live and (ev != 1 or key != (live.flag, live.value, live.uop, live.debug_uop)):
                self.emit(self.memory.pop(i), 'memory_owner_lost', reason='held_owner_disappeared')
                live = None
            if live and self.previous_memory_hold.get(i) != 1:
                live.reasons.append('memory_residency_continuity_unproven')
            if ev != 1:
                continue
            if None in key:
                self.stats['unknown_memory_identity'] += 1
                continue
            if (name, f, v, uop, dbg) in flushed_memory:
                continue
            if live is None:
                gen = self.active.get((f, v))
                nk = (f, v, gen.generation if gen else None, uop)
                self.attempt_numbers[nk] += 1
                live = Attempt(name, f, v, uop, dbg, s.get(prefix + '.ex.seq'),
                               s.get(prefix + '.ex.fu'), s.get(prefix + '.ex.opcode'),
                               s.get(prefix + '.ex.latency'), time, cycle,
                               gen.generation if gen else None, gen.pc if gen else None,
                               self.attempt_numbers[nk], 'lsu_path')
                if self.previous_memory_hold.get(i) != 0:
                    live.reasons.append('memory_entry_boundary_not_proven')
                if uop != dbg:
                    live.reasons.append('control_debug_uop_mapping_unverified')
                if live.attempt > 1:
                    live.reasons.append('reissue_or_replay_requires_validation')
                self.memory[i] = live
            needed = ('valid', 'ready', 'cancel', 'hold', 'replace', 'flush')
            fields = {n: s.get(prefix + '.' + n) for n in needed}
            flush = need_flush((f, v), redirect, s.get('redirect.level')) if redirect_valid == 1 else False
            if (redirect_valid is None or flush is None or None in fields.values() or
                    fields['flush'] != redirect_valid):
                self.emit(self.memory.pop(i), 'redirect_unknown', reason='memory_control_unknown')
            elif flush:
                self.emit(self.memory.pop(i), 'flushed', reason='memory_ex0_suppressed_by_redirect')
            elif fields['cancel']:
                self.emit(self.memory.pop(i), 'cancelled', reason='memory_source_promise_cancelled')
            elif fields['replace']:
                self.emit(self.memory.pop(i), 'rejected', reason='memory_resident_replaced_retry_possible')
            elif fields['valid'] == 1 and fields['ready'] == 1:
                out = tuple(s.get(prefix + '.out.' + x) for x in ('flag', 'value', 'uop'))
                if out != (f, v, uop):
                    live.reasons.append('memory_request_identity_mismatch')
                allocation = self.active.get((f, v))
                if not allocation or allocation.generation != live.generation:
                    live.reasons.append('generation_not_live_at_output')
                lsu = tuple(s.get(prefix + '.lsu.' + x) for x in
                            ('valid', 'ready', 'meta', 'flag', 'value', 'uop', 'debug_uop'))
                if i < 5 and lsu == (1, 1, 0, f, v, uop, dbg):
                    self.emit(self.memory.pop(i), 'lsu_accepted', exit_time=time, exit_cycle=cycle,
                              handoff_time=time, handoff_cycle=cycle)
                else:
                    self.emit(self.memory.pop(i), 'memory_handoff_only', handoff_time=time,
                              handoff_cycle=cycle, reason='vagq_input_not_lsu_acceptance')
            elif fields['hold'] != 1:
                self.emit(self.memory.pop(i), 'memory_owner_lost', reason='no_accept_and_not_held')

        self.previous_memory_hold = {i: s.get(f'm{i}.hold') for i in range(len(MEM_PIPES))}

        # 如果 ROB 分配或提交控制含 X，之后不能再信任现存世代映射。
        unknown_rob = (s.get('commit.is') is None or
                       (s.get('commit.is') == 1 and any(s.get(f'commit.{i}.valid') is None for i in range(8))) or
                       s.get('enq.accept') is None or
                       (s.get('enq.accept') == 1 and any(
                           s.get(f'enq.{i}.valid') is None or
                           (s.get(f'enq.{i}.valid') == 1 and s.get(f'enq.{i}.first') is None)
                           for i in range(8))))
        if s.get('commit.is') == 1:
            unknown_rob |= any(s.get(f'commit.{i}.valid') == 1 and
                               (s.get(f'commit.{i}.flag') is None or s.get(f'commit.{i}.value') is None)
                               for i in range(8))
        if redirect_valid == 0 and s.get('enq.accept') == 1:
            unknown_rob |= any(s.get(f'enq.{i}.valid') == 1 and s.get(f'enq.{i}.first') == 1 and
                               (s.get(f'enq.{i}.flag') is None or s.get(f'enq.{i}.value') is None or
                                s.get(f'enq.{i}.pc') is None) for i in range(8))
        if unknown_rob:
            self.stats['unknown_rob_control'] += 1
            for p in list(self.active):
                self._remove_ptr(p, 'rob_control_unknown', 'allocation_or_commit_control_unknown')
            self.active.clear()

        # 提交仅用于撤销映射，不充当 Exu 的出入点；同采样提交在后续边沿生效。
        if s.get('commit.is') == 1:
            for i in range(8):
                if s.get(f'commit.{i}.valid') == 1:
                    p = (s.get(f'commit.{i}.flag'), s.get(f'commit.{i}.value'))
                    if None not in p:
                        self._remove_ptr(p, 'retired_without_exu_output', 'no_exu_output_observed')
                        self.active.pop(p, None)
        # ROB 的登记写入受 redirect 抑制；同采样的 Ex0 不可用新分配反向关联。
        if not unknown_rob and redirect_valid == 0 and s.get('enq.accept') == 1:
            for i in range(8):
                if s.get(f'enq.{i}.valid') == 1 and s.get(f'enq.{i}.first') == 1:
                    p = (s.get(f'enq.{i}.flag'), s.get(f'enq.{i}.value'))
                    pc = s.get(f'enq.{i}.pc')
                    if None in p or pc is None:
                        self.stats['unknown_allocation'] += 1
                        continue
                    if p in self.active:
                        self._remove_ptr(p, 'generation_reused', 'allocation_replaced_live_generation')
                        self.stats['allocation_replaced'] += 1
                    self.generation += 1
                    self.active[p] = Allocation(self.generation, pc, cycle + 1)
                    self.stats['allocations'] += 1

    def finish(self):
        for key in list(self.pending):
            for a in self.pending.pop(key):
                self.emit(a, 'trace_end', reason='no_exit_observed_before_end')
        for i in list(self.memory):
            self.emit(self.memory.pop(i), 'trace_end', reason='no_lsu_accept_before_end')
        # 跨 pipe 的重复身份也可能是 replay 或跨功能单元的分支，无法以先后猜配。
        # 已输出过的较早候选同样降级，避免因后续才观察到重复而误称已验证。
        groups = collections.defaultdict(list)
        for row in self.rows:
            if row['generation'] is not None:
                key = (row['rob_flag'], row['rob_value'], row['generation'], row['uop_idx'])
                groups[key].append(row)
        for rows in groups.values():
            if len(rows) < 2:
                continue
            for row in rows:
                if row['certainty'] in ('verified_vfmac', 'verified_lsu'):
                    self.stats[row['certainty']] -= 1
                row['certainty'] = 'uncertain'
                row['elapsed_cycles'] = ''
                reasons = row['reason'].split(';') if row['reason'] else []
                if 'multiple_attempts_across_pipes' not in reasons:
                    reasons.append('multiple_attempts_across_pipes')
                row['reason'] = ';'.join(reasons)


def run(args):
    mapping = discover(args.signals)
    calibration = calibrate(args.fst_events, args.fst, mapping, args.end_time, args.min_checks)
    stats = collections.Counter()
    stats['clock_values'] = set()
    tracker = Tracker(calibration['calibrated'])
    for t, cycle, state in snapshots(args.fst_events, args.fst, mapping, args.end_time, stats):
        tracker.step(t, cycle, state)
    tracker.finish()
    with open(args.csv, 'w', newline='', encoding='utf-8') as out:
        writer = csv.DictWriter(out, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(tracker.rows)
    print('时基校准:', '通过' if calibration['calibrated'] else '未通过，所有周期字段留空')
    print('采样数:', calibration['samples'], '计时器不连续:', calibration['timer_breaks'],
          '未知时间组:', calibration['timer_unknown_groups'],
          '重复时间组:', calibration['timer_duplicate_groups'])
    print('TOP.clock 事件数/取值:', calibration['clock_events'], sorted(calibration['clock_values'], key=str))
    print('输入到寄存 ex0 检查/一致:', calibration['in_checks'], calibration['in_matches'])
    print('VFMac 一周期校准检查/一致:', calibration['vfmac_checks'], calibration['vfmac_matches'])
    print('ROB 分配/替换:', tracker.stats['allocations'], tracker.stats['allocation_replaced'])
    print('输出统计:', ', '.join(f'{key}={value}' for key, value in sorted(tracker.stats.items())))
    print('缺失可选信号:', ', '.join(sorted(set(signal_names()) - set(mapping))) or '无')
    print('CSV:', args.csv)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fst', required=True, help='输入 FST，不会修改')
    parser.add_argument('--signals', required=True, help='对应 FST 的 fst_signals 层级 TSV')
    parser.add_argument('--fst-events', required=True, help='由 fst_events.c 编译的导出器')
    parser.add_argument('--csv', required=True, help='输出 CSV')
    parser.add_argument('--end-time', type=int, default=(1 << 64) - 1, help='包含的最后 FST 时间')
    parser.add_argument('--min-checks', type=int, default=10, help='校准所需最少样本数')
    args = parser.parse_args()
    if args.end_time < 0 or args.min_checks < 1:
        parser.error('end-time 须非负且 min-checks 须为正')
    if any(os.path.abspath(args.csv) == os.path.abspath(p) for p in (args.fst, args.signals, args.fst_events)):
        parser.error('CSV 不得覆盖输入文件')
    try:
        return run(args)
    except (ValueError, OSError, RuntimeError) as exc:
        print('错误:', exc, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
