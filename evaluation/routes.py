"""
evaluation/routes.py

All evaluation functionality is exposed as a Flask Blueprint, registered
additively in app.py (`app.register_blueprint(evaluation_bp)`). None of
these routes are on the live assistant's request path — the existing
/api/query, /api/execute, /api/context, /api/process/<pid> routes and the
main chat UI are completely untouched by this module's presence.
"""

import os
from flask import Blueprint, request, jsonify, render_template, current_app

from evaluation.evaluator import (
    EvaluationError,
    create_test_case,
    load_test_cases,
    delete_test_case,
    run_test_case,
    run_query,
    reevaluate_result,
    score_result,
    load_results,
    get_result,
    delete_result,
    load_config,
)
from evaluation.metrics import full_dashboard_payload

evaluation_bp = Blueprint(
    "evaluation",
    __name__,
    url_prefix="",  # explicit full paths below keep routes easy to scan
)


@evaluation_bp.errorhandler(EvaluationError)
def handle_evaluation_error(err):
    """
    Catches any EvaluationError not already handled by a route's own
    try/except (notably: corrupted results.json / test_cases.json) and
    returns clean JSON instead of a bare Flask 500 HTML page. The data
    itself is never touched here — this only changes how the error is
    reported.
    """
    return jsonify({"error": str(err)}), 500


@evaluation_bp.after_request
def no_cache_for_evaluation_api(response):
    """
    Evaluation data changes after every run. Tell the browser never to reuse
    a stored copy of these GET responses, otherwise the Review Queue can keep
    showing an old list even though results.json has new entries.
    """
    if request.path.startswith("/api/evaluation/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# ---------------------------------------------------------------------------
# Dashboard page
# ---------------------------------------------------------------------------

@evaluation_bp.route("/evaluation")
def evaluation_dashboard():
    return render_template("evaluation.html")


# ---------------------------------------------------------------------------
# Config (categories, criteria, thresholds) — read-only from the UI's side
# ---------------------------------------------------------------------------

@evaluation_bp.route("/api/evaluation/config", methods=["GET"])
def api_get_config():
    return jsonify(load_config())


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

@evaluation_bp.route("/api/evaluation/test-cases", methods=["GET"])
def api_list_test_cases():
    return jsonify(load_test_cases())


@evaluation_bp.route("/api/evaluation/test-cases", methods=["POST"])
def api_create_test_case():
    """
    Creates a new test case UNLESS one with the same category and a
    case/whitespace-normalized version of the same query already exists —
    in that case the existing test case is returned (HTTP 200, with
    "duplicate": true and a clear "message") instead of creating a second
    definition. A genuinely new test case is created and returned as
    HTTP 201.
    """
    body = request.get_json(silent=True) or {}
    category = (body.get("category") or "").strip()
    user_query = (body.get("user_query") or "").strip()
    try:
        test_case = create_test_case(category, user_query)
    except EvaluationError as e:
        return jsonify({"error": str(e)}), 400

    if test_case.get("duplicate"):
        test_case["message"] = "Test case already exists."
        return jsonify(test_case), 200
    return jsonify(test_case), 201


@evaluation_bp.route("/api/evaluation/test-cases/<test_id>", methods=["DELETE"])
def api_delete_test_case(test_id):
    try:
        delete_test_case(test_id)
    except EvaluationError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify({"deleted": test_id})


# ---------------------------------------------------------------------------
# Running a test case through the real pipeline
# ---------------------------------------------------------------------------

def _missing_key_response():
    """
    Without an API key every run would just be recorded as a 'Failed'
    assistant result, which pollutes the scores. Refuse instead, and say why.
    """
    if os.environ.get("OPENROUTER_API_KEY"):
        return None
    return jsonify({"error": (
        "OPENROUTER_API_KEY is not set, so nothing was run or saved. Copy .env.example to "
        ".env in the project folder, paste your key, and restart app.py."
    )}), 400


@evaluation_bp.route("/api/evaluation/run/<test_id>", methods=["POST"])
def api_run_test_case(test_id):
    """
    Runs the test case's query through the SAME pipeline the live assistant
    uses, capturing the real context collected and real LLM output (or real
    error) — then immediately, automatically evaluates all six criteria
    from that evidence (evaluation/auto_evaluator.py) and persists the
    scored result. No manual Pass/Partial/Fail selection is required for
    this to count. A human can still open the result afterward and submit
    an explicit override via /score below.
    """
    blocked = _missing_key_response()
    if blocked:
        return blocked
    try:
        result = run_test_case(test_id)
    except EvaluationError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        # Anything unexpected: log the full traceback in the terminal running
        # app.py, and give the browser a readable JSON error instead of an
        # HTML 500 page (which the UI can't parse).
        current_app.logger.exception("Unexpected error running test case %s", test_id)
        return jsonify({"error": f"Unexpected error while running this test case: {type(e).__name__}: {e}"}), 500
    return jsonify(result), 201


@evaluation_bp.route("/api/evaluation/run-query", methods=["POST"])
def api_run_query():
    """
    Evaluates an ARBITRARY natural-language query directly — no pre-created
    test case required. Body: {"category": "...", "user_query": "..."}.
    Runs through the exact same assistant pipeline as everything else in
    this module, then automatically evaluates and persists the scored
    result, same as api_run_test_case above.
    """
    body = request.get_json(silent=True) or {}
    category = (body.get("category") or "").strip()
    user_query = (body.get("user_query") or "").strip()

    blocked = _missing_key_response()
    if blocked:
        return blocked
    try:
        result = run_query(category, user_query)
    except EvaluationError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        current_app.logger.exception("Unexpected error running ad-hoc query")
        return jsonify({"error": f"Unexpected error while running this query: {type(e).__name__}: {e}"}), 500
    return jsonify(result), 201


# ---------------------------------------------------------------------------
# Results — listing, viewing, scoring
# ---------------------------------------------------------------------------

@evaluation_bp.route("/api/evaluation/results", methods=["GET"])
def api_list_results():
    """Optional ?scored=true|false filter, e.g. for a 'pending review' queue."""
    scored_param = request.args.get("scored")
    results = load_results()
    if scored_param is not None:
        want_scored = scored_param.lower() == "true"
        results = [r for r in results if r.get("scored") == want_scored]
    # Most recent first — easiest to review what you just ran.
    results = sorted(results, key=lambda r: r["run_at"], reverse=True)
    return jsonify(results)


@evaluation_bp.route("/api/evaluation/results-status", methods=["GET"])
def api_results_status():
    """
    Tiny payload the Review Queue polls to learn whether anything changed
    (new run, new score, retry) without downloading every result each time.
    """
    results = load_results()
    latest_run = max((r.get("run_at") or 0 for r in results), default=0)
    latest_score = max((r.get("scored_at") or 0 for r in results), default=0)
    pending = sum(1 for r in results if not r.get("scored"))
    return jsonify({
        "total": len(results),
        "pending": pending,
        "latest_run_at": latest_run,
        "latest_scored_at": latest_score,
        "signature": f"{len(results)}:{pending}:{latest_run}:{latest_score}",
    })


@evaluation_bp.route("/api/evaluation/results/<result_id>", methods=["GET"])
def api_get_result(result_id):
    try:
        return jsonify(get_result(result_id))
    except EvaluationError as e:
        return jsonify({"error": str(e)}), 404


@evaluation_bp.route("/api/evaluation/results/<result_id>/auto-evaluate", methods=["POST"])
def api_reevaluate_result(result_id):
    """
    Re-runs ONLY the automatic evaluation engine against a result's
    already-stored evidence (does not re-run the assistant/pipeline).
    Intended to recover a result whose automatic evaluation previously
    failed to complete (see result.evaluation_error) — e.g. a transient
    LLM API error — without falling back to a manual override.
    """
    try:
        result = reevaluate_result(result_id)
    except EvaluationError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify(result)


@evaluation_bp.route("/api/evaluation/results/<result_id>/score", methods=["POST"])
def api_score_result(result_id):
    """
    Body: { "criterion_statuses": {"intent_understanding": "pass", ...},
            "evaluator_notes": "..." }
    Any criterion omitted from criterion_statuses is treated as N/A.
    """
    body = request.get_json(silent=True) or {}
    criterion_statuses = body.get("criterion_statuses") or {}
    evaluator_notes = body.get("evaluator_notes") or ""

    try:
        scored = score_result(result_id, criterion_statuses, evaluator_notes)
    except EvaluationError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(scored)


@evaluation_bp.route("/api/evaluation/results/<result_id>", methods=["DELETE"])
def api_delete_result(result_id):
    try:
        delete_result(result_id)
    except EvaluationError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify({"deleted": result_id})


# ---------------------------------------------------------------------------
# Metrics for the dashboard
# ---------------------------------------------------------------------------

@evaluation_bp.route("/api/evaluation/metrics", methods=["GET"])
def api_metrics():
    return jsonify(full_dashboard_payload())
