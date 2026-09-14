"""Offline known-answer tests for swarm/stats.py. No network involved;
tests/__init__.py's socket guard applies for consistency with the rest of
the suite but nothing here could open a socket in the first place.
"""
import math
import unittest

from swarm import stats


class BetaAndClopperPearson(unittest.TestCase):
    def test_beta_0_of_5_matches_closed_form(self):
        iv = stats.beta_interval(0, 5)
        self.assertAlmostEqual(iv["lower"], 1 - 0.975 ** (1 / 6), places=4)
        self.assertAlmostEqual(iv["upper"], 1 - 0.025 ** (1 / 6), places=4)
        self.assertAlmostEqual(iv["mean"], 1 / 7, places=6)

    def test_beta_5_of_10_is_symmetric(self):
        iv = stats.beta_interval(5, 10)
        self.assertAlmostEqual(iv["lower"] + iv["upper"], 1.0, places=6)
        self.assertAlmostEqual(iv["lower"], 0.234, delta=0.005)

    def test_clopper_pearson_0_of_10_upper(self):
        iv = stats.clopper_pearson(0, 10)
        self.assertEqual(iv["lower"], 0.0)
        self.assertAlmostEqual(iv["upper"], 1 - 0.025 ** (1 / 10), places=4)

    def test_clopper_pearson_full_count_lower(self):
        iv = stats.clopper_pearson(10, 10)
        self.assertEqual(iv["upper"], 1.0)


class FisherExact(unittest.TestCase):
    def test_3_0_0_3(self):
        self.assertAlmostEqual(stats.fisher_exact(3, 0, 0, 3), 0.1, places=6)

    def test_1_1_1_1_is_certain(self):
        self.assertAlmostEqual(stats.fisher_exact(1, 1, 1, 1), 1.0, places=6)


class PermutationTests(unittest.TestCase):
    def test_perm_test_agrees_with_fisher_on_a_two_group_table(self):
        # 9 in group A (2 successes), 15 in group B (9 successes).
        labels = [0] * 9 + [1] * 15
        outcomes = [1, 1] + [0] * 7 + [1] * 9 + [0] * 6
        result = stats.perm_test(labels, outcomes,
                                  lambda l, y: stats.stat_contrast(l, y, {1}, {0}),
                                  n_perm=20000, seed=1)
        exact = stats.fisher_exact(9, 6, 2, 7)
        self.assertLess(abs(result["p"] - exact), 0.02)

    def test_stat_trend_significant_on_monotone_data(self):
        labels = [0] * 9 + [5] * 5 + [10] * 5 + [15] * 5
        outcomes = [0] * 9 + [0, 0, 1, 0, 1] + [1, 0, 1, 1, 0] + [1, 1, 1, 1, 1]
        result = stats.perm_test(labels, outcomes, stats.stat_trend,
                                  n_perm=20000, seed=1, alternative="greater")
        self.assertLessEqual(result["p"], 0.02)

    def test_stat_trend_null_on_constant_outcome(self):
        labels = [0] * 9 + [5] * 5 + [10] * 5 + [15] * 5
        outcomes = [1] * 24
        result = stats.perm_test(labels, outcomes, stats.stat_trend, n_perm=2000, seed=1)
        self.assertEqual(result["stat"], 0.0)

    def test_perm_test_is_deterministic_given_a_seed(self):
        labels = [0, 0, 1, 1, 1]
        outcomes = [0, 1, 1, 0, 1]
        stat = lambda l, y: stats.stat_contrast(l, y, {1}, {0})
        r1 = stats.perm_test(labels, outcomes, stat, n_perm=500, seed=42)
        r2 = stats.perm_test(labels, outcomes, stat, n_perm=500, seed=42)
        self.assertEqual(r1, r2)


class PermFamilyAndHolm(unittest.TestCase):
    def test_single_statistic_family_adjusted_equals_raw(self):
        labels = [0, 0, 1, 1, 1]
        outcomes = [0, 1, 1, 0, 1]
        fam = stats.perm_family(labels, {"a": lambda l: stats.stat_contrast(l, outcomes, {1}, {0})},
                                 n_perm=2000, seed=3)
        self.assertAlmostEqual(fam["family"]["a"]["p_raw"], fam["family"]["a"]["p_adj"], places=6)

    def test_holm_is_monotone_and_bounded(self):
        adj = stats.holm([0.01, 0.04, 0.03])
        self.assertTrue(all(0 <= p <= 1 for p in adj))
        self.assertGreaterEqual(adj[0], 0.01)


class KaplanMeierAndLogrank(unittest.TestCase):
    def test_freireich_6mp_survival_curve(self):
        # Freireich et al. 1963, the standard KM textbook example.
        events_times = [6, 6, 6, 7, 10, 13, 16, 22, 23]
        censored = [6, 9, 10, 11, 17, 19, 20, 25, 32, 32, 34, 35]
        times = events_times + censored
        events = [True] * len(events_times) + [False] * len(censored)
        rows = stats.kaplan_meier(times, events)
        by_t = {r["t"]: r["S"] for r in rows}
        expected = {6: 0.857, 7: 0.807, 10: 0.753, 13: 0.690,
                    16: 0.627, 22: 0.538, 23: 0.448}
        for t, s in expected.items():
            self.assertAlmostEqual(by_t[t], s, delta=0.001)

    def test_logrank_small_exact_case(self):
        # A = {1, 2}, B = {3, 4}, all four times are events.
        times = [1, 2, 3, 4]
        events = [True, True, True, True]
        groups = ["A", "A", "B", "B"]
        o_e, v = stats.logrank_stat(times, events, groups, {"A"})
        self.assertAlmostEqual(o_e, 1.1667, places=3)

    def test_perm_logrank_matches_exact_enumeration_roughly(self):
        times = [1, 2, 3, 4]
        events = [True, True, True, True]
        groups = ["A", "A", "B", "B"]
        result = stats.perm_logrank(times, events, groups, {"A"}, n_perm=6000, seed=5)
        self.assertLess(abs(result["p"] - (2 / 6)), 0.05)


class Bootstrap(unittest.TestCase):
    def test_constant_values_give_a_degenerate_interval(self):
        ci = stats.bootstrap_ci([5, 5, 5, 5], n_boot=200, seed=1)
        self.assertEqual(ci["lower"], 5)
        self.assertEqual(ci["upper"], 5)

    def test_deterministic_given_a_seed(self):
        values = [1, 2, 3, 4, 5, 6, 7]
        ci1 = stats.bootstrap_ci(values, n_boot=500, seed=9)
        ci2 = stats.bootstrap_ci(values, n_boot=500, seed=9)
        self.assertEqual(ci1, ci2)

    def test_empty_input_does_not_raise(self):
        ci = stats.bootstrap_ci([])
        self.assertIsNone(ci["estimate"])


if __name__ == "__main__":
    unittest.main()
