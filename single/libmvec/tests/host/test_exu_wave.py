#!/usr/bin/env python3
"""Exu 动态波形重建的主机单测，不依赖仿真器。"""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "script"
sys.path.insert(0, str(SCRIPT))

import exu_wave as w


def frame():
    s = {'redirect.valid': 0, 'redirect.flag': 0, 'redirect.value': 0,
         'redirect.level': 0, 'enq.accept': 0, 'commit.is': 0}
    for i in range(8):
        s[f'enq.{i}.valid'] = 0
        s[f'commit.{i}.valid'] = 0
    for i in range(6):
        s[f'p{i}.flush'] = 0
        s[f'p{i}.ex.valid'] = 0
        s[f'p{i}.out.valid'] = 0
    for i in range(len(w.MEM_PIPES)):
        s[f'm{i}.hold'] = 0
        s[f'm{i}.flush'] = 0
    return s


def enq(s, slot, flag, value, pc):
    s['enq.accept'] = 1
    s.update({f'enq.{slot}.valid': 1, f'enq.{slot}.first': 1,
              f'enq.{slot}.flag': flag, f'enq.{slot}.value': value, f'enq.{slot}.pc': pc})


def entry(s, pipe, flag, value, uop, latency=1, debug=None, vfmac=True):
    p = f'p{pipe}'
    s.update({p + '.ex.valid': 1, p + '.ex.flag': flag, p + '.ex.value': value,
              p + '.ex.uop': uop, p + '.ex.debug_uop': uop if debug is None else debug,
              p + '.ex.seq': 0, p + '.ex.fu': 1, p + '.ex.opcode': 2,
              p + '.ex.latency': latency})
    if pipe == 0 and vfmac:
        s['p0.vfmac.in'] = 1


def output(s, pipe, flag, value, uop, latency=1):
    p = f'p{pipe}'
    s.update({p + '.out.valid': 1, p + '.out.flag': flag,
              p + '.out.value': value, p + '.out.debug_uop': uop})
    if pipe == 0:
        s[f'p0.vfmac.out{latency}'] = 1


def mem(s, pipe, flag, value, uop, ready, cancel=0, replace=0, mismatch=False):
    p = f'm{pipe}'
    s.update({p + '.ex.valid': 1, p + '.ex.flag': flag, p + '.ex.value': value,
              p + '.ex.uop': uop, p + '.ex.debug_uop': uop, p + '.ex.seq': 0,
              p + '.valid': 1, p + '.ready': ready, p + '.cancel': cancel,
              p + '.replace': replace, p + '.hold': int(not ready and not cancel and not replace),
              p + '.out.flag': flag, p + '.out.value': value + mismatch, p + '.out.uop': uop,
              p + '.lsu.valid': 1, p + '.lsu.ready': ready, p + '.lsu.meta': 0,
              p + '.lsu.flag': flag, p + '.lsu.value': value,
              p + '.lsu.uop': uop, p + '.lsu.debug_uop': uop})


def feed(tracker, cycle, state):
    tracker.step(cycle, cycle, state)


class TrackerTests(unittest.TestCase):
    def test_multi_uop_and_same_cycle(self):
        t = w.Tracker(True)
        s = frame(); enq(s, 0, 0, 9, 0x100); feed(t, 1, s)
        s = frame(); entry(s, 0, 0, 9, 0); feed(t, 2, s)
        s = frame(); entry(s, 0, 0, 9, 1); output(s, 0, 0, 9, 0); feed(t, 3, s)
        s = frame(); output(s, 0, 0, 9, 1); feed(t, 4, s)
        self.assertEqual([r['uop_idx'] for r in t.rows], [0, 1])
        self.assertEqual([r['elapsed_cycles'] for r in t.rows], [1, 1])
        self.assertEqual({r['generation'] for r in t.rows}, {1})
        self.assertEqual({r['pc'] for r in t.rows}, {'0x100'})
        self.assertEqual({r['certainty'] for r in t.rows}, {'verified_vfmac'})

    def test_same_cycle_zero_latency_and_unknown_fu(self):
        t = w.Tracker(True)
        s = frame(); enq(s, 0, 0, 9, 0x100); feed(t, 1, s)
        s = frame(); entry(s, 1, 0, 9, 0, latency=0, vfmac=False); output(s, 1, 0, 9, 0); feed(t, 2, s)
        r = t.rows[0]
        self.assertEqual((r['entry_fst_time'], r['exit_fst_time']), (2, 2))
        self.assertEqual(r['elapsed_cycles'], '')
        self.assertEqual(r['certainty'], 'uncertain')
        self.assertIn('fu_acceptance_not_validated', r['reason'])

    def test_rob_wrap_and_reuse_generation(self):
        t = w.Tracker(True)
        for c, flag, pc in [(1, 0, 0x100), (4, 1, 0x200), (7, 0, 0x300)]:
            s = frame(); enq(s, 0, flag, 9, pc); feed(t, c, s)
            s = frame(); entry(s, 0, flag, 9, 0); feed(t, c + 1, s)
            s = frame(); output(s, 0, flag, 9, 0); feed(t, c + 2, s)
        self.assertEqual([r['generation'] for r in t.rows], [1, 2, 3])
        self.assertEqual([r['pc'] for r in t.rows], ['0x100', '0x200', '0x300'])

    def test_flush_flag_wrap_and_self(self):
        self.assertTrue(w.need_flush((1, 2), (0, 500), 0))
        self.assertFalse(w.need_flush((0, 500), (0, 500), 0))
        self.assertTrue(w.need_flush((0, 500), (0, 500), 1))
        t = w.Tracker(True)
        s = frame(); enq(s, 0, 1, 2, 0x100); feed(t, 1, s)
        s = frame(); entry(s, 0, 1, 2, 0); feed(t, 2, s)
        s = frame(); s.update({'redirect.valid': 1, 'redirect.flag': 0,
                               'redirect.value': 500, 'redirect.level': 0})
        for i in range(6): s[f'p{i}.flush'] = 1
        feed(t, 3, s)
        self.assertEqual(t.rows[0]['outcome'], 'flushed')
        self.assertEqual(t.rows[0]['exit_fst_time'], None)
        self.assertEqual(t.rows[0]['elapsed_cycles'], '')

    def test_rejected_divider_and_replay_not_fabricated(self):
        t = w.Tracker(True)
        s = frame(); enq(s, 0, 0, 9, 0x100); feed(t, 1, s)
        s = frame(); entry(s, 1, 0, 9, 0, vfmac=False)
        s.update({'p1.div.valid': 1, 'p1.div.accepted': 0}); feed(t, 2, s)
        s = frame(); entry(s, 1, 0, 9, 0, vfmac=False); feed(t, 3, s)
        s = frame(); output(s, 1, 0, 9, 0); feed(t, 4, s)
        self.assertEqual([r['outcome'] for r in t.rows], ['rejected', 'completed'])
        self.assertEqual([r['attempt'] for r in t.rows], [1, 2])
        self.assertIn('reissue_or_replay_requires_validation', t.rows[1]['reason'])
        self.assertEqual(t.rows[1]['certainty'], 'uncertain')

    def test_overlapping_same_uop_is_ambiguous(self):
        t = w.Tracker(True)
        s = frame(); enq(s, 0, 0, 9, 0x100); feed(t, 1, s)
        s = frame(); entry(s, 0, 0, 9, 0, latency=3); feed(t, 2, s)
        s = frame(); entry(s, 0, 0, 9, 0, latency=2); feed(t, 3, s)
        s = frame(); output(s, 0, 0, 9, 0, latency=2); feed(t, 5, s)
        self.assertEqual([r['outcome'] for r in t.rows], ['ambiguous', 'ambiguous'])
        self.assertTrue(all(r['exit_fst_time'] is None for r in t.rows))

    def test_unknown_mapping_and_uncalibrated_time(self):
        t = w.Tracker(False)
        s = frame(); entry(s, 0, 0, 9, 0); feed(t, 1, s)
        s = frame(); output(s, 0, 0, 9, 0); feed(t, 2, s)
        r = t.rows[0]
        self.assertEqual(r['pc'], '')
        self.assertEqual(r['generation'], None)
        self.assertEqual(r['entry_cycle'], '')
        self.assertEqual(r['elapsed_cycles'], '')
        self.assertIn('allocation_not_observed', r['reason'])
        self.assertIn('uncalibrated_timebase', r['reason'])

    def test_divergent_debug_uop_mapping_uncertain(self):
        t = w.Tracker(True)
        s = frame(); enq(s, 0, 0, 9, 0x100); feed(t, 1, s)
        s = frame(); entry(s, 0, 0, 9, 2, debug=6); feed(t, 2, s)
        s = frame(); output(s, 0, 0, 9, 6); feed(t, 3, s)
        self.assertEqual(t.rows[0]['certainty'], 'uncertain')
        self.assertIn('control_debug_uop_mapping_unverified', t.rows[0]['reason'])

    def test_vstd_success_is_not_lsu_acceptance(self):
        t = w.Tracker(True)
        s = frame(); enq(s, 0, 0, 9, 0x100); feed(t, 1, s)
        s = frame(); entry(s, 4, 0, 9, 0, vfmac=False)
        s['p4.vstd.success'] = 1; output(s, 4, 0, 9, 0); feed(t, 2, s)
        self.assertEqual(t.rows[0]['outcome'], 'vstd_response_unresolved')
        self.assertIsNone(t.rows[0]['exit_fst_time'])
        self.assertEqual(t.rows[0]['elapsed_cycles'], '')

    def test_memory_hold_and_lsu_handshake(self):
        t = w.Tracker(True)
        s = frame(); enq(s, 0, 0, 9, 0x100); feed(t, 1, s)
        for c in (2, 3, 4):
            s = frame(); mem(s, 0, 0, 9, 0, 0); feed(t, c, s)
        s = frame(); mem(s, 0, 0, 9, 0, 1); feed(t, 5, s)
        self.assertEqual(len(t.rows), 1)
        self.assertEqual((t.rows[0]['entry_cycle'], t.rows[0]['exit_cycle']), (2, 5))
        self.assertEqual((t.rows[0]['elapsed_cycles'], t.rows[0]['outcome']), (3, 'lsu_accepted'))
        self.assertEqual(t.rows[0]['certainty'], 'verified_lsu')

    def test_memory_cancel_and_replacement_then_retry(self):
        for terminal in ('cancel', 'replace'):
            t = w.Tracker(True)
            s = frame(); enq(s, 0, 0, 9, 0x100); feed(t, 1, s)
            s = frame(); mem(s, 0, 0, 9, 0, 0); feed(t, 2, s)
            s = frame(); mem(s, 0, 0, 9, 0, 0, **{terminal: 1}); feed(t, 3, s)
            s = frame(); mem(s, 0, 0, 9, 0, 1); feed(t, 4, s)
            self.assertEqual(t.rows[0]['outcome'], 'cancelled' if terminal == 'cancel' else 'rejected')
            self.assertEqual(t.rows[0]['elapsed_cycles'], '')
            self.assertEqual(t.rows[1]['outcome'], 'lsu_accepted')
            self.assertEqual(t.rows[1]['certainty'], 'uncertain')
            self.assertIn('reissue_or_replay_requires_validation', t.rows[1]['reason'])

    def test_vagq_handoff_is_not_lsu_accept(self):
        t = w.Tracker(True)
        s = frame(); enq(s, 0, 0, 9, 0x100); feed(t, 1, s)
        s = frame(); mem(s, 0, 0, 9, 0, 1)
        s['m0.lsu.meta'] = 1; feed(t, 2, s)
        self.assertEqual(t.rows[0]['outcome'], 'memory_handoff_only')
        self.assertIsNone(t.rows[0]['exit_fst_time'])
        self.assertEqual(t.rows[0]['elapsed_cycles'], '')
        self.assertIn('vagq_input_not_lsu_acceptance', t.rows[0]['reason'])

    def test_memory_request_identity_mismatch_and_initial_hold(self):
        t = w.Tracker(True)
        s = frame(); mem(s, 0, 0, 9, 0, 1, mismatch=True); feed(t, 1, s)
        self.assertEqual(t.rows[0]['certainty'], 'uncertain')
        self.assertIn('memory_entry_boundary_not_proven', t.rows[0]['reason'])
        self.assertIn('memory_request_identity_mismatch', t.rows[0]['reason'])
        self.assertIn('allocation_not_observed', t.rows[0]['reason'])

    def test_cross_pipe_reissue_downgrades_earlier_output(self):
        t = w.Tracker(True)
        s = frame(); enq(s, 0, 0, 9, 0x100); feed(t, 1, s)
        s = frame(); entry(s, 0, 0, 9, 0); feed(t, 2, s)
        s = frame(); output(s, 0, 0, 9, 0); feed(t, 3, s)
        self.assertEqual(t.rows[0]['certainty'], 'verified_vfmac')
        s = frame(); entry(s, 1, 0, 9, 0, vfmac=False); feed(t, 4, s)
        s = frame(); output(s, 1, 0, 9, 0); feed(t, 5, s)
        t.finish()
        self.assertEqual([r['attempt'] for r in t.rows], [1, 2])
        self.assertEqual([r['certainty'] for r in t.rows], ['uncertain', 'uncertain'])
        self.assertEqual(t.rows[0]['elapsed_cycles'], '')
        self.assertIn('multiple_attempts_across_pipes', t.rows[0]['reason'])
        self.assertEqual(t.stats['verified_vfmac'], 0)

    def test_unknown_out_valid_taints_pending(self):
        t = w.Tracker(True)
        s = frame(); enq(s, 0, 0, 9, 0x100); feed(t, 1, s)
        s = frame(); entry(s, 0, 0, 9, 0, latency=3); feed(t, 2, s)
        s = frame(); s['p0.out.valid'] = None; feed(t, 3, s)
        s = frame(); output(s, 0, 0, 9, 0, latency=3); feed(t, 5, s)
        self.assertEqual(t.rows[0]['outcome'], 'signal_unknown')
        self.assertEqual(t.rows[0]['elapsed_cycles'], '')
        self.assertEqual(t.stats['unmatched_output'], 1)

    def test_unknown_rob_controls_discard_generation(self):
        t = w.Tracker(True)
        s = frame(); enq(s, 0, 0, 9, 0x100); feed(t, 1, s)
        s = frame(); s['enq.accept'] = None; feed(t, 2, s)
        s = frame(); entry(s, 0, 0, 9, 0); feed(t, 3, s)
        s = frame(); output(s, 0, 0, 9, 0); feed(t, 4, s)
        self.assertIsNone(t.rows[-1]['generation'])
        self.assertIn('allocation_not_observed', t.rows[-1]['reason'])

    def test_partially_unknown_allocation_invalidates_entire_beat(self):
        t = w.Tracker(True)
        s = frame(); enq(s, 0, 0, 9, 0x100)
        s['enq.1.valid'] = 1; s['enq.1.first'] = None; feed(t, 1, s)
        s = frame(); entry(s, 0, 0, 9, 0); feed(t, 2, s)
        s = frame(); output(s, 0, 0, 9, 0); feed(t, 3, s)
        self.assertEqual(t.stats['unknown_rob_control'], 1)
        self.assertIsNone(t.rows[0]['generation'])
        self.assertEqual(t.rows[0]['certainty'], 'uncertain')

    def test_simultaneous_commit_cannot_be_entry_or_exit(self):
        t = w.Tracker(True)
        s = frame(); enq(s, 0, 0, 9, 0x100); feed(t, 1, s)
        s = frame(); entry(s, 0, 0, 9, 0); feed(t, 2, s)
        s = frame(); s.update({'commit.is': 1, 'commit.0.valid': 1,
                               'commit.0.flag': 0, 'commit.0.value': 9}); feed(t, 3, s)
        self.assertEqual(t.rows[0]['outcome'], 'retired_without_exu_output')
        self.assertIsNone(t.rows[0]['exit_fst_time'])


class CalibrationTests(unittest.TestCase):
    def calibration_stream(self, phase_shift=False, discontinuity=False):
        states = []
        for i in range(6):
            s = {'p0.flush': 0, 'p0.in.valid': 1,
                 'p0.in.flag': 0, 'p0.in.value': i + 1, 'p0.in.uop': 0,
                 'p0.ex.valid': 1, 'p0.ex.flag': 0, 'p0.ex.value': i,
                 'p0.ex.uop': 0, 'p0.ex.debug_uop': 0, 'p0.ex.latency': 1,
                 'p0.vfmac.in': 1, 'p0.vfmac.out1': 1, 'p0.out.valid': 1,
                 'p0.out.flag': 0, 'p0.out.value': i - 1 + phase_shift,
                 'p0.out.debug_uop': 0}
            states.append((i, i + (1 if discontinuity and i > 3 else 0), s))
        return states

    def test_fixed_latency_and_timer_calibration(self):
        with patch.object(w, 'snapshots', return_value=iter(self.calibration_stream())):
            stats = w.calibrate('', '', {}, 100, 3)
        self.assertTrue(stats['calibrated'])
        self.assertEqual((stats['in_matches'], stats['vfmac_matches']), (5, 5))

    def test_phase_shift_or_timer_gap_refused(self):
        for kwargs in ({'phase_shift': True}, {'discontinuity': True}):
            with patch.object(w, 'snapshots', return_value=iter(self.calibration_stream(**kwargs))):
                stats = w.calibrate('', '', {}, 100, 3)
            self.assertFalse(stats['calibrated'])


if __name__ == '__main__':
    unittest.main()
