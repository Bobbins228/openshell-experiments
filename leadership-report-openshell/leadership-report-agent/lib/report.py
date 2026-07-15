"""Report validation and normalization."""

from .errors import AgentError


def validate_report(report: dict) -> None:
    """Validate report structure and auto-fix minor formatting issues.

    Raises AgentError if required fields are missing, wrong type, or bullets are malformed.
    """
    if not isinstance(report, dict):
        raise AgentError("Report must be a JSON object.", retriable=False)

    for field in ("date", "summary", "bullets"):
        if field not in report or not report[field]:
            raise AgentError(f"Report missing '{field}' field.", retriable=False)

    if not isinstance(report["date"], str):
        raise AgentError("'date' must be a string.", retriable=False)
    if not isinstance(report["summary"], str):
        raise AgentError("'summary' must be a string.", retriable=False)
    if not isinstance(report["bullets"], list):
        raise AgentError("'bullets' must be a list.", retriable=False)
    if not report["bullets"]:
        raise AgentError("'bullets' must not be empty.", retriable=False)

    for i, b in enumerate(report["bullets"]):
        if not isinstance(b, dict):
            raise AgentError(f"Bullet {i} must be an object.", retriable=False)
        if "label" not in b or "text" not in b:
            raise AgentError(f"Bullet {i} missing 'label' or 'text'.", retriable=False)
        if not isinstance(b["label"], str) or not isinstance(b["text"], str):
            raise AgentError(f"Bullet {i} 'label' and 'text' must be strings.", retriable=False)
        if not b["label"].endswith(":"):
            b["label"] = b["label"].rstrip() + ":"
        if not b["text"].startswith(" "):
            b["text"] = " " + b["text"]
        if not b["text"].endswith("\n"):
            b["text"] = b["text"].rstrip() + "\n"
