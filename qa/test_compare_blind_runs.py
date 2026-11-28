import copy
import unittest

from compare_blind_runs import compare, unexpected_changes


class BlindComparison(unittest.TestCase):
    def setUp(self):
        self.cohort = dict(expected={"unseen": "abc"}, repeats=1, cases=1, model="test",
                           scorer="sealed", payload_sha256="old")
        self.run = dict(case="unseen", repeat=1, returncode=0, total=2, expected_fills=1,
                        wrong_fills=0, missed_fills=0, correct_fills=1, correct_holds=1,
                        differences=[], seconds=20)
        self.next = dict(self.cohort, payload_sha256="new")

    def test_slower_correct_candidate_keeps_quality(self):
        result = compare(self.cohort, [self.run], self.next, [dict(self.run, seconds=40)])
        self.assertTrue(result["quality_passed"])
        self.assertEqual(result["paired"][0]["candidate_seconds"], 40)

    def test_faster_wrong_fill_or_missed_value_fails(self):
        for change in [dict(correct_holds=0, wrong_fills=1), dict(correct_fills=0, missed_fills=1)]:
            with self.subTest(change=change):
                candidate = dict(self.run, **change, differences=[{"row": 2, "column": 4}], seconds=1)
                result = compare(self.cohort, [self.run], self.next, [candidate])
                self.assertFalse(result["quality_passed"])
                self.assertEqual(result["paired"][0]["differences"], candidate["differences"])

    def test_changed_key_missing_scores_or_duplicate_runs_cannot_pass(self):
        other = copy.deepcopy(self.next)
        other["expected"]["unseen"] = "different"
        for new, rows in [(other, [self.run]), (self.next, []), (self.next, [self.run]*2),
                          (self.next, [dict(self.run, altered_inputs=["source.xlsx"])]),
                          (self.next, [dict(self.run, total=0)]),
                          (self.next, [dict(self.run, expected_fills=2)]),
                          (self.next, [dict(self.run, returncode=1)])]:
            with self.subTest(new=new, rows=rows), self.assertRaises(ValueError):
                compare(self.cohort, [self.run], new, rows)

    def test_identically_inconsistent_receipts_are_incomplete(self):
        broken = dict(self.run, expected_fills=2)
        with self.assertRaises(ValueError):
            compare(self.cohort, [broken], self.next, [broken])

    def test_transformation_preserves_display_and_allows_only_the_sealed_changes(self):
        before = {(2, 5): "1.35", (3, 5): "8"}
        after = {(2, 5): "1.35", (3, 5): ""}
        original = {(2, 5): "1.350", (3, 5): "8"}
        self.assertEqual(unexpected_changes(before, after, original, {(2, 5): "1.350", (3, 5): ""}), [])
        self.assertEqual(unexpected_changes(before, after, original, after), [(2, 5)])
        with self.assertRaises(ValueError):
            unexpected_changes(before, after, original, {(2, 5): "1.350"})
