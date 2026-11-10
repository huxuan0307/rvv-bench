import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import campaign

class CampaignGateTests(unittest.TestCase):
    def test_gate_requires_mode_manifest_and_selected_cases(self):
        doc=dict(state='passed',mode='nemu',manifest={'snapshot_sha256':'abc'},cases=[{'name':'abi-public'}])
        with patch.object(campaign.collect,'collect',return_value=doc):
            self.assertEqual(campaign.require_gate(Path('/not-launched'),'nemu','abc',{'abi-public'}),doc)
            for mode,sha,want in [('gsim','abc',{'abi-public'}),('nemu','wrong',{'abi-public'}),('nemu','abc',{'perf-log'})]:
                with self.assertRaises(ValueError): campaign.require_gate(Path('/not-launched'),mode,sha,want)
    def test_partial_gate_does_not_pass(self):
        for state in ('failed','in_progress','aborted'):
            with patch.object(campaign.collect,'collect',return_value=dict(state=state,mode='gsim')):
                with self.assertRaises(ValueError):campaign.require_gate(Path('/not-launched'),'gsim','abc',{'abi-public'})
if __name__=='__main__':unittest.main()
