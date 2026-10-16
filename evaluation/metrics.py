"""
evaluation/metrics.py

Aggregates ONLY real, stored, human-scored results into dashboard metrics.
Never fabricates or interpolates data — categories/criteria with zero scored
results simply don't appear (or appear with explicit zero/None), rather than
being filled in with placeholder numbers.
"""

from evaluation.evaluator import load_results, load_config


def _scored_results():
    return [r for r in load_results() if r.get("scored")]


def overall_metrics() -> dict:
    """
    Overall System Evaluation Score = (total points obtained / total
    applicable points) x 100, across every SCORED result — points are
    summed first, THEN divided, never averaged as percentages. This is
    the cumulative, restart-persistent score: it is recomputed fresh from
    evaluation/results.json on every call, so it is always reconstructable
    from the stored records alone (no in-memory counters, no session state).
    """
    config = load_config()
    thresholds = config["classification_thresholds"]
    all_results = load_results()
    scored = [r for r in all_results if r.get("scored")]

    total_obtained = sum(r["total_score"] for r in scored if r["applicable_points"] > 0)
    total_applicable = sum(r["applicable_points"] for r in scored if r["applicable_points"] > 0)
    overall_percentage = (100.0 * total_obtained / total_applicable) if total_applicable > 0 else None

    fully_successful = sum(1 for r in scored if r["classification"] in ("Excellent", "Good"))
    partially_successful = sum(1 for r in scored if r["classification"] == "Partial")
    failed = sum(1 for r in scored if r["classification"] == "Failed")
    not_applicable = sum(1 for r in scored if r["classification"] == "Not Applicable")

    return {
        "total_evaluations_run": len(all_results),
        "total_scored_evaluations": len(scored),
        "overall_system_evaluation_score": round(overall_percentage, 1) if overall_percentage is not None else None,
        "fully_successful": fully_successful,
        "partially_successful": partially_successful,
        "failed": failed,
        "not_applicable": not_applicable,
        "classification_thresholds": thresholds,
    }


def category_scores() -> dict:
    """Category Score = (points obtained in category / applicable points in category) x 100."""
    scored = _scored_results()
    by_category = {}

    for r in scored:
        if r["applicable_points"] <= 0:
            continue
        cat = r["category"]
        bucket = by_category.setdefault(cat, {"obtained": 0.0, "applicable": 0.0, "count": 0})
        bucket["obtained"] += r["total_score"]
        bucket["applicable"] += r["applicable_points"]
        bucket["count"] += 1

    out = {}
    for cat, bucket in by_category.items():
        pct = (100.0 * bucket["obtained"] / bucket["applicable"]) if bucket["applicable"] > 0 else None
        out[cat] = {
            "score_percent": round(pct, 1) if pct is not None else None,
            "tests_evaluated": bucket["count"],
        }
    return out


def criterion_scores() -> dict:
    """
    Per-criterion average score, across every scored result where that
    criterion was applicable (not N/A). Powers the optional criterion-wise
    chart.
    """
    config = load_config()
    scored = _scored_results()
    by_criterion = {key: {"obtained": 0.0, "applicable": 0.0} for key in config["criteria"]}

    for r in scored:
        statuses = r.get("criterion_statuses") or {}
        multipliers = config["status_multipliers"]
        for key, meta in config["criteria"].items():
            status = statuses.get(key, "n/a")
            if status == "n/a":
                continue
            max_points = meta["max_points"]
            by_criterion[key]["applicable"] += max_points
            by_criterion[key]["obtained"] += max_points * multipliers[status]

    out = {}
    for key, meta in config["criteria"].items():
        bucket = by_criterion[key]
        pct = (100.0 * bucket["obtained"] / bucket["applicable"]) if bucket["applicable"] > 0 else None
        out[key] = {
            "label": meta["label"],
            "score_percent": round(pct, 1) if pct is not None else None,
        }
    return out


def safety_handling_score() -> float:
    """Convenience accessor — same computation as criterion_scores()['safety_handling']."""
    return criterion_scores().get("safety_handling", {}).get("score_percent")


def context_correctness_score() -> float:
    """
    Combined 'context correctness' = context_collection + context_interpretation
    criteria together, since both concern whether the assistant got the real
    system data right and understood it correctly.
    """
    config = load_config()
    scored = _scored_results()
    multipliers = config["status_multipliers"]
    obtained = 0.0
    applicable = 0.0

    for r in scored:
        statuses = r.get("criterion_statuses") or {}
        for key in ("context_collection", "context_interpretation"):
            status = statuses.get(key, "n/a")
            if status == "n/a":
                continue
            max_points = config["criteria"][key]["max_points"]
            applicable += max_points
            obtained += max_points * multipliers[status]

    return round(100.0 * obtained / applicable, 1) if applicable > 0 else None


def full_dashboard_payload() -> dict:
    """Single call that returns everything the dashboard needs, all from real stored data."""
    return {
        "overall": overall_metrics(),
        "by_category": category_scores(),
        "by_criterion": criterion_scores(),
        "safety_handling_score": safety_handling_score(),
        "context_correctness_score": context_correctness_score(),
    }
