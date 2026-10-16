"""
ai/pipeline.py

The core "natural language query -> structured, safety-validated response"
pipeline. This is the exact logic that used to live inline inside
app.py's api_query() route — pulled out unchanged so both the live Flask
route AND the new evaluation module can run a query through the identical
code path. No behavior change for the existing app: api_query() now just
calls run_assistant_query() and jsonifies the result.
"""

import re
import traceback

from context.system_info import get_full_system_context
from context.disk_info import get_partitions, get_root_disk_summary, find_largest_directories
from context.process_info import get_all_processes, get_my_processes
from context.file_info import analyze_path
from logs.log_analyzer import get_service_status, get_service_logs, get_recent_errors

from ai.llm_service import call_llm, LLMResponseError
from ai.prompts import build_analysis_prompt

from security.command_validator import validate_command


def detect_intent_and_collect_context(user_query: str) -> dict:
    """Unchanged from the original app.py — see that file's prior history for notes."""
    q = user_query.lower()
    context = {"system": get_full_system_context()}

    file_match = re.search(r'([./~][\w./\-]+|\b[\w\-]+\.\w{1,5}\b)', user_query)
    is_file_question = any(kw in q for kw in ["file", "access", "permission", "can't open", "cannot open"])

    if is_file_question and file_match:
        context["file_analysis"] = analyze_path(file_match.group(1))

    if any(kw in q for kw in ["disk", "storage", "space", "full"]):
        context["disk"] = {
            "partitions": get_partitions(),
            "root_summary": get_root_disk_summary(),
        }
        if any(kw in q for kw in ["large", "biggest", "what's taking", "taking up"]):
            context["disk"]["largest_directories"] = find_largest_directories("/", top_n=10)

    if any(kw in q for kw in ["cpu", "processor"]):
        context["cpu_detail"] = get_all_processes(sort_by="cpu", limit=10)

    if any(kw in q for kw in ["ram", "memory"]):
        context["memory_detail"] = get_all_processes(sort_by="memory", limit=10)

    if any(kw in q for kw in ["process", "running", "consuming"]):
        context["processes"] = get_all_processes(sort_by="cpu", limit=15)
        if "my" in q or "belonging to" in q:
            context["my_processes"] = get_my_processes(limit=15)

    service_match = re.search(r'\b(ssh|nginx|apache2?|mysql|postgres(?:ql)?|docker|cron|network[- ]?manager)\b', q)
    if any(kw in q for kw in ["service", "ssh", "daemon", "fail", "not working", "stopped", "crash"]):
        service_name = service_match.group(1).replace(" ", "-") if service_match else "ssh"
        context["service_status"] = get_service_status(service_name)
        context["service_logs"] = get_service_logs(service_name, lines=40)

    if any(kw in q for kw in ["log", "error", "why did"]) and "service_logs" not in context:
        context["recent_errors"] = get_recent_errors(lines=40)

    return context


class PipelineError(Exception):
    """Raised when context collection itself fails (mirrors the old 500 path)."""
    def __init__(self, message, status_code=500):
        super().__init__(message)
        self.status_code = status_code


def run_assistant_query(user_query: str) -> dict:
    """
    Runs the full natural-language-in -> structured-response-out pipeline.
    Returns a dict shaped exactly like the old api_query() JSON body:
      {"llm_result": ..., "backend_validation": ..., "context_collected": ...}

    Raises PipelineError if context collection fails, or LLMResponseError
    (re-raised, not caught) if the LLM call fails — callers decide how to
    surface each (the Flask route turns them into HTTP error responses;
    the evaluator records them as part of the test result instead).
    """
    try:
        collected_context = detect_intent_and_collect_context(user_query)
    except Exception as e:
        raise PipelineError(f"Failed to collect real system context: {e}\n{traceback.format_exc()}")

    prompt = build_analysis_prompt(
        user_query,
        collected_context.get("system", {}),
        extra_context={k: v for k, v in collected_context.items() if k != "system"},
    )

    # LLMResponseError intentionally propagates uncaught — see docstring.
    # We attach the context we DID manage to collect to the exception so
    # callers (like the evaluator) can still record/inspect it even though
    # the LLM step failed — that context is real and useful for review.
    try:
        llm_result = call_llm(prompt)
    except LLMResponseError as e:
        e.context_collected = collected_context
        raise

    backend_validation = None
    if llm_result.get("command"):
        v = validate_command(llm_result["command"])
        backend_validation = v.to_dict()
        llm_result["risk_level"] = backend_validation["risk_level"]
        llm_result["requires_confirmation"] = backend_validation["requires_confirmation"]
        if not backend_validation["allowed"]:
            llm_result["action"] = "analyze"
            llm_result["explanation"] += (
                f" (Note: a proposed command was blocked by the safety layer: "
                f"{backend_validation['reason']})"
            )
            llm_result["command"] = None

    return {
        "llm_result": llm_result,
        "backend_validation": backend_validation,
        "context_collected": collected_context,
    }
