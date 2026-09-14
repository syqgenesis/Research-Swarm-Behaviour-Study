"""Ingests whatever a judge produced for `swarm/sweep.py`'s batches and turns
it into `<run>.annotations.jsonl`. Stdlib only, no network, no subprocess —
the judge call itself happens outside this package (a Claude subagent, or a
DeepSeek pass through swarm.client run separately); this module only
validates and merges the JSON it comes back with.

Expected on-disk shape, one directory per run:
    runs/<run>.judge_batches/batch-0001.json          (written by sweep.py)
    runs/<run>.judge_batches/batch-0001.result.json    (written by the judge)

A `.result.json` must match design/judge-rubric.md's output schema exactly:
{"batch_id": ..., "results": [{"item_id": ..., "labels": {code: {"present":
bool, "confidence": 0-10, "quote": str}}}, ...]}. Anything else is a
validation error recorded against that batch, not a crash.
"""
import glob
import json
import os

from swarm import config
from swarm.sweep import _ALL_CODE_RES  # the one place the valid code set lives

VALID_CODES = set(_ALL_CODE_RES) | {
    # codes the LLM judge carries that the regex Tier 0 does not (see
    # design/judge-rubric.md) — listed explicitly so a typo'd code name
    # in a judge response is caught, not silently accepted.
    "lockout", "unfair", "exploit_intent", "exploit_reject",
    "whistle_intent", "peer_model", "eval_aware", "gloat", "verify", "backtrack",
}


def _batch_dir(run_id, run_dir=None):
    base = run_dir or config.RUN_DIR
    return os.path.join(base, run_id + ".judge_batches")


def _load_batch_inputs(run_id, run_dir=None):
    """{batch_id: {item_id: item}} from the input files sweep.py wrote."""
    out = {}
    for path in sorted(glob.glob(os.path.join(_batch_dir(run_id, run_dir), "batch-*.json"))):
        if path.endswith(".result.json"):
            continue
        with open(path, encoding="utf-8") as f:
            batch = json.load(f)
        out[batch["batch_id"]] = {item["item_id"]: item for item in batch["items"]}
    return out


def validate_result(batch_result, expected_items):
    """expected_items: {item_id: item} for this batch, from the input file.
    Returns (ok_results, errors). Never raises on malformed judge output —
    every failure path is a recorded error, same discipline as the grader."""
    errors = []
    ok_results = []
    if not isinstance(batch_result, dict) or "results" not in batch_result:
        return [], ["result is not a dict with a 'results' list"]

    seen_ids = set()
    for entry in batch_result.get("results", []):
        if not isinstance(entry, dict):
            errors.append("a results entry is not an object")
            continue
        item_id = entry.get("item_id")
        if item_id not in expected_items:
            errors.append("unknown or missing item_id: %r" % (item_id,))
            continue
        if item_id in seen_ids:
            errors.append("duplicate item_id: %s" % item_id)
            continue
        seen_ids.add(item_id)

        labels = entry.get("labels")
        if labels is None:
            labels = {}
        if not isinstance(labels, dict):
            errors.append("%s: labels is not an object" % item_id)
            continue

        clean_labels = {}
        reasoning_text = expected_items[item_id].get("reasoning_text", "")
        bad = False
        for code, val in labels.items():
            if code not in VALID_CODES:
                errors.append("%s: unknown code %r" % (item_id, code))
                bad = True
                continue
            if not isinstance(val, dict) or "present" not in val:
                errors.append("%s/%s: label is not a well-formed object" % (item_id, code))
                bad = True
                continue
            present = bool(val.get("present"))
            confidence = val.get("confidence")
            quote = val.get("quote")
            if present:
                if not isinstance(quote, str) or not quote.strip():
                    errors.append("%s/%s: present=true with no quote" % (item_id, code))
                    bad = True
                    continue
                if quote not in reasoning_text:
                    errors.append("%s/%s: quote is not a verbatim substring of the item's text"
                                  % (item_id, code))
                    bad = True
                    continue
                if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 10:
                    confidence = None  # tolerated: record the label, drop the score
                clean_labels[code] = {"present": True, "confidence": confidence, "quote": quote}
        if bad:
            continue
        ok_results.append({"item_id": item_id, "labels": clean_labels})

    missing = set(expected_items) - seen_ids
    for item_id in sorted(missing):
        errors.append("%s: no result returned for this item" % item_id)

    return ok_results, errors


def merge_batches(run_id, run_dir=None, judge_name="unspecified", prompt_version="v1"):
    """Scans every `*.result.json` next to `*.json` in the batch directory,
    validates each, and writes `<run>.annotations.jsonl` (append-safe: a
    re-run overwrites the file, it does not duplicate rows). Returns a
    summary dict; never raises for a malformed or partial batch."""
    inputs = _load_batch_inputs(run_id, run_dir)
    base = run_dir or config.RUN_DIR
    batch_dir = _batch_dir(run_id, run_dir)

    rows = []
    batch_errors = {}
    merged_batches = []
    missing_batches = []

    for batch_id, expected_items in inputs.items():
        result_path = os.path.join(batch_dir, batch_id + ".result.json")
        if not os.path.exists(result_path):
            missing_batches.append(batch_id)
            continue
        try:
            with open(result_path, encoding="utf-8") as f:
                raw = json.load(f)
        except ValueError as e:
            batch_errors[batch_id] = ["invalid JSON: %s" % e]
            continue

        ok_results, errors = validate_result(raw, expected_items)
        if errors:
            batch_errors[batch_id] = errors
        merged_batches.append(batch_id)
        for result in ok_results:
            item = expected_items[result["item_id"]]
            rows.append({
                "item_id": result["item_id"], "run_id": run_id,
                "agent": item["agent"], "step": item["step"], "hop": item["hop"],
                "ts": item["ts"], "judge_name": judge_name, "prompt_version": prompt_version,
                "labels": result["labels"],
            })

    annotations_path = os.path.join(base, run_id + ".annotations.jsonl")
    with open(annotations_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

    return {
        "run_id": run_id, "judge_name": judge_name,
        "total_batches": len(inputs), "merged_batches": len(merged_batches),
        "missing_batches": missing_batches, "batches_with_errors": batch_errors,
        "annotated_items": len(rows), "annotations_path": annotations_path,
    }


def judge_status(run_id, run_dir=None):
    """Which batches still need a .result.json — the thing to check mid-run
    to know what to feed the judge next."""
    batch_dir = _batch_dir(run_id, run_dir)
    inputs = sorted(glob.glob(os.path.join(batch_dir, "batch-*.json")))
    inputs = [p for p in inputs if not p.endswith(".result.json")]
    pending, done = [], []
    for path in inputs:
        batch_id = os.path.splitext(os.path.basename(path))[0]
        result_path = os.path.join(batch_dir, batch_id + ".result.json")
        (done if os.path.exists(result_path) else pending).append(batch_id)
    return {"pending": pending, "done": done}


# ------------------------------------------------------------- calibration
def cohens_kappa(gold, judged, code):
    """gold, judged: {item_id: {code: bool}}. Cohen's kappa for one code over
    the intersection of item ids both label. Stdlib only — no numpy/sklearn.
    Returns None if fewer than 2 shared items or no variation to measure."""
    shared = sorted(set(gold) & set(judged))
    if len(shared) < 2:
        return None
    pairs = [(bool(gold[i].get(code)), bool(judged[i].get(code))) for i in shared]
    n = len(pairs)
    agree = sum(1 for a, b in pairs if a == b)
    p_o = agree / n
    p_gold_true = sum(1 for a, _ in pairs if a) / n
    p_judged_true = sum(1 for _, b in pairs if b) / n
    p_e = p_gold_true * p_judged_true + (1 - p_gold_true) * (1 - p_judged_true)
    if p_e == 1.0:
        return 1.0 if p_o == 1.0 else 0.0
    return (p_o - p_e) / (1 - p_e)


def calibration_report(gold_path, annotations_path, codes=None):
    """gold_path: JSONL of {"item_id":..., "labels": {code: bool, ...}} —
    the hand-labelled calibration set. annotations_path: an annotations.jsonl
    from merge_batches. Returns {code: {kappa, n_shared, gold_rate, judge_rate}}."""
    gold = {}
    if os.path.exists(gold_path):
        with open(gold_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                gold[rec["item_id"]] = {k: bool(v) for k, v in (rec.get("labels") or {}).items()}

    judged = {}
    if os.path.exists(annotations_path):
        with open(annotations_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                judged[rec["item_id"]] = {k: bool(v.get("present"))
                                           for k, v in (rec.get("labels") or {}).items()}

    codes = codes or VALID_CODES
    report = {}
    for code in sorted(codes):
        shared = sorted(set(gold) & set(judged))
        kappa = cohens_kappa(gold, judged, code)
        report[code] = {
            "kappa": kappa,
            "n_shared": len(shared),
            "gold_rate": (sum(1 for i in shared if gold[i].get(code)) / len(shared)) if shared else None,
            "judge_rate": (sum(1 for i in shared if judged[i].get(code)) / len(shared)) if shared else None,
        }
    return report


if __name__ == "__main__":
    checks = {}

    expected = {"x.a1.s1.h0": {"reasoning_text": "I think this is a bluff, nobody checks it."}}
    good = {"batch_id": "b1", "results": [
        {"item_id": "x.a1.s1.h0",
         "labels": {"bluff": {"present": True, "confidence": 8, "quote": "this is a bluff"}}}]}
    ok, errs = validate_result(good, expected)
    checks["a well-formed result validates cleanly"] = (len(ok) == 1 and not errs)

    bad_quote = {"batch_id": "b1", "results": [
        {"item_id": "x.a1.s1.h0",
         "labels": {"bluff": {"present": True, "confidence": 8, "quote": "not in the text"}}}]}
    ok2, errs2 = validate_result(bad_quote, expected)
    checks["a non-verbatim quote is rejected, not silently kept"] = (len(ok2) == 0 and bool(errs2))

    unknown_code = {"batch_id": "b1", "results": [
        {"item_id": "x.a1.s1.h0",
         "labels": {"not_a_real_code": {"present": True, "confidence": 5, "quote": "bluff"}}}]}
    ok3, errs3 = validate_result(unknown_code, expected)
    checks["an unknown code name is rejected"] = (len(ok3) == 0 and bool(errs3))

    missing_item = {"batch_id": "b1", "results": []}
    ok4, errs4 = validate_result(missing_item, expected)
    checks["a missing item is reported, not silently dropped"] = bool(errs4)

    gold = {"i1": {"bluff": True}, "i2": {"bluff": False}, "i3": {"bluff": True}, "i4": {"bluff": False}}
    perfect = {"i1": {"bluff": True}, "i2": {"bluff": False}, "i3": {"bluff": True}, "i4": {"bluff": False}}
    checks["perfect agreement gives kappa 1.0"] = cohens_kappa(gold, perfect, "bluff") == 1.0
    inverted = {"i1": {"bluff": False}, "i2": {"bluff": True}, "i3": {"bluff": False}, "i4": {"bluff": True}}
    k = cohens_kappa(gold, inverted, "bluff")
    checks["total disagreement gives a negative kappa"] = (k is not None and k < 0)

    for name, ok in checks.items():
        print(("PASS  " if ok else "FAIL  ") + name)
    raise SystemExit(0 if all(checks.values()) else 1)
