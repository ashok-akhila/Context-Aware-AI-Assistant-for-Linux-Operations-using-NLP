"""
app.py

Main Flask backend. Implements the full pipeline from spec:

User -> Natural Language -> Intent Detection -> Context Collection ->
LLM Analysis -> Structured Action -> Safety Validation -> Command +
Explanation + Risk -> User Confirmation -> Validated Execution ->
Actual Output -> (LLM Error Analysis if needed) -> Result shown to user

Intent detection here is a lightweight keyword router that decides WHICH
real context to collect (disk vs process vs service vs file vs log) — it
does not try to replace the LLM for understanding nuance. The LLM does the
actual reasoning/explanation over the real, already-collected data. This
keeps context collection bounded and safe (we never let the LLM decide
which files/logs to read) while still using the LLM for language
understanding and explanation, per spec section 6.
"""

import os
from dotenv import load_dotenv
load_dotenv()  # must run before ai.llm_service reads OPENROUTER_API_KEY from the environment
if not os.environ.get("OPENROUTER_API_KEY"):
    print("\n*** WARNING: OPENROUTER_API_KEY is not set. Copy .env.example to .env in THIS folder "
          "and paste your key, then restart. LLM calls will fail until you do. ***\n")

from flask import Flask, request, jsonify, render_template

from context.system_info import get_full_system_context
from context.process_info import get_process_by_pid

from ai.llm_service import call_llm, LLMResponseError
from ai.prompts import build_error_analysis_prompt
from ai.pipeline import run_assistant_query, PipelineError

from execution.command_executor import execute_command
from evaluation.routes import evaluation_bp

app = Flask(__name__)
app.register_blueprint(evaluation_bp)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/context", methods=["GET"])
def api_context():
    """Raw real system context, for the UI's context panel."""
    return jsonify(get_full_system_context())


@app.route("/api/query", methods=["POST"])
def api_query():
    """
    Main pipeline entry point: natural language in, structured
    analysis/action out. Does NOT execute anything.

    The actual pipeline logic now lives in ai/pipeline.run_assistant_query()
    so the evaluation module can run test cases through the identical code
    path — this route is just the HTTP wrapper around it.
    """
    body = request.get_json(silent=True) or {}
    user_query = (body.get("query") or "").strip()
    if not user_query:
        return jsonify({"error": "Missing 'query' in request body."}), 400

    try:
        result = run_assistant_query(user_query)
    except PipelineError as e:
        app.logger.error(str(e))
        return jsonify({"error": f"Failed to collect real system context: {e}"}), 500
    except LLMResponseError as e:
        return jsonify({
            "error": str(e),
            "context_collected": getattr(e, "context_collected", None),
        }), 502

    return jsonify(result)


@app.route("/api/execute", methods=["POST"])
def api_execute():
    """
    Executes a command ONLY if:
      - it independently passes security validation, AND
      - the user has explicitly confirmed (or it's LOW risk and confirmation
        isn't required).
    This is the only route in the app that touches subprocess.
    """
    body = request.get_json(silent=True) or {}
    command = (body.get("command") or "").strip()
    user_confirmed = bool(body.get("confirmed", False))

    if not command:
        return jsonify({"error": "Missing 'command' in request body."}), 400

    result = execute_command(command, user_confirmed=user_confirmed)
    result_dict = result.to_dict()

    # If it failed to execute for a real reason (not a safety block), or
    # ran but returned a non-zero exit code, ask the LLM to explain the
    # actual error using the real stderr/return code.
    needs_error_explanation = (
        (result.executed and result.return_code != 0) or
        (not result.executed and result.block_reason and "safety layer" not in (result.block_reason or "").lower()
         and "Confirmation required" not in (result.block_reason or ""))
    )

    if needs_error_explanation:
        try:
            prompt = build_error_analysis_prompt(
                command=command,
                stdout=result.stdout,
                stderr=result.stderr,
                return_code=result.return_code if result.executed else -1,
                system_context=get_full_system_context(),
            )
            error_analysis = call_llm(prompt)
            result_dict["ai_error_analysis"] = error_analysis
        except LLMResponseError as e:
            result_dict["ai_error_analysis"] = {"error": str(e)}

    return jsonify(result_dict)


@app.route("/api/process/<int:pid>", methods=["GET"])
def api_process_detail(pid):
    """Real detail for a single process — used before confirming a kill."""
    return jsonify(get_process_by_pid(pid))


if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host="127.0.0.1", port=5000, debug=debug_mode)
