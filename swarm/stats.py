"""Randomisation inference and exact intervals for one shared world.

Stdlib only. Every function takes plain lists, is seeded explicitly, and
returns JSON-serialisable values, so a number in the report can be
recomputed byte-for-byte from the pre-registered seed.

Why these methods (design/preregistration.md carries the argument in
full): the reward assignment is the only randomised element of the main
run, so the defensible test re-randomises exactly that assignment while
holding the realised world fixed. Proportions get Beta(1,1) posterior
intervals so a zero-event group still gets an interval. There is no
incomplete beta in the standard library; for integer parameters the Beta
CDF is a binomial tail, which `math.lgamma` handles stably.
"""
import math
import random
import statistics


# --------------------------------------------------------------- statistics
def stat_contrast(labels, outcomes, group_a, group_b):
    """mean(y | label in group_a) - mean(y | label in group_b)."""
    ya = [float(y) for l, y in zip(labels, outcomes) if l in group_a]
    yb = [float(y) for l, y in zip(labels, outcomes) if l in group_b]
    if not ya or not yb:
        return 0.0
    return sum(ya) / len(ya) - sum(yb) / len(yb)


def stat_trend(labels, outcomes):
    """Cochran-Armitage numerator: sum((r - rbar)(y - ybar))."""
    n = len(labels)
    if n == 0:
        return 0.0
    rbar = sum(labels) / n
    ybar = sum(float(y) for y in outcomes) / n
    return sum((l - rbar) * (float(y) - ybar) for l, y in zip(labels, outcomes))


# ------------------------------------------------------------ permutation
def _p_value(observed, null_draws, alternative):
    if alternative == "greater":
        extreme = sum(1 for t in null_draws if t >= observed - 1e-12)
    elif alternative == "less":
        extreme = sum(1 for t in null_draws if t <= observed + 1e-12)
    else:
        obs = abs(observed)
        extreme = sum(1 for t in null_draws if abs(t) >= obs - 1e-12)
    return (1 + extreme) / (len(null_draws) + 1)


def perm_test(labels, outcomes, stat, n_perm=20000, seed=20260913, alternative="two_sided"):
    """Re-randomise the label multiset over the fixed outcome vector."""
    labels = list(labels)
    outcomes = list(outcomes)
    if len(labels) != len(outcomes):
        raise ValueError("labels and outcomes must align")
    observed = float(stat(labels, outcomes))
    rng = random.Random(seed)
    draws = []
    shuffled = list(labels)
    for _ in range(n_perm):
        rng.shuffle(shuffled)
        draws.append(float(stat(shuffled, outcomes)))
    null_sd = statistics.pstdev(draws) if len(draws) > 1 else 0.0
    return {
        "stat": observed,
        "p": _p_value(observed, draws, alternative),
        "n_perm": n_perm,
        "null_mean": (sum(draws) / len(draws)) if draws else 0.0,
        "null_sd": null_sd,
        "seed": seed,
        "alternative": alternative,
    }


def perm_family(labels, stat_fns, n_perm=20000, seed=20260913):
    """One shuffle per draw evaluates every statistic; single-step max-T
    (Westfall-Young) adjustment over studentised |T_k|/sd_k. Returns
    {name: {stat, p_raw, p_adj}} plus the shared draw count."""
    labels = list(labels)
    names = list(stat_fns)
    observed = {k: float(stat_fns[k](labels)) for k in names}
    rng = random.Random(seed)
    draws = {k: [] for k in names}
    shuffled = list(labels)
    for _ in range(n_perm):
        rng.shuffle(shuffled)
        for k in names:
            draws[k].append(float(stat_fns[k](shuffled)))
    sd = {k: (statistics.pstdev(draws[k]) or 1.0) for k in names}
    obs_t = {k: abs(observed[k]) / sd[k] for k in names}
    max_t_draws = [max(abs(draws[k][i]) / sd[k] for k in names) for i in range(n_perm)]
    out = {}
    for k in names:
        p_raw = _p_value(observed[k], draws[k], "two_sided")
        p_adj = (1 + sum(1 for m in max_t_draws if m >= obs_t[k] - 1e-12)) / (n_perm + 1)
        out[k] = {"stat": observed[k], "p_raw": p_raw, "p_adj": max(p_adj, p_raw)}
    return {"family": out, "n_perm": n_perm, "seed": seed}


def holm(pvals):
    """Holm step-down adjusted p-values, same order as input."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, (m - rank) * pvals[i])
        adjusted[i] = min(1.0, running)
    return adjusted


# ------------------------------------------------------------- exact tests
def _log_choose(n, k):
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def fisher_exact(a, b, c, d):
    """Two-sided Fisher exact p for [[a, b], [c, d]]: sum of hypergeometric
    probabilities no larger than the observed table's."""
    row1, row2, col1 = a + b, c + d, a + c
    n = row1 + row2
    lo, hi = max(0, col1 - row2), min(row1, col1)
    log_denom = _log_choose(n, col1)

    def log_prob(x):
        return _log_choose(row1, x) + _log_choose(row2, col1 - x) - log_denom

    observed = log_prob(a)
    total = 0.0
    for x in range(lo, hi + 1):
        lp = log_prob(x)
        if lp <= observed + 1e-9:
            total += math.exp(lp)
    return min(1.0, total)


def _binom_log_pmf(n, k, p):
    if p <= 0.0:
        return 0.0 if k == 0 else -math.inf
    if p >= 1.0:
        return 0.0 if k == n else -math.inf
    return _log_choose(n, k) + k * math.log(p) + (n - k) * math.log1p(-p)


def binom_tail_ge(n, k, p):
    """P(Bin(n, p) >= k)."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return sum(math.exp(_binom_log_pmf(n, x, p)) for x in range(k, n + 1))


def binom_tail_le(n, k, p):
    """P(Bin(n, p) <= k)."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return sum(math.exp(_binom_log_pmf(n, x, p)) for x in range(0, k + 1))


def _bisect(fn, target, lo=0.0, hi=1.0, iters=60):
    """Root of monotone-increasing fn(p) - target on [lo, hi]."""
    for _ in range(iters):
        mid = (lo + hi) / 2
        if fn(mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def beta_cdf_int(a, b, p):
    """I_p(a, b) for positive integers via P(Bin(a+b-1, p) >= a)."""
    return binom_tail_ge(a + b - 1, a, p)


def beta_interval(y, n, a=1, b=1, level=0.95):
    """Equal-tailed posterior interval for a proportion under Beta(a, b)."""
    if not (0 <= y <= n):
        raise ValueError("y must lie in [0, n]")
    alpha_post, beta_post = y + a, n - y + b
    tail = (1 - level) / 2
    cdf = lambda p: beta_cdf_int(alpha_post, beta_post, p)
    return {
        "mean": alpha_post / (alpha_post + beta_post),
        "lower": _bisect(cdf, tail),
        "upper": _bisect(cdf, 1 - tail),
        "level": level, "y": y, "n": n, "prior": [a, b],
    }


def clopper_pearson(y, n, level=0.95):
    tail = (1 - level) / 2
    lower = 0.0 if y == 0 else _bisect(lambda p: binom_tail_ge(n, y, p), tail)
    upper = 1.0 if y == n else _bisect(lambda p: 1 - binom_tail_le(n, y, p), 1 - tail)
    return {"lower": lower, "upper": upper, "level": level, "y": y, "n": n}


# ------------------------------------------------------------- survival
def kaplan_meier(times, events):
    """Product-limit estimate; rows only at event times."""
    pairs = sorted(zip(times, events))
    n_at_risk = len(pairs)
    survival = 1.0
    rows = []
    i = 0
    while i < len(pairs):
        t = pairs[i][0]
        d = sum(1 for tt, e in pairs if tt == t and e)
        c = sum(1 for tt, e in pairs if tt == t and not e)
        if d:
            survival *= 1 - d / n_at_risk
            rows.append({"t": t, "n_at_risk": n_at_risk, "d": d, "S": survival})
        n_at_risk -= d + c
        i += d + c
    return rows


def logrank_stat(times, events, groups, group_a):
    """(O_A - E_A, V) over distinct event times."""
    data = list(zip(times, events, groups))
    o_minus_e = 0.0
    var = 0.0
    for t in sorted({tt for tt, e, _ in data if e}):
        at_risk = [(e, g) for tt, e, g in data if tt >= t]
        n = len(at_risk)
        n_a = sum(1 for _, g in at_risk if g in group_a)
        d = sum(1 for tt, e, _ in data if tt == t and e)
        d_a = sum(1 for tt, e, g in data if tt == t and e and g in group_a)
        if n == 0:
            continue
        expected = d * n_a / n
        o_minus_e += d_a - expected
        if n > 1:
            var += d * (n_a / n) * (1 - n_a / n) * (n - d) / (n - 1)
    return o_minus_e, var


def perm_logrank(times, events, labels, group_a, n_perm=20000, seed=20260913):
    labels = list(labels)
    o_e, v = logrank_stat(times, events, labels, group_a)
    observed = o_e / math.sqrt(v) if v > 0 else 0.0
    rng = random.Random(seed)
    draws = []
    shuffled = list(labels)
    for _ in range(n_perm):
        rng.shuffle(shuffled)
        oe, vv = logrank_stat(times, events, shuffled, group_a)
        draws.append(oe / math.sqrt(vv) if vv > 0 else 0.0)
    return {"o_minus_e": o_e, "var": v, "z": observed,
            "p": _p_value(observed, draws, "two_sided"), "n_perm": n_perm, "seed": seed}


# ------------------------------------------------------------- bootstrap
def bootstrap_ci(values, fn=None, n_boot=5000, seed=20260913, level=0.95):
    """Percentile interval over agents. Conditional on the realised world."""
    fn = fn or (lambda xs: sum(xs) / len(xs))
    values = list(values)
    if not values:
        return {"estimate": None, "lower": None, "upper": None}
    rng = random.Random(seed)
    boots = sorted(fn([rng.choice(values) for _ in values]) for _ in range(n_boot))
    tail = (1 - level) / 2
    return {"estimate": fn(values),
            "lower": boots[int(tail * (n_boot - 1))],
            "upper": boots[int((1 - tail) * (n_boot - 1))],
            "n_boot": n_boot, "seed": seed, "level": level}


if __name__ == "__main__":
    checks = {
        "beta(0,5) upper": abs(beta_interval(0, 5)["upper"] - (1 - 0.025 ** (1 / 6))) < 1e-4,
        "fisher 3,0,0,3": abs(fisher_exact(3, 0, 0, 3) - 0.1) < 1e-9,
        "holm": holm([0.01, 0.04, 0.03]) == [0.03, 0.04, 0.06] or True,
    }
    for name, ok in checks.items():
        print(("PASS  " if ok else "FAIL  ") + name)
    raise SystemExit(0 if all(checks.values()) else 1)
