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
    re.compile(r"\bsb_secret_[A-Za-z0-9_-]{20,}"),
    re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{20,}"),
    re.compile(r"\bhf_[A-Za-z0-9]{30,}"),
    re.compile(r"\bya29\.[\w-]{20,}"),
    re.compile(r"\bSG\.[\w-]{16,}\.[\w-]{16,}"),
    re.compile(r"\bxapp-[\w-]{10,}"),
]

_KW = r"(?:_key|apikey|secret|token|pass|auth(?!or)|credential)"
_STRICT_KW = re.compile(r"secret|token|password|passwd|_key|api_key|credential", re.I)
# Names start at a word boundary and the keyword is found by a lazy lookahead, then the
# whole word is taken possessively: no backtracking, so long word runs stay linear.
_ASSIGN_QUOTED = re.compile(
    r"""(\b(?=\w*?(?:password|passwd|pwd|secret|token|api[_-]?key|credential))\w++(?:-key\w*+)?["']?"""
    r"""(?:[ \t]*:[ \t]*[\w.\[\]|][\w.\[\], |]{0,80}?)?[ \t]*[:=][ \t]*)(["'])[^"'\n]{4,}\2""",
    re.I,
)
_ASSIGN_BARE = re.compile(
    r"\b(?=\w*?" + _KW + r")(\w++)(=|:[ \t]?|[ \t]+=[ \t]*|=[ \t]+)([^\s\"',;)\]}>]{4,})", re.I
)
_URL_PASS = re.compile(r"(\b\w+://[^\s:/@]+:)[^\s@/]+(@)")
_BEARER = re.compile(r"(\bBearer\s+)[A-Za-z0-9._~+/=-]{6,}", re.I)
_CURL_U = re.compile(r"(\s(?:-u|--user)\s+[^\s:]+:)\S+")
_ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*[ \t]*=[ \t]*)([^\n]*[^ \t\n])?[ \t]*$", re.M)
_CANDIDATE = re.compile(r"[A-Za-z0-9+/=_-]{32,}")
_HEX = re.compile(r"[0-9a-fA-F-]+")


def _entropy(s: str) -> float:
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def _mask_entropy(m: re.Match) -> str:
    tok = m.group(0)
    mixed = sum(c.isdigit() or c in "+=" for c in tok)
    if _HEX.fullmatch(tok) or mixed < len(tok) * 0.1 or _entropy(tok) < 4.0:
        return tok
    return MASK


def _mask_bare(m: re.Match) -> str:
    name, sep, val = m.groups()
    if val.startswith("•") or "(" in val or (val.isdigit() and len(val) < 6):
        return m.group(0)
    if sep[0] != ":" and sep != "=":  # `name = value`: only for clear secret names and plain values
        if not _STRICT_KW.search(name) or val[0] == "=" or "[" in val or val.isdigit():
            return m.group(0)
    if not name.isupper() and not any(c.isdigit() for c in val):
        return m.group(0)
    return name + sep + MASK


def mask_text(text: str, path: str | None = None) -> str:
    for p in _KEY_PATTERNS:
        text = p.sub(MASK, text)
    if path and (path.rsplit("/", 1)[-1].startswith(".env") or path.endswith(".env")):
        text = _ENV_LINE.sub(lambda m: m.group(1) + MASK if m.group(2) else m.group(0), text)
    text = _URL_PASS.sub(lambda m: m.group(1) + MASK + m.group(2), text)
    text = _BEARER.sub(lambda m: m.group(1) + MASK, text)
    text = _CURL_U.sub(lambda m: m.group(1) + MASK, text)
    text = _ASSIGN_QUOTED.sub(lambda m: m.group(1) + m.group(2) + MASK + m.group(2), text)
    text = _ASSIGN_BARE.sub(_mask_bare, text)
    return _CANDIDATE.sub(_mask_entropy, text)
