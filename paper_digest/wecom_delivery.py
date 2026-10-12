"""WeCom webhook delivery helpers."""

from __future__ import annotations

import json
import os
import re
import time
from urllib.request import Request, urlopen

from .config import WeComWebhookConfig

_MARKDOWN_LINK_PATTERN = re.compile(r"^(\d+\.\s*)\[(.+)]\((https?://[^)]+)\)$")
# Keep a safety margin below WeCom's single-message limit. The remaining room
# is useful when the webhook serializes the JSON payload.
_MAX_MARKDOWN_BYTES = 3800
_SEND_INTERVAL_SECONDS = 1.0


class WeComDeliveryError(RuntimeError):
    """Raised when WeCom webhook delivery fails."""


def send_wecom_message(
    config: WeComWebhookConfig,
    *,
    title: str,
    body: str,
) -> None:
    """Send a notification, splitting long reports into complete messages."""

    archive_url = _archive_url()
    report_body = body
    if archive_url:
        report_body = f"完整 HTML 报告：\n{archive_url}\n\n{body}"

    content = _normalize_markdown(title, report_body)
    chunks = _chunk_markdown(content)
    total = len(chunks)

    for index, chunk in enumerate(chunks, start=1):
        if total > 1:
            marker = f"> 第 {index}/{total} 条\n\n"
            chunk = marker + chunk
            if len(chunk.encode("utf-8")) > _MAX_MARKDOWN_BYTES:
                chunk = chunk[len(marker) :]
        _post_wecom_message(config, chunk)
        if index < total:
            time.sleep(_SEND_INTERVAL_SECONDS)


def _post_wecom_message(
    config: WeComWebhookConfig,
    content: str,
) -> None:
    """Send one already-sized message to the webhook."""

    request = Request(
        config.webhook_url,
        data=json.dumps(_build_content_payload(content)).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            payload = response.read()
    except OSError as exc:
        raise WeComDeliveryError(
            f"failed to send WeCom webhook notification: {exc}"
        ) from exc

    _validate_response(payload)


def _build_payload(title: str, body: str) -> dict[str, object]:
    return _build_content_payload(_normalize_markdown(title, body))


def _build_content_payload(content: str) -> dict[str, object]:
    return {
        "msgtype": "markdown",
        "markdown": {
            "content": content,
        },
    }


def _normalize_markdown(title: str, body: str) -> str:
    lines = [f"# {title}", ""]
    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue

        match = _MARKDOWN_LINK_PATTERN.match(stripped)
        if match:
            prefix, text, href = match.groups()
            lines.append(f"{prefix}{text}")
            lines.append(f"> {href}")
            continue

        if line.startswith("   - "):
            lines.append("- " + stripped[2:])
            continue
        lines.append(stripped)

    return "\n".join(lines).strip()


def _chunk_markdown(value: str) -> list[str]:
    """Split Markdown without cutting paragraphs or UTF-8 characters."""

    paragraphs = re.split(r"\n{2,}", value.strip())
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate.encode("utf-8")) <= _MAX_MARKDOWN_BYTES:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        if len(paragraph.encode("utf-8")) <= _MAX_MARKDOWN_BYTES:
            current = paragraph
            continue

        chunks.extend(_split_utf8_text(paragraph))

    if current:
        chunks.append(current)
    return chunks or [""]


def _split_utf8_text(value: str) -> list[str]:
    """Split an oversized paragraph at UTF-8 character boundaries."""

    chunks: list[str] = []
    current: list[str] = []
    current_bytes = 0
    for character in value:
        character_bytes = len(character.encode("utf-8"))
        if current and current_bytes + character_bytes > _MAX_MARKDOWN_BYTES:
            chunks.append("".join(current))
            current = []
            current_bytes = 0
        current.append(character)
        current_bytes += character_bytes
    if current:
        chunks.append("".join(current))
    return chunks


def _archive_url() -> str | None:
    """Return an explicit archive URL or derive the default GitHub Pages URL."""

    explicit_url = os.getenv("PAPER_DIGEST_ARCHIVE_URL", "").strip()
    if explicit_url:
        return explicit_url

    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    if "/" not in repository:
        return None
    owner, name = repository.split("/", 1)
    server_url = os.getenv("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    if server_url != "https://github.com":
        return None
    return f"https://{owner}.github.io/{name}/"


def _truncate_markdown(value: str) -> str:
    payload = value.encode("utf-8")
    if len(payload) <= _MAX_MARKDOWN_BYTES:
        return value

    suffix = "\n\n> 内容过长，已截断。"
    suffix_bytes = suffix.encode("utf-8")
    limit = _MAX_MARKDOWN_BYTES - len(suffix_bytes)
    truncated = payload[:limit]
    while truncated:
        try:
            normalized = truncated.decode("utf-8")
        except UnicodeDecodeError:
            truncated = truncated[:-1]
            continue
        return normalized.rstrip() + suffix
    return suffix.strip()


def _validate_response(payload: bytes) -> None:
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise WeComDeliveryError("received malformed JSON from WeCom webhook") from exc

    if not isinstance(raw, dict):
        raise WeComDeliveryError("WeCom webhook response payload is invalid")

    code = raw.get("errcode", 0)
    if isinstance(code, int) and code == 0:
        return

    message = raw.get("errmsg") or "unknown error"
    raise WeComDeliveryError(
        f"WeCom webhook rejected notification with code {code}: {message}"
    )
