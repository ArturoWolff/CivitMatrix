"""Strip secrets from strings before logging or raising."""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"([?&]token=)[^&\s\"']+", re.IGNORECASE)


def redact_secrets(text: str) -> str:
    """Mask ``token=`` query values (API keys on download URLs)."""
    if not text or "token=" not in text.lower():
        return text
    return _TOKEN_RE.sub(r"\1***", text)
