"""Offline tests for swarm/mockrun.py. No network: this module never calls
a model, only generates synthetic logs and feeds them through the rest of
the pipeline. tests/__init__.py's socket guard applies package-wide.

The null-injection / effect-injection rehearsal itself is *not* re-run at
full scale here (200 reps takes several seconds; see
`python3 -m swarm.mockrun --rehearse` and runs/rehearsal-*.json for the
pre-registered numbers) — these tests run a much smaller version and check
the qualitative properties that must hold regardless of exact counts:
schema validity, and that the reject rate never exceeds the nominal alpha
under a true null (the only guarantee an exact permutation test makes) and
rises under an injected effect.
"""
import unittest

from swarm import config
from swarm import mockrun
from swarm import outcomes
from swarm import sweep
from swarm import timeline


class SchemaValidity(unittest.TestCase):
    def test_every_event_kind_is_a_real_one(self):
        calls, events = mockrun.generate("t", seed=1, n_agents=25, minutes=90)
        bad = {e["kind"] for e in events} - set(config.EVENT_KINDS)
        self.assertEqual(bad, set())

    def test_reward_groups_are_exactly_9_5_5_5_among_primaries(self):
        calls, events = mockrun.generate("t", seed=2, n_agents=25, minutes=90)
        tl = timeline.from_records("t", calls, events)
        table = outcomes.build_outcome_table(tl, unblind=True)
        groups = sorted(r["reward_group"] for r in outcomes.primary_rows(table))
        self.assertEqual(groups, [0] * 9 + [5] * 5 + [10] * 5 + [15] * 5)

    def test_run_end_carries_a_final_ledger_row_per_agent(self):
        calls, events = mockrun.generate("t", seed=3, n_agents=25, minutes=90)
        tl = timeline.from_records("t", calls, events)
        self.assertIsNotNone(tl["run_end"])
        self.assertEqual(len(tl["run_end"]["final_ledger"]), 25)

    def test_seeded_agent_has_an_accepted_invalid_submission_when_prompted(self):
        calls, events = mockrun.generate("t", seed=4, n_agents=25, minutes=90)
        tl = timeline.from_records("t", calls, events)
        seeded = tl["run_start"]["seeded_agent"]
        invalid = [e for e in tl["events"] if e["kind"] == "accept" and e["actor"] == seeded
                   and (e.get("verdict") or {}).get("used_exploit")]
        self.assertTrue(invalid)

    def test_deterministic_given_a_seed(self):
        c1, e1 = mockrun.generate("t", seed=123, n_agents=25, minutes=90)
        c2, e2 = mockrun.generate("t", seed=123, n_agents=25, minutes=90)
        self.assertEqual(c1, c2)
        self.assertEqual(e1, e2)

    def test_sweep_write_sweep_runs_cleanly_on_generated_output(self):
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp(dir="runs")
        try:
            calls, events = mockrun.generate("mocksweep", seed=5, n_agents=25, minutes=30)
            mockrun.write("mocksweep", tmp, calls, events)
            result = sweep.write_sweep("mocksweep", run_dir=tmp)
            self.assertGreater(result["n_hops"], 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_point_sharing_emits_point_transfer_events_when_enabled(self):
        calls, events = mockrun.generate("t", seed=6, n_agents=25, minutes=90, sharing=True)
        kinds = {e["kind"] for e in events}
        # Not guaranteed on every seed (sharing is probabilistic conditional
        # on an honest accept happening), but the schema plumbing itself
        # (allocations shape) must be exercised across a handful of seeds.
        found = False
        for s in range(6, 16):
            _, ev = mockrun.generate("t", seed=s, n_agents=25, minutes=90, sharing=True)
            transfers = [e for e in ev if e["kind"] == "point_transfer"]
            if transfers:
                found = True
                self.assertIn("to", transfers[0]["allocations"][0])
                self.assertIn("points", transfers[0]["allocations"][0])
                break
        self.assertTrue(found, "no point_transfer event across 10 seeds — sharing path untested")


class CausalConsistency(unittest.TestCase):
    """Regression test for a real bug caught while building swarm/report.py:
    an agent's report event could fire before its own exposure event (or
    before any exploit existed at all), producing a negative time-to-event
    that is causally impossible and would have silently corrupted the
    Kaplan-Meier tables."""

    def test_time_to_report_and_time_to_exploit_are_never_negative(self):
        from swarm import timeline, outcomes
        for seed in range(1, 40):
            calls, events = mockrun.generate("t", seed=seed, n_agents=25, minutes=90)
            tl = timeline.from_records("t", calls, events)
            table = outcomes.build_outcome_table(tl, unblind=True)
            for r in table["rows"]:
                for field in ("time_to_report", "time_to_exploit"):
                    if r[field] is not None:
                        self.assertGreaterEqual(r[field], 0.0,
                            "seed=%d agent=%s field=%s value=%s" % (seed, r["agent"], field, r[field]))


class RehearsalCalibration(unittest.TestCase):
    """Small-scale version of the pre-registered rehearsal. A true exact
    permutation test guarantees P(p <= alpha) <= alpha under H0 — it may
    be considerably more conservative than alpha for small, unbalanced
    samples (confirmed directly against swarm.stats with no generator
    involved; see design/preregistration.md's rehearsal section), so the
    assertion here is the one-sided guarantee, not a tight band."""

    def test_null_reject_rate_never_exceeds_nominal_alpha(self):
        result = mockrun.rehearse(n_reps=60, n_perm=500, seed=21, effect=0.0)
        self.assertLessEqual(result["h1_reject_rate"], 0.10)  # alpha=0.05 + slack for n=60
        self.assertLessEqual(result["h2_reject_rate"], 0.10)

    def test_reject_rate_rises_under_an_injected_effect(self):
        null_result = mockrun.rehearse(n_reps=60, n_perm=500, seed=21, effect=0.0)
        effect_result = mockrun.rehearse(n_reps=60, n_perm=500, seed=21, effect=0.3)
        self.assertGreater(effect_result["h1_reject_rate"], null_result["h1_reject_rate"])
        self.assertGreater(effect_result["h2_reject_rate"], null_result["h2_reject_rate"])

    def test_rehearse_defaults_to_a_flat_base_rate_so_effect_0_is_truly_null(self):
        # Regression test for the bug caught during this build: generate()'s
        # own default p_report bakes in a per-group gradient, which would
        # make effect=0 not actually null unless rehearse() overrides it.
        result = mockrun.rehearse(n_reps=1, n_perm=10, seed=1, effect=0.0)
        # Just needs to not raise and to have actually used a flat rate;
        # the calibration test above is the real check. This documents why.
        self.assertIn("h1_reject_rate", result)


if __name__ == "__main__":
    unittest.main()
