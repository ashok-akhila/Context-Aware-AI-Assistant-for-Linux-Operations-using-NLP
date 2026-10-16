"""
evaluation/evaluator.py

Core logic for the evaluation module. This module is purely additive: it
imports and calls into the existing pipeline (ai.pipeline.run_assistant_query)
rather than reimplementing any of it, and it never runs on the request path
of the live assistant — it only runs when someone explicitly creates/runs/
scores a test case through the evaluation routes.

Storage is plain JSON, per the project's "keep it simple, no unnecessary
databases" requirement. Reads/writes are guarded by a lock and written
atomically (write-to-temp then rename) so a crash mid-write can't corrupt
the store.

Scoring model:
  - Each criterion has a max point value (see config.json).
  - Each *applicable* criterion is marked Pass / Partial / Fail — normally
    by the automatic evaluation engine (evaluation/auto_evaluator.py),
    which independently judges each criterion from real pipeline evidence;
    a human can still override any result explicitly via score_result().
  - Criteria marked N/A are excluded entirely from both the earned points
    and the applicable-points denominator, so they neither help nor hurt
    the score (per the "handle N/A fairly" requirement).
  - total_score = sum(max_points * status_multiplier) over applicable criteria
  - applicable_points = sum(max_points) over applicable criteria
  - percentage = 100 * total_score / applicable_points (None if nothing applicable)
  - classification = Excellent / Good / Partial / Failed, using configurable
    thresholds from config.json.

New test cases are prevented from duplicating an existing one (same
category + case/whitespace-normalized query) — see create_test_case() and
normalize_query(). Duplicate PREVENTION only applies to test-case
DEFINITIONS; the same test case can still be run any number of times, and
every run is stored as its own evaluation result (see run_test_case()).
"""

import json
import os
import re
import threading
import time
import uuid

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_BASE_DIR, "config.json")
_TEST_CASES_PATH = os.path.join(_BASE_DIR, "test_cases.json")
_RESULTS_PATH = os.path.join(_BASE_DIR, "results.json")

_lock = threading.Lock()

VALID_STATUSES = {"pass", "partial", "fail", "n/a"}


class EvaluationError(Exception):
    pass


# ---------------------------------------------------------------------------
# Low-level JSON storage helpers
# ---------------------------------------------------------------------------

def _read_json(path):
    if not os.path.exists(path):
        return [] if path != _CONFIG_PATH else {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        # Never silently treat a corrupt-but-existing file as empty — that
        # would look like "evaluation history reset itself" to the user.
        # Fail loudly instead so the real file can be inspected/repaired.
        raise EvaluationError(
            f"Stored evaluation data at '{path}' is corrupted and could not be parsed "
            f"({e}). It was NOT reset or overwritten — inspect/restore the file manually."
        )


def _write_json_atomic(path, data):
    tmp_path = f"{path}.tmp-{uuid.uuid4().hex}"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


def load_config():
    with _lock:
        return _read_json(_CONFIG_PATH)


def load_test_cases():
    with _lock:
        return _read_json(_TEST_CASES_PATH)


def load_results():
    with _lock:
        return _read_json(_RESULTS_PATH)


# ---------------------------------------------------------------------------
# Test case management
# ---------------------------------------------------------------------------

def normalize_query(user_query: str) -> str:
    """Trim, collapse repeated whitespace, and lowercase — used only for
    duplicate-comparison purposes. The original, human-typed user_query is
    always what gets stored and displayed/run."""
    return re.sub(r"\s+", " ", (user_query or "").strip()).lower()


def create_test_case(category: str, user_query: str) -> dict:
    """
    Creates a new test case, UNLESS an existing test case already has the
    same category and a case-insensitive, whitespace-normalized version of
    the same query — in which case the existing test case is returned
    instead (with "duplicate": True) and nothing new is written. This
    keeps test-case DEFINITIONS unique while leaving evaluation RUNS (see
    run_test_case / run_query below) completely free to repeat as many
    times as legitimately needed for the same test case.
    """
    category = (category or "").strip()
    config = load_config()
    if category not in config.get("categories", []):
        raise EvaluationError(
            f"Unknown category '{category}'. Valid categories: {config.get('categories', [])}"
        )
    if not user_query or not user_query.strip():
        raise EvaluationError("user_query must not be empty.")

    normalized = normalize_query(user_query)

    with _lock:
        test_cases = _read_json(_TEST_CASES_PATH)

        for tc in test_cases:
            existing_normalized = tc.get("normalized_query") or normalize_query(tc.get("user_query", ""))
            if tc.get("category") == category and existing_normalized == normalized:
                return dict(tc, duplicate=True)

        test_case = {
            "test_id": f"t_{uuid.uuid4().hex[:10]}",
            "category": category,
            "user_query": user_query.strip(),
            "normalized_query": normalized,
            "created_at": time.time(),
        }
        test_cases.append(test_case)
        _write_json_atomic(_TEST_CASES_PATH, test_cases)
    return dict(test_case, duplicate=False)


def get_test_case(test_id: str) -> dict:
    for tc in load_test_cases():
        if tc["test_id"] == test_id:
            return tc
    raise EvaluationError(f"No test case with id '{test_id}'.")


def delete_test_case(test_id: str) -> None:
    with _lock:
        test_cases = _read_json(_TEST_CASES_PATH)
        remaining = [tc for tc in test_cases if tc["test_id"] != test_id]
        if len(remaining) == len(test_cases):
            raise EvaluationError(f"No test case with id '{test_id}'.")
        _write_json_atomic(_TEST_CASES_PATH, remaining)


# ---------------------------------------------------------------------------
# Running a test case through the REAL, existing pipeline
# ---------------------------------------------------------------------------

def _base_result_shape(result_id: str, test_id, category: str, user_query: str,
                        pipeline_error, llm_result, backend_validation, context_collected) -> dict:
    return {
        "result_id": result_id,
        "test_id": test_id,
        "category": category,
        "user_query": user_query,
        "pipeline_error": pipeline_error,
        "llm_result": llm_result,
        "backend_validation": backend_validation,
        "context_collected": context_collected,
        "execution_result": None,  # this evaluation path never executes commands
        "user_confirmed": False,
        "run_at": time.time(),
        "scored": False,
        "evaluation_mode": None,
        "criteria": None,
        "evaluation_error": None,
        "criterion_statuses": None,
        "evaluator_notes": "",
        "total_score": None,
        "applicable_points": None,
        "percentage": None,
        "classification": None,
        "scored_at": None,
    }


def _apply_automatic_evaluation(result: dict, config: dict) -> dict:
    """
    Runs the result through the separate automatic evaluation engine
    (evaluation/auto_evaluator.py) and folds the outcome into the result
    dict IN PLACE, then returns it. This is the ONLY place that decides
    scored/criterion_statuses/total_score for a freshly-run result, so
    run_test_case() and run_query() and reevaluate_result() all share the
    exact same behavior.

    If the evaluator itself could not complete (see AutoEvaluationError in
    auto_evaluator.py), the result is left unscored with evaluation_error
    set, so it surfaces in the pending/review queue for a retry or an
    explicit manual override via score_result() — it is never silently
    scored as if nothing went wrong.
    """
    from evaluation.auto_evaluator import evaluate as auto_evaluate

    evidence = {
        "user_query": result["user_query"],
        "category": result["category"],
        "context_collected": result["context_collected"],
        "llm_result": result["llm_result"],
        "backend_validation": result["backend_validation"],
        "pipeline_error": result["pipeline_error"],
        "execution_result": result.get("execution_result"),
        "user_confirmed": result.get("user_confirmed", False),
    }

    outcome = auto_evaluate(evidence, config)
    criteria = outcome["criteria"]
    evaluation_error = outcome["evaluation_error"]

    result["criteria"] = criteria
    result["evaluation_mode"] = "automatic"
    result["evaluation_error"] = evaluation_error

    if evaluation_error:
        # Incomplete — leave unscored rather than guessing at a score.
        result["scored"] = False
        result["criterion_statuses"] = {k: v["status"] for k, v in criteria.items()}
        result["evaluator_notes"] = f"Automatic evaluation incomplete: {evaluation_error}"
        return result

    criterion_statuses = {k: v["status"] for k, v in criteria.items()}
    scoring = compute_score(criterion_statuses, config)

    result["scored"] = True
    result["criterion_statuses"] = criterion_statuses
    result["evaluator_notes"] = "Automatically evaluated from real pipeline evidence — see per-criterion evidence."
    result["total_score"] = scoring["total_score"]
    result["applicable_points"] = scoring["applicable_points"]
    result["percentage"] = scoring["percentage"]
    result["classification"] = scoring["classification"]
    result["scored_at"] = time.time()

    return result


def run_test_case(test_id: str) -> dict:
    """
    Runs the test case's query through the exact same pipeline the live app
    uses (ai.pipeline.run_assistant_query) — real context collection, real
    LLM output (or the real error if a step failed) — and then immediately
    runs the result through the separate automatic evaluation engine so the
    six criteria are scored without any manual Pass/Partial/Fail selection.
    The evaluator never sees "trust me, this worked"; it only sees the
    actual evidence produced by this real run.
    """
    # Imported here (not at module top) to avoid any import-time coupling
    # between the evaluation module and the live app before it's needed.
    from ai.pipeline import run_assistant_query, PipelineError
    from ai.llm_service import LLMResponseError

    test_case = get_test_case(test_id)
    config = load_config()

    pipeline_output = None
    pipeline_error = None
    context_collected = None
    try:
        pipeline_output = run_assistant_query(test_case["user_query"])
        context_collected = pipeline_output.get("context_collected")
    except PipelineError as e:
        pipeline_error = f"Context collection failed: {e}"
    except LLMResponseError as e:
        pipeline_error = f"LLM call failed: {e}"
        # Context collection succeeded even though the LLM step failed —
        # ai.pipeline attaches it to the exception so we don't lose it.
        context_collected = getattr(e, "context_collected", None)

    result = _base_result_shape(
        result_id=f"r_{uuid.uuid4().hex[:10]}",
        test_id=test_case["test_id"],
        category=test_case["category"],
        user_query=test_case["user_query"],
        pipeline_error=pipeline_error,
        llm_result=(pipeline_output or {}).get("llm_result"),
        backend_validation=(pipeline_output or {}).get("backend_validation"),
        context_collected=context_collected,
    )
    result = _apply_automatic_evaluation(result, config)

    with _lock:
        results = _read_json(_RESULTS_PATH)
        results.append(result)
        _write_json_atomic(_RESULTS_PATH, results)

    return result


def run_query(category: str, user_query: str) -> dict:
    """
    Evaluates an ARBITRARY natural-language query directly, without requiring
    a pre-created test case first. Runs through the exact same
    ai.pipeline.run_assistant_query() used by run_test_case() and by the
    live /api/query route — there is only ever one assistant pipeline —
    then automatically evaluates the outcome the same way run_test_case()
    does.

    The resulting record has the same shape as a test-case-run result,
    except test_id is None (nothing to reference in test_cases.json).
    """
    from ai.pipeline import run_assistant_query, PipelineError
    from ai.llm_service import LLMResponseError

    category = (category or "").strip()
    config = load_config()
    if category not in config.get("categories", []):
        raise EvaluationError(
            f"Unknown category '{category}'. Valid categories: {config.get('categories', [])}"
        )
    if not user_query or not user_query.strip():
        raise EvaluationError("user_query must not be empty.")
    user_query = user_query.strip()

    pipeline_output = None
    pipeline_error = None
    context_collected = None
    try:
        pipeline_output = run_assistant_query(user_query)
        context_collected = pipeline_output.get("context_collected")
    except PipelineError as e:
        pipeline_error = f"Context collection failed: {e}"
    except LLMResponseError as e:
        pipeline_error = f"LLM call failed: {e}"
        context_collected = getattr(e, "context_collected", None)

    result = _base_result_shape(
        result_id=f"r_{uuid.uuid4().hex[:10]}",
        test_id=None,  # ad-hoc evaluation, not tied to a stored test case
        category=category,
        user_query=user_query,
        pipeline_error=pipeline_error,
        llm_result=(pipeline_output or {}).get("llm_result"),
        backend_validation=(pipeline_output or {}).get("backend_validation"),
        context_collected=context_collected,
    )
    result = _apply_automatic_evaluation(result, config)

    with _lock:
        results = _read_json(_RESULTS_PATH)
        results.append(result)
        _write_json_atomic(_RESULTS_PATH, results)

    return result


def reevaluate_result(result_id: str) -> dict:
    """
    Re-runs ONLY the automatic evaluation step against the evidence already
    stored for an existing result (does not re-run the assistant/pipeline,
    so it does not consume a new real system snapshot). This exists for
    the case where the automatic evaluator itself failed to complete
    (e.g. transient LLM API error) — see evaluation_error on the result —
    so the person isn't forced into a manual override just to recover from
    a transient evaluator failure.
    """
    config = load_config()
    with _lock:
        results = _read_json(_RESULTS_PATH)
        target = None
        for r in results:
            if r["result_id"] == result_id:
                target = r
                break
        if target is None:
            raise EvaluationError(f"No result with id '{result_id}'.")

        target = _apply_automatic_evaluation(target, config)
        _write_json_atomic(_RESULTS_PATH, results)

    return target


# ---------------------------------------------------------------------------
# Scoring — always a human decision, never automatic
# ---------------------------------------------------------------------------

def classify(percentage, thresholds: dict) -> str:
    if percentage is None:
        return "Not Applicable"
    if percentage >= thresholds["excellent_min_percent"]:
        return "Excellent"
    if percentage >= thresholds["good_min_percent"]:
        return "Good"
    if percentage >= thresholds["partial_min_percent"]:
        return "Partial"
    return "Failed"


def compute_score(criterion_statuses: dict, config: dict) -> dict:
    """
    Pure scoring function — no I/O — so it's independently testable and
    reusable. criterion_statuses: {criterion_key: "pass"|"partial"|"fail"|"n/a"}
    Criteria omitted entirely are treated as N/A.
    """
    criteria_def = config["criteria"]
    multipliers = config["status_multipliers"]

    total_score = 0.0
    applicable_points = 0.0

    for key, meta in criteria_def.items():
        status = (criterion_statuses.get(key) or "n/a").lower()
        if status not in VALID_STATUSES:
            raise EvaluationError(f"Invalid status '{status}' for criterion '{key}'.")
        if status == "n/a":
            continue
        max_points = meta["max_points"]
        applicable_points += max_points
        total_score += max_points * multipliers[status]

    percentage = (100.0 * total_score / applicable_points) if applicable_points > 0 else None
    classification = classify(percentage, config["classification_thresholds"])

    return {
        "total_score": round(total_score, 2),
        "applicable_points": applicable_points,
        "percentage": round(percentage, 1) if percentage is not None else None,
        "classification": classification,
    }


def score_result(result_id: str, criterion_statuses: dict, evaluator_notes: str = "") -> dict:
    """
    Explicit human scoring/override. This remains available for the
    existing Review Queue's manual path (and for old, pre-existing
    manually-scored history — untouched) and now also doubles as the
    explicit override mechanism for an automatically-scored result: a
    human can still open a result and change the verdict, which is
    recorded as evaluation_mode="manual" (or "manual_override" if it had
    previously been scored automatically) so the dashboard/history can
    tell the two apart. It never runs on the normal "run a query" path
    any more — that path is now automatic by default (see run_query /
    run_test_case in this module).
    """
    config = load_config()
    scoring = compute_score(criterion_statuses, config)

    with _lock:
        results = _read_json(_RESULTS_PATH)
        target = None
        for r in results:
            if r["result_id"] == result_id:
                target = r
                break
        if target is None:
            raise EvaluationError(f"No result with id '{result_id}'.")

        was_automatic = target.get("evaluation_mode") == "automatic"

        target["scored"] = True
        target["evaluation_mode"] = "manual_override" if was_automatic else "manual"
        target["evaluation_error"] = None
        target["criterion_statuses"] = {k: (v or "n/a").lower() for k, v in criterion_statuses.items()}
        target["evaluator_notes"] = evaluator_notes or ""
        target["total_score"] = scoring["total_score"]
        target["applicable_points"] = scoring["applicable_points"]
        target["percentage"] = scoring["percentage"]
        target["classification"] = scoring["classification"]
        target["scored_at"] = time.time()

        _write_json_atomic(_RESULTS_PATH, results)

    return target


def get_result(result_id: str) -> dict:
    for r in load_results():
        if r["result_id"] == result_id:
            return r
    raise EvaluationError(f"No result with id '{result_id}'.")


def delete_result(result_id: str) -> None:
    with _lock:
        results = _read_json(_RESULTS_PATH)
        remaining = [r for r in results if r["result_id"] != result_id]
        if len(remaining) == len(results):
            raise EvaluationError(f"No result with id '{result_id}'.")
        _write_json_atomic(_RESULTS_PATH, remaining)
