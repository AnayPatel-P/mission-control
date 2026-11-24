import math
import re
from collections import Counter

MASK = "•••masked•••"

_KEY_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),
]

_ASSIGN = re.compile(
    r"""(\w*(?:password|passwd|pwd|secret|token|api[_-]?key|credential)\w*["']?\s*[:=]\s*["']?)"""
    r"""([^\s"',;]{4,})""",
    re.I,
)
_ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*\s*=\s*)(.*?)\s*$", re.M)
_CANDIDATE = re.compile(r"[A-Za-z0-9+/=_-]{32,}")
_HEX = re.compile(r"[0-9a-fA-F-]+")


def _entropy(s: str) -> float:
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def _mask_entropy(m: re.Match) -> str:
    tok = m.group(0)
    if _HEX.fullmatch(tok) or _entropy(tok) < 4.0:
        return tok
    return MASK


def mask_text(text: str, path: str | None = None) -> str:
    for p in _KEY_PATTERNS:
        text = p.sub(MASK, text)
    if path and (path.rsplit("/", 1)[-1].startswith(".env") or path.endswith(".env")):
        text = _ENV_LINE.sub(lambda m: m.group(1) + MASK if m.group(2) else m.group(0), text)
    text = _ASSIGN.sub(lambda m: m.group(1) + MASK, text)
    return _CANDIDATE.sub(_mask_entropy, text)
