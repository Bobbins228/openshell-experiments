"""Report validation and normalization."""

from datetime import datetime

from .errors import AgentError


def _validate_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgentError(f"'{field}' must be a non-empty string.", retriable=False)
    try:
        value.encode("utf-16-le")
    except UnicodeEncodeError as exc:
        raise AgentError(f"'{field}' contains invalid Unicode.", retriable=False) from exc
    return value


def validate_report(report: dict) -> None:
    """Validate report structure and normalize minor formatting issues.

    Raises AgentError if required fields are missing or bullets are malformed.
    """
    if not isinstance(report, dict):
        raise AgentError("Report must be a JSON object.", retriable=False)

    for field in ("date", "summary"):
        _validate_text(report.get(field), field)

    try:
        datetime.strptime(report["date"], "%b %d, %Y")
    except ValueError as exc:
        raise AgentError("Report 'date' must use the format 'Mon DD, YYYY'.", retriable=False) from exc

    bullets = report.get("bullets")
    if not isinstance(bullets, list) or not bullets:
        raise AgentError("'bullets' must be a non-empty list.", retriable=False)

    for index, bullet in enumerate(bullets):
        if not isinstance(bullet, dict):
            raise AgentError(f"Bullet {index} must be an object.", retriable=False)

        label = _validate_text(bullet.get("label"), f"bullet {index} label")
        text = _validate_text(bullet.get("text"), f"bullet {index} text")

        if not label.endswith(":"):
            bullet["label"] = label.rstrip() + ":"
        if not text.startswith(" "):
            bullet["text"] = " " + text
        if not bullet["text"].endswith("\n"):
            bullet["text"] = bullet["text"].rstrip() + "\n"
