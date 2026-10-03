from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any


def normalize_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, list):
        return [normalize_scalar(item) for item in value]
    text = unicodedata.normalize("NFKC", str(value)).replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if text.startswith(("http://", "https://")):
        text = text.rstrip("/")
    return text


def normalize_publication_date_for_replay(value: Any) -> Any:
    """Normalize only a complete numeric date's separator for replay comparison."""
    value = normalize_scalar(value)
    if not isinstance(value, str):
        return value
    match = re.fullmatch(r"([0-9]{4})[./-]([0-9]{2})[./-]([0-9]{2})", value)
    if not match:
        return value
    return "-".join(match.groups())


def _collapse_tandem_tokens(tokens: list[str], max_block: int = 16) -> list[str]:
    """Collapse adjacent duplicate token blocks emitted by MathJax visual+A11y layers."""
    changed = True
    while changed:
        changed = False
        result: list[str] = []
        index = 0
        while index < len(tokens):
            collapsed = False
            largest = min(max_block, (len(tokens) - index) // 2)
            for size in range(largest, 0, -1):
                if tokens[index:index + size] == tokens[index + size:index + 2 * size]:
                    result.extend(tokens[index:index + size])
                    index += 2 * size
                    changed = True
                    collapsed = True
                    break
            if not collapsed:
                result.append(tokens[index])
                index += 1
        tokens = result
    return tokens


def normalize_abstract_for_replay(value: Any) -> Any:
    """Comparison-only abstract signature; raw extracted abstracts remain unchanged.

    The strict text is first normalized. A token signature then removes only adjacent
    tandem repetitions, the common artifact when MathJax exposes both rendered and
    accessibility text. It never imputes or changes the stored source value.
    """
    text = normalize_scalar(value)
    if text is None:
        return None
    text = text.replace("ℓ", "l")
    # MathJax visual layers often expose a subscript as `l 1`, while its
    # accessibility/semantic layer exposes the equivalent compact token `l1`.
    text = re.sub(r"\b([A-Za-z])\s+([0-9]+)\b", r"\1\2", text)
    # The accessibility layer may spell a visual compound such as `nlogK` as
    # `n log K`. Compact only this algebraic log form so an adjacent visual and
    # accessibility rendering becomes an exact tandem block below.
    text = re.sub(
        r"\b([A-Za-z])\s+log\s+([A-Za-z])\b",
        r"\1log\2",
        text,
        flags=re.IGNORECASE,
    )
    # Superscripts can be exposed twice as `10 4 10 4`, while another DOM read
    # yields the compact visual token `104`. Compact only an immediately
    # repeated numeric pair; ordinary adjacent numbers remain distinguishable.
    text = re.sub(
        r"\b([0-9]+)\s+([0-9]+)\s+\1\s+\2\b",
        lambda match: match.group(1) + match.group(2),
        text,
    )
    tokens = re.findall(r"[A-Za-z0-9]+", text.casefold())
    return " ".join(_collapse_tandem_tokens(tokens))


_MATH_SURFACE_MARKER = re.compile(
    r"[\\$∑√Ωω≈≤≥]|[\U0001D400-\U0001D7FF]"
)


def math_rendering_equivalent_for_replay(expected: Any, observed: Any) -> bool:
    """Recognize a narrow MathJax/BibTeX source-surface change.

    This is comparison-only. It requires explicit mathematical rendering markers
    on both source values, long abstracts, high ordered-token similarity, and high
    token-set overlap. Stored source text is never rewritten or imputed.
    """
    expected_scalar = normalize_scalar(expected)
    observed_scalar = normalize_scalar(observed)
    if not isinstance(expected_scalar, str) or not isinstance(observed_scalar, str):
        return False
    if not _MATH_SURFACE_MARKER.search(expected_scalar):
        return False
    if not _MATH_SURFACE_MARKER.search(observed_scalar):
        return False
    expected_tokens = str(normalize_abstract_for_replay(expected_scalar)).split()
    observed_tokens = str(normalize_abstract_for_replay(observed_scalar)).split()
    if min(len(expected_tokens), len(observed_tokens)) < 50:
        return False
    sequence_ratio = difflib.SequenceMatcher(
        a=expected_tokens,
        b=observed_tokens,
        autojunk=False,
    ).ratio()
    expected_set = set(expected_tokens)
    observed_set = set(observed_tokens)
    union = expected_set | observed_set
    jaccard = len(expected_set & observed_set) / len(union) if union else 1.0
    return sequence_ratio >= 0.85 and jaccard >= 0.90
