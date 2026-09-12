"""Bounded witness grammars and pure combinatorial checks; no submitted code runs.

These functions take plain values, never a DSL environment. The grader and live
candidate tool use this verifier; tests carry separately written reference checks.
"""
from itertools import combinations

from swarm import config

MAX_WITNESS_CHARS = 4096


def parse_witness(candidate, kind):
    if not isinstance(candidate, str) or len(candidate) > MAX_WITNESS_CHARS:
        return ()
    candidate = candidate.strip()
    if not candidate:
        return ()
    if kind in ("clique", "subset_sum"):
        tokens = [part.strip() for part in candidate.split(",")]
        if len(tokens) > 256 or any(not t.isascii() or not t.isdecimal() or len(t) > 4 for t in tokens):
            return ()
        values = tuple(int(t) for t in tokens)
        return values if min(values) > 0 and len(set(values)) == len(values) else ()
    alphabet = {"sat": "TF", "discrepancy": "+-", "vanderwaerden": "0123456789"}.get(kind, "")
    if len(candidate) > 256 or any(c not in alphabet for c in candidate):
        return ()
    if kind == "sat":
        return tuple(c == "T" for c in candidate)
    if kind == "discrepancy":
        return tuple(1 if c == "+" else -1 for c in candidate)
    return tuple(int(c) for c in candidate)


def _indices(w, n):
    return (isinstance(w, tuple) and bool(w) and len(w) <= n
            and all(type(i) is int and 1 <= i <= n for i in w)
            and len(set(w)) == len(w))


def is_clique(w, rows, k):
    return (_indices(w, len(rows)) and len(w) == k
            and all(rows[a - 1][b - 1] == "1" for a, b in combinations(w, 2)))


def sums_to(w, nums, total):
    return _indices(w, len(nums)) and sum(nums[i - 1] for i in w) == total


def satisfies(w, clauses, n):
    return (isinstance(w, tuple) and len(w) == n and all(type(x) is bool for x in w)
            and all(any(w[abs(lit) - 1] == (lit > 0) for lit in clause) for clause in clauses))


def within_bound(w, sets, bound):
    return (isinstance(w, tuple) and bool(sets) and len(w) == len(sets[0])
            and all(type(x) is int and x in (-1, 1) for x in w)
            and all(abs(sum(v for v, bit in zip(w, row) if bit == "1")) <= bound for row in sets))


def ap_free(w, k, colours, n):
    if not isinstance(w, tuple) or len(w) != n or any(type(x) is not int or not 0 <= x < colours for x in w):
        return False
    return not any(all(w[a + j * d] == w[a] for j in range(1, k))
                   for d in range(1, (n - 1) // (k - 1) + 1)
                   for a in range(n - (k - 1) * d))


CHECKS = {
    "clique": (is_clique, ("adj", "k")),
    "subset_sum": (sums_to, ("nums", "total")),
    "sat": (satisfies, ("clauses", "n")),
    "discrepancy": (within_bound, ("sets", "bound")),
    "vanderwaerden": (ap_free, ("k", "colours", "n")),
}


def well_formed(problem):
    """Validate external data before a builtin can index it or do bounded work."""
    try:
        if not isinstance(problem, dict) or problem.get("kind") not in CHECKS:
            return False
        n = problem.get("n")
        if type(n) is not int or not 1 <= n <= 64:
            return False
        kind = problem["kind"]
        if kind == "clique":
            rows = problem["adj"]
            return (type(problem["k"]) is int and 1 <= problem["k"] <= n
                    and isinstance(rows, (tuple, list)) and len(rows) == n
                    and all(isinstance(r, str) and len(r) == n and set(r) <= {"0", "1"} for r in rows)
                    and all(rows[i][i] == "0" for i in range(n))
                    and all(rows[i][j] == rows[j][i] for i in range(n) for j in range(i)))
        if kind == "subset_sum":
            nums = problem["nums"]
            return (isinstance(nums, (tuple, list)) and len(nums) == n
                    and all(type(x) is int and 0 < x <= config.EVAL_MAX_ABS for x in nums)
                    and type(problem["total"]) is int and 0 < problem["total"] <= config.EVAL_MAX_ABS)
        if kind == "sat":
            clauses = problem["clauses"]
            return (isinstance(clauses, (tuple, list)) and 1 <= len(clauses) <= 10000
                    and type(problem["m"]) is int and len(clauses) == problem["m"]
                    and all(isinstance(c, (tuple, list)) and len(c) == 3
                            and all(type(x) is int and 1 <= abs(x) <= n for x in c)
                            and len({abs(x) for x in c}) == 3 for c in clauses))
        if kind == "discrepancy":
            rows = problem["sets"]
            return (isinstance(rows, (tuple, list)) and 1 <= len(rows) <= 256
                    and type(problem["m"]) is int and len(rows) == problem["m"]
                    and all(isinstance(r, str) and len(r) == n and set(r) <= {"0", "1"} for r in rows)
                    and type(problem["bound"]) is int and 0 <= problem["bound"] <= n)
        return (type(problem["k"]) is int and 2 <= problem["k"] <= n
                and type(problem["colours"]) is int and 2 <= problem["colours"] <= 10)
    except (KeyError, TypeError, ValueError, IndexError):
        return False


def really_solves(candidate, problem):
    """Total validity check independent of the submission's names and definitions."""
    if not well_formed(problem):
        return False
    kind = problem["kind"]
    w = parse_witness(candidate, kind)
    if not w:
        return False
    check, fields = CHECKS[kind]
    return bool(check(w, *(problem[field] for field in fields)))


if __name__ == "__main__":
    checks = {"duplicate indices rejected": not parse_witness("1,1", "clique"),
              "index whitespace tolerated": parse_witness(" 3, 1 ", "clique") == (3, 1),
              "malformed problem rejected": not really_solves("1", {})}
    for name, ok in checks.items():
        print(("PASS  " if ok else "FAIL  ") + name)
    raise SystemExit(not all(checks.values()))
