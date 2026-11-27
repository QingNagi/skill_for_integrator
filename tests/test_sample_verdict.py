"""Unconfirmed sample measurements must not produce an overall verdict."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'traced-template-fill' / 'scripts'))
from dilib.planner import Planner


class SampleVerdictTests(unittest.TestCase):
    def planner(self):
        p = object.__new__(Planner)
        p.questions = []
        p.answers = {}
        p.report = {'unverified_sources': [], 'unfilled': [], 'fills': []}
        p.ops = []
        p.missing_regions = []
        p.row_shift = {}
        p.scan_existing_fields = lambda: None
        p.doc_sources = lambda: {}
        p.oid = lambda: 'o'
        p.an = {'tables': [{'id': 'T0', 'fields': [], 'regions': [],
                            'blanks': [{'cell': 'T0.R2.C1', 'text': ' 只合格  只不合格'}]}]}
        return p

    def record(self, p, missing=False):
        res = {'verdict': {'1': 'pass', '2': 'fail'}, 'fills': {
            0: {'1': {'value': '' if missing else '2', 'how': '单值'}, '2': {'value': '4', 'how': '单值'}}}}
        rows = [{'sym': 'V', 'row': 0}, {'sym': 'C', 'row': 1, 'not_tested': True}]
        p.record_verdict('S2', 'T0.D1', rows, {'1': 2, '2': 3}, res)
        p.plan_fields()
        return [o for o in p.ops if o['op'] == 'fill_blanks']

    def test_unverified_selected_source_or_default_blocks_counts(self):
        for scenario in ('source', 'default', 'missing'):
            with self.subTest(scenario=scenario):
                p = self.planner()
                if scenario == 'source':
                    p.report['unverified_sources'].append({'key':'src:verify:S2'})
                if scenario == 'default':
                    p.answers['td:val:V'] = {'effect': {'mode':'raw'}, 'default': True}
                self.assertEqual(self.record(p, missing=(scenario == 'missing')), [])
                self.assertTrue(any(o.get('warn') for o in p.ops))

    def test_unrelated_missing_field_allows_confirmed_counts(self):
        p = self.planner()
        p.questions = [{'key':'fields:missing'}]
        p.report['unverified_sources'].append({'key':'src:verify:S3'})
        self.assertEqual(self.record(p)[0]['values'], ['1','1'])

    def test_all_regions_contribute_to_the_same_sample_verdict(self):
        p = self.planner()
        rows = [{"sym": "V", "row": 0}]
        cells = {sid: {"value": "4", "how": "单值"} for sid in ("1", "2")}
        for region, verdict in [("first", {"1": "fail", "2": "pass"}), ("second", {"1": "pass", "2": "fail"})]:
            p.record_verdict("S2", region, rows, {"1": 2, "2": 3}, {"verdict": verdict, "fills": {0: cells}})
        self.assertEqual(p.verdict, (0, 2, []))
