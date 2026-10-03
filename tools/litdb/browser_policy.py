from __future__ import annotations

import re
from urllib.parse import urlsplit

INJECTION_PATTERNS = (
    re.compile(r"(?i)ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions"),
    re.compile(r"(?i)(?:upload|exfiltrate|send)\s+(?:your\s+)?(?:local\s+)?files?"),
    re.compile(r"(?i)(?:run|execute)\s+(?:this\s+)?(?:shell|terminal|command)"),
    re.compile(r"(?i)(?:reveal|print|copy)\s+(?:the\s+)?(?:password|cookie|token|secret|authorization)"),
    re.compile(r"(?i)change\s+(?:the\s+)?(?:venue|output\s+path|acceptance|threshold|allowed\s+domains?)"),
)


def domain_allowed(url: str, allowed_domains: list[str]) -> bool:
    hostname = (urlsplit(url).hostname or "").lower().rstrip(".")
    return any(hostname == domain.lower() or hostname.endswith("." + domain.lower()) for domain in allowed_domains)


def detect_prompt_injection(text: str) -> list[str]:
    return [pattern.pattern for pattern in INJECTION_PATTERNS if pattern.search(text)]
