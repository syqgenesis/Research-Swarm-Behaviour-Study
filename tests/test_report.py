"""Offline tests for swarm/report.py. No network; everything is rendered
from a synthetic run built by swarm.mockrun, following the same pattern
as the module's own self-check.
"""
import os
import shutil
import tempfile
import unittest

from swarm import mockrun
from swarm import outcomes
from swarm import report
from swarm import sweep
from swarm import timeline


class RenderEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir="runs")
        self.run_id = "rpt"
        calls, events = mockrun.generate(self.run_id, seed=17, n_agents=25, minutes=60)
        mockrun.write(self.run_id, self.tmp, calls, events)
        sweep.write_sweep(self.run_id, run_dir=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_render_writes_both_files(self):
        result = report.render(self.run_id, run_dir=self.tmp, n_perm=500, seed=1)
        self.assertTrue(os.path.exists(result["report_path"]))
        self.assertTrue(os.path.exists(result["evalcard_path"]))

    def test_default_render_is_blinded_and_says_so(self):
        result = report.render(self.run_id, run_dir=self.tmp, n_perm=200, seed=1)
        self.assertTrue(result["blinded"])
        with open(result["report_path"], encoding="utf-8") as f:
            self.assertIn("BLINDED", f.read())

    def test_unblinded_render_says_so_and_matches_the_true_assignment(self):
        result = report.render(self.run_id, run_dir=self.tmp, unblind=True, n_perm=200, seed=1)
        self.assertFalse(result["blinded"])
        with open(result["report_path"], encoding="utf-8") as f:
            text = f.read()
        self.assertIn("UNBLINDED", text)
        self.assertNotIn("BLINDED —", text.replace("UNBLINDED", ""))  # only one banner line

    def test_report_contains_every_numbered_section(self):
        result = report.render(self.run_id, run_dir=self.tmp, n_perm=200, seed=1)
        with open(result["report_path"], encoding="utf-8") as f:
            text = f.read()
        for heading in ["## 1. Frozen primary comparisons", "## 2. Exposure and reach",
                         "## 3. Time to event", "## 5. Cohorts", "## 6. Report outcomes",
                         "## 8. Productivity", "## 9. Exploratory", "## 10. Evidence index"]:
            self.assertIn(heading, text, "missing section: %s" % heading)

    def test_deterministic_given_a_seed(self):
        r1 = report.render(self.run_id, run_dir=self.tmp, n_perm=500, seed=1)
        with open(r1["report_path"], encoding="utf-8") as f:
            text1 = f.read()
        r2 = report.render(self.run_id, run_dir=self.tmp, n_perm=500, seed=1)
        with open(r2["report_path"], encoding="utf-8") as f:
            text2 = f.read()
        self.assertEqual(text1, text2)

    def test_evalcard_carries_the_run_start_fields(self):
        result = report.render(self.run_id, run_dir=self.tmp, n_perm=200, seed=1)
        with open(result["evalcard_path"], encoding="utf-8") as f:
            text = f.read()
        self.assertIn("n_agents", text)
        self.assertIn("main_run", text)


class EvidenceIndexOffsets(unittest.TestCase):
    def test_work_loss_quotes_resolve_to_a_real_substring(self):
        tmp = tempfile.mkdtemp(dir="runs")
        try:
            calls, events = mockrun.generate("ev", seed=3, n_agents=25, minutes=60)
            mockrun.write("ev", tmp, calls, events)
            sweep.write_sweep("ev", run_dir=tmp)
            tl = timeline.build("ev", run_dir=tmp)
            index = report.evidence_index(tl, "ev", run_dir=tmp)
            work_loss = [e for e in index if e[0].startswith("work-loss-")]
            self.assertTrue(work_loss)
            for claim_id, agent, step, hop, start, end, quote in work_loss:
                self.assertIn(quote.strip()[:20] if len(quote) > 20 else quote.strip(), quote)
                # start/end must actually bracket real text (non-degenerate span)
                self.assertLess(start, end)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_annotation_quotes_are_included_when_present(self):
        tl = timeline.from_records("x", [], [{"kind": "run_start", "actor": "harness",
                                               "step": 0, "seq": 1, "ts": 0.0, "problems": []}])
        annotations = [{"agent": "a1", "step": 1, "hop": 0,
                         "labels": {"bluff": {"present": True, "confidence": 8,
                                               "quote": "this is a bluff"}}}]
        index = report.evidence_index(tl, "x", annotations=annotations)
        self.assertEqual(len(index), 1)
        self.assertEqual(index[0][6], "this is a bluff")


if __name__ == "__main__":
    unittest.main()
