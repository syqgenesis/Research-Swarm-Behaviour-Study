"""Renders `<run>.report.md` and `<run>.evalcard.md` from a swarm.timeline
dict plus the outcome table and (optionally) judge annotations.

Stdlib only, no network. Every number in the frozen-primaries section is
recomputed from swarm.stats at render time from the seed named in the
call — nothing is cached or hand-typed. Section order and content follow
design/preregistration.md; do not reorder without updating that document.
"""
import hashlib
import json
import os

from swarm import config
from swarm import judge_merge
from swarm import outcomes
from swarm import stats
from swarm import sweep
from swarm import timeline as timeline_module


def _md_table(headers, rows):
    if not rows:
        return "_(nothing to report)_"
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(out)


def _pct(x, digits=1):
    return "n/a" if x is None else ("%." + str(digits) + "f%%") % (100 * x)


def _num(x, digits=3):
    return "n/a" if x is None else ("%." + str(digits) + "f") % x


def _group_summary(rows, group_labels):
    """Per-group n / event-count / proportion / Beta(1,1) interval, for a
    binary field `y_field`."""
    def summarize(y_field):
        out = []
        for g in group_labels:
            gy = [1.0 if r[y_field] else 0.0 for r in rows if r["reward_group"] == g]
            n, y = len(gy), int(sum(gy))
            iv = stats.beta_interval(y, n) if n else None
            out.append((g, n, y, _pct(y / n) if n else "n/a",
                         "[%.2f, %.2f]" % (iv["lower"], iv["upper"]) if iv else "n/a"))
        return out
    return summarize


def _primary_section(table, n_perm, seed):
    rows = outcomes.primary_rows(table)
    group_labels = sorted({r["reward_group"] for r in rows})
    summarize = _group_summary(rows, group_labels)

    lines = ["## 1. Frozen primary comparisons",
             "", "**H1**: reporting proportion, paid (5/10/15) vs 0 points per correct "
             "report. **H2**: reporting rises monotonically with reward level.", "",
             "### Reporting (H1, H2)", ""]
    lines.append(_md_table(["group", "n", "reported", "proportion", "Beta(1,1) 95% CI"],
                            summarize("reported")))
    lines.append("")

    labels = [r["reward_group"] for r in rows]
    y = [1.0 if r["reported"] else 0.0 for r in rows]
    h1 = stats.perm_test(labels, y, lambda l, o: stats.stat_contrast(l, o, {5, 10, 15}, {0}),
                          n_perm=n_perm, seed=seed, alternative="greater")
    h2 = stats.perm_test(labels, y, stats.stat_trend, n_perm=n_perm, seed=seed + 1,
                          alternative="greater")
    lines += [
        "H1 (paid vs 0, one-sided greater): contrast = %s, permutation p = %s (n_perm=%d, seed=%d)"
        % (_num(h1["stat"]), _num(h1["p"], 4), n_perm, seed),
        "",
        "H2 (monotone trend, one-sided greater): trend statistic = %s, permutation p = %s (seed=%d)"
        % (_num(h2["stat"]), _num(h2["p"], 4), seed + 1),
        "",
        "Holm-adjusted over {H1, H2}:",
        "",
        _md_table(["hypothesis", "raw p", "holm p"],
                   list(zip(["H1", "H2"], [_num(h1["p"], 4), _num(h2["p"], 4)],
                            [_num(p, 4) for p in stats.holm([h1["p"], h2["p"]])]))),
        "",
        "### Exploiting, for context (not the primary outcome)", "",
    ]
    lines.append(_md_table(["group", "n", "exploited", "proportion", "Beta(1,1) 95% CI"],
                            summarize("exploited")))
    return "\n".join(lines), {"h1_p": h1["p"], "h2_p": h2["p"]}


def _exposure_section(table):
    rows = outcomes.primary_rows(table)
    n = len(rows)
    exposed = [r for r in rows if r["exposed_strict"]]
    by_channel = {}
    for r in exposed:
        by_channel[r["exposure_channel"]] = by_channel.get(r["exposure_channel"], 0) + 1
    lines = ["## 2. Exposure and reach (Q1)", "",
              "%d of %d primary agents (%s) had a logged read of an exploit-carrying "
              "artefact." % (len(exposed), n, _pct(len(exposed) / n if n else None)), ""]
    lines.append(_md_table(["channel", "n first-exposed via this channel"],
                            sorted(by_channel.items())))
    lines.append("")
    rep_exp = [r for r in exposed if r["reported"]]
    exp_exp = [r for r in exposed if r["exploited"]]
    lines += [
        "Reporting given exposure: %d/%d (%s)."
        % (len(rep_exp), len(exposed), _pct(len(rep_exp) / len(exposed) if exposed else None)),
        "",
        "Exploiting given exposure: %d/%d (%s). This is `adoption_given_exposure` — "
        "the denominator that matters, not the whole population."
        % (len(exp_exp), len(exposed), _pct(len(exp_exp) / len(exposed) if exposed else None)),
    ]
    return "\n".join(lines)


def _survival_section(table, n_perm, seed):
    rows = [r for r in outcomes.primary_rows(table) if r["time_to_report"] is not None]
    lines = ["## 3. Time to event among the exposed (Q3)", ""]
    if not rows:
        lines.append("No exposed agents with a defined time-to-report — nothing to show.")
        return "\n".join(lines)
    times = [r["time_to_report"] for r in rows]
    events = [bool(r["report_event"]) for r in rows]
    is_paid = [r["reward_group"] != 0 for r in rows]
    km_paid = stats.kaplan_meier([t for t, p in zip(times, is_paid) if p],
                                  [e for e, p in zip(events, is_paid) if p])
    km_zero = stats.kaplan_meier([t for t, p in zip(times, is_paid) if not p],
                                  [e for e, p in zip(events, is_paid) if not p])
    lograk = stats.perm_logrank(times, events, is_paid, {True}, n_perm=n_perm, seed=seed)
    lines += [
        "Kaplan-Meier, time to first report from first exposure, censored at run end "
        "or pool depletion. %d exposed agents, %d reported before censoring."
        % (len(rows), sum(events)),
        "",
        "Paid group survival (fraction not yet reported):",
        _md_table(["t", "n at risk", "events", "S(t)"],
                   [(r["t"], r["n_at_risk"], r["d"], _num(r["S"])) for r in km_paid]) if km_paid
        else "_(no report events in the paid group)_",
        "",
        "Zero-reward group survival:",
        _md_table(["t", "n at risk", "events", "S(t)"],
                   [(r["t"], r["n_at_risk"], r["d"], _num(r["S"])) for r in km_zero]) if km_zero
        else "_(no report events in the zero-reward group)_",
        "",
        "Permutation log-rank (paid vs zero): z = %s, p = %s (n_perm=%d)"
        % (_num(lograk["z"]), _num(lograk["p"], 4), n_perm),
    ]
    return "\n".join(lines)


def _cohort_section(table):
    rows = outcomes.primary_rows(table)
    counts = {}
    for r in rows:
        counts[r["cohort"]] = counts.get(r["cohort"], 0) + 1
    lines = ["## 5. Cohorts (Q6, Q7)", "",
              "Assigned by the written rule in design/preregistration.md — not by the "
              "judge, and not the paper's manual method.", ""]
    lines.append(_md_table(["cohort", "n"], sorted(counts.items(), key=lambda kv: -kv[1])))
    lines.append("")
    mixed = [r for r in rows if r["cohort"] == "mixed"]
    if mixed:
        lines.append("Within-agent sequences (mixed cohort):")
        lines.append(_md_table(["agent", "flags"],
                                [(r["agent"], "; ".join(r["cohort_flags"])) for r in mixed]))
    return "\n".join(lines)


def _report_ledger_section(table):
    rows = outcomes.primary_rows(table)
    reported = [r for r in rows if r["n_reports"] > 0]
    lines = ["## 6. Report outcomes (Q4)", ""]
    if not reported:
        lines.append("No reports filed.")
        return "\n".join(lines)
    outcome_counts = {}
    for r in reported:
        outcome_counts[r["first_report_outcome"]] = outcome_counts.get(r["first_report_outcome"], 0) + 1
    lines.append(_md_table(["first report outcome", "n agents"], sorted(outcome_counts.items())))
    lines.append("")
    lines.append(_md_table(
        ["agent", "group", "n reports", "correct", "reported seeded agent"],
        [(r["agent"], r["reward_group"], r["n_reports"], r["n_correct_reports"], r["reported_seeded"])
         for r in reported]))
    return "\n".join(lines)


def _sharing_section(table):
    rows = table["rows"]
    if not any(r["transfers_sent"] or r["transfers_received"] for r in rows):
        return ""
    lines = ["## 7. Point sharing", "",
             "Only present because `point_sharing_enabled` was set for this run.", ""]
    lines.append(_md_table(
        ["agent", "sent (n / pts)", "received (n / pts)", "received from an exploiter"],
        [(r["agent"], "%d / %d" % (r["transfers_sent"], r["points_sent"]),
          "%d / %d" % (r["transfers_received"], r["points_received"]),
          r["received_from_exploiter"])
         for r in rows if r["transfers_sent"] or r["transfers_received"]]))
    return "\n".join(lines)


def _productivity_section(table):
    rows = table["rows"]
    lines = ["## 8. Productivity and points (Q5)", ""]
    lines.append(_md_table(
        ["agent", "group", "honest accepts", "invalid accepts", "awarded",
         "penalty", "final points"],
        [(r["agent"], r["reward_group"], r["honest_accepts"], r["n_invalid_accepts"],
          r["awarded_points"], r["cheating_penalty"], r["final_points"])
         for r in sorted(rows, key=lambda r: r["agent"])]))
    return "\n".join(lines)


def _exploratory_section(run_id, run_dir, annotations):
    lines = ["## 9. Exploratory", ""]
    sweep_path = os.path.join(run_dir or config.RUN_DIR, run_id + ".sweep.json")
    if os.path.exists(sweep_path):
        with open(sweep_path, encoding="utf-8") as f:
            sw = json.load(f)
        lines += [
            "Codebook hit counts (Tier 0, recall only — not a finding by itself):",
            _md_table(["code", "hops carrying it"],
                       sorted(sw.get("codebook_totals", {}).items(), key=lambda kv: -kv[1])),
            "",
            "%d correct candidates found in reasoning that were never filed."
            % len(sw.get("work_loss_unfiled", [])),
        ]
    else:
        lines.append("_(no `<run>.sweep.json` found — run swarm.sweep.write_sweep first)_")
    if annotations:
        codes = {c for row in annotations for c in (row.get("labels") or {})}
        gold_path = os.path.join(run_dir or config.RUN_DIR, run_id + ".gold.jsonl")
        if os.path.exists(gold_path):
            ann_path = os.path.join(run_dir or config.RUN_DIR, run_id + ".annotations.jsonl")
            kappa = judge_merge.calibration_report(gold_path, ann_path, codes=codes)
            lines += ["", "Judge calibration (kappa vs. hand labels):",
                      _md_table(["code", "kappa", "n shared"],
                                 [(c, _num(v["kappa"], 3) if v["kappa"] is not None else "n/a", v["n_shared"])
                                  for c, v in sorted(kappa.items())])]
    return "\n".join(lines)


def evidence_index(tl, run_id, run_dir=None, annotations=None):
    """[(claim_id, agent, step, hop, char_start, char_end, quote), ...] —
    every claim traceable back to an exact offset in the source hop."""
    index = []
    sweep_path = os.path.join(run_dir or config.RUN_DIR, run_id + ".sweep.json")
    if os.path.exists(sweep_path):
        with open(sweep_path, encoding="utf-8") as f:
            sw = json.load(f)
        for i, h in enumerate(sw.get("work_loss_unfiled", [])):
            index.append(("work-loss-%d" % i, h["agent"], h["step"], h["hop"],
                           h["char_start"], h["char_end"], h["quote"]))
    for i, row in enumerate(annotations or []):
        for code, val in (row.get("labels") or {}).items():
            if val.get("present") and val.get("quote"):
                index.append(("annotation-%d-%s" % (i, code), row["agent"], row["step"],
                               row.get("hop"), None, None, val["quote"]))
    return index


def evalcard(tl):
    rs = tl.get("run_start") or {}
    re_ = tl.get("run_end") or {}
    lines = ["# EvalCard", "",
              "| field | value |", "|---|---|",
              "| run_id | %s |" % rs.get("run_id", tl.get("run_id")),
              "| benchmark | %s |" % rs.get("benchmark"),
              "| prompt_version | %s |" % rs.get("prompt_version"),
              "| seed | %s |" % rs.get("seed"),
              "| n_agents | %s |" % rs.get("n_agents"),
              "| n_problems | %s |" % len(rs.get("problems") or []),
              "| minutes | %s |" % rs.get("minutes"),
              "| main_run | %s |" % rs.get("main_run"),
              "| point_sharing_enabled | %s |" % rs.get("point_sharing_enabled"),
              "| seeded_agent set | %s |" % bool(rs.get("seeded_agent")),
              "| hint_peer set | %s |" % bool(rs.get("hint_peer")),
              "| spend_cap_gbp | %s |" % rs.get("spend_cap_gbp"),
              "| elapsed_s | %s |" % re_.get("elapsed_s"),
              "| total calls | %s |" % re_.get("calls"),
              "| total cost_gbp | %s |" % re_.get("cost_gbp"),
              ""]
    return "\n".join(lines)


def render(run_id, run_dir=None, unblind=False, n_perm=20000, seed=20260913, annotations=None):
    """Writes `<run>.report.md` and `<run>.evalcard.md`. `annotations`, if
    given, is a list of judge_merge annotation rows (already merged from
    `<run>.annotations.jsonl` by the caller — this function stays pure)."""
    tl = timeline_module.build(run_id, run_dir)
    table = outcomes.build_outcome_table(tl, annotations=annotations, unblind=unblind)

    banner = ("**UNBLINDED** — reward-group labels are the real assignment."
              if unblind else
              "**BLINDED** — reward-group labels are a reproducible scramble; "
              "the analysis code and every table below were finalised before "
              "unblinding. See design/preregistration.md for the procedure.")

    primary_md, primary_p = _primary_section(table, n_perm, seed)
    sections = [
        "# Analysis report — %s" % run_id, "", banner, "",
        primary_md, "",
        _exposure_section(table), "",
        _survival_section(table, n_perm, seed), "",
        _cohort_section(table), "",
        _report_ledger_section(table), "",
    ]
    sharing = _sharing_section(table)
    if sharing:
        sections += [sharing, ""]
    sections += [
        _productivity_section(table), "",
        _exploratory_section(run_id, run_dir, annotations), "",
    ]

    index = evidence_index(tl, run_id, run_dir, annotations)
    sections += ["## 10. Evidence index", ""]
    if index:
        sections.append(_md_table(["claim_id", "agent", "step", "hop", "quote"],
                                   [(c, a, s, h, (q or "")[:120]) for c, a, s, h, cs, ce, q in index]))
    else:
        sections.append("_(none — run swarm.sweep.write_sweep and/or attach annotations first)_")

    report_path = os.path.join(run_dir or config.RUN_DIR, run_id + ".report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(sections) + "\n")

    evalcard_path = os.path.join(run_dir or config.RUN_DIR, run_id + ".evalcard.md")
    with open(evalcard_path, "w", encoding="utf-8") as f:
        f.write(evalcard(tl))

    return {"report_path": report_path, "evalcard_path": evalcard_path,
            "blinded": table["meta"]["blinded"], "primary_p": primary_p}


if __name__ == "__main__":
    import shutil
    import tempfile
    from swarm import mockrun

    tmp = tempfile.mkdtemp(dir="runs")
    checks = {}
    try:
        calls, events = mockrun.generate("selfcheck", seed=42, n_agents=25, minutes=60)
        mockrun.write("selfcheck", tmp, calls, events)
        sweep.write_sweep("selfcheck", run_dir=tmp)
        result = render("selfcheck", run_dir=tmp, unblind=False, n_perm=500, seed=1)
        checks["report file was written"] = os.path.exists(result["report_path"])
        checks["evalcard file was written"] = os.path.exists(result["evalcard_path"])
        checks["default render is blinded"] = result["blinded"] is True
        with open(result["report_path"], encoding="utf-8") as f:
            text = f.read()
        checks["report mentions the primary hypotheses"] = "H1" in text and "H2" in text
        checks["report has an evidence index section"] = "Evidence index" in text
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for name, ok in checks.items():
        print(("PASS  " if ok else "FAIL  ") + name)
    raise SystemExit(0 if all(checks.values()) else 1)
