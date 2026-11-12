"""Texts quoted by the diagnosis: secrets masked, cut as factory-live cuts them (ADR 0012).

Ported from factory-live at commit d47f235: `redactSecrets` (packages/domain/src/redact.ts) and `quote` (the module
of the runtime's texts, in packages/domain/src/diagnosis/). The regular expressions keep JavaScript's meaning: `\\w`,
`\\d` and `\\b` are ASCII (`re.ASCII`), `\\s` is JavaScript's set of spaces (`JS_SPACE`), and lengths count UTF-16
code units.
"""

from __future__ import annotations

import math
import re

# JavaScript's `\s` (ECMAScript WhiteSpace and LineTerminator), for a character class.
JS_SPACE = "\\t\\n\\v\\f\\r \\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff"
_JS_SPACE_CHARS = "\t\n\v\f\r \u00a0\u1680\u2028\u2029\u202f\u205f\u3000\ufeff" + "".join(
    map(chr, range(0x2000, 0x200B))
)


def js_regex(pattern: str, flags: int = 0) -> re.Pattern[str]:
    """A JavaScript regular expression (without the `u` flag), compiled with the same meaning in Python: `\\s` and
    `\\S` are JavaScript's spaces, inside or outside a character class; `[\\s\\S]` is any character."""
    out: list[str] = []
    in_class = False
    i = 0
    while i < len(pattern):
        if pattern.startswith(r"[\s\S]", i):
            out.append(r"[\s\S]")
            i += 6
            continue
        char = pattern[i]
        if char == "\\" and i + 1 < len(pattern):
            escaped = pattern[i + 1]
            if escaped == "s":
                out.append(JS_SPACE if in_class else f"[{JS_SPACE}]")
            elif escaped == "S" and not in_class:
                out.append(f"[^{JS_SPACE}]")
            else:
                out.append(pattern[i : i + 2])
            i += 2
            continue
        if char == "[" and not in_class:
            in_class = True
        elif char == "]" and in_class:
            in_class = False
        out.append(char)
        i += 1
    return re.compile("".join(out), flags | re.ASCII)


MASK = "[secret masqué]"
_SECRET_KEYS = "|".join(
    (
        "api[_-]?key",
        "access[_-]?token",
        "refresh[_-]?token",
        "auth[_-]?token",
        "id[_-]?token",
        "client[_-]?secret",
        "secret[_-]?key",
        "private[_-]?key",
        "password",
        "passwd",
        "secret",
        "token",
    )
)
# Each pattern with its replacement, in factory-live's order; `\1` keeps the first group.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (js_regex(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), MASK),
    (js_regex(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), MASK),
    (js_regex(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), MASK),
    (js_regex(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}"), MASK),
    (js_regex(r"\bnvapi-[A-Za-z0-9_-]{20,}"), MASK),
    (js_regex(r"\bfreellmapi-[A-Za-z0-9_-]{16,}"), MASK),
    (js_regex(r"\bAKIA[0-9A-Z]{16}\b"), MASK),
    (js_regex(r"\bhf_[A-Za-z0-9]{30,}\b"), MASK),
    (js_regex(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), MASK),
    (js_regex(r"\bAIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])"), MASK),
    # A Telegram bot token (`<id>:<35 characters>`), even glued to `bot` in its API's URL.
    (js_regex(r"\d{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"), MASK),
    (js_regex(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), MASK),
    (js_regex(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE), rf"\1{MASK}"),
    (js_regex(r"(\bAuthorization:\s*(?:token|basic)\s+)[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE), rf"\1{MASK}"),
    # The password of a URL (`https://user:password@host`): user and host stay readable.
    (js_regex(r"(\b[a-z][a-z0-9+.-]*:\/\/[^/\s:@]+:)[^/\s@]+@", re.IGNORECASE), rf"\1{MASK}@"),
    (
        js_regex(r"""\b([A-Z][A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|PRIVATE_KEY))=("[^"]*"|'[^']*'|[^\s'"]+)"""),
        rf"\1={MASK}",
    ),
    # A secret key in lower case: a quoted value, or at least 6 characters that call nothing.
    (
        js_regex(
            rf"""((?:^|[^\w-])["']?(?:{_SECRET_KEYS})["']?\s*[:=]\s*)("[^"\n]+"|'[^'\n]+'|[A-Za-z0-9._~+/=-]{{6,}}(?![\w(]))""",
            re.IGNORECASE | re.MULTILINE,
        ),
        rf"\1{MASK}",
    ),
)


def redact_secrets(text: str) -> str:
    """Mask the secrets of a text before it is stored or shown."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def js_trim(text: str) -> str:
    """JavaScript's `String.prototype.trim`."""
    return text.strip(_JS_SPACE_CHARS)


def _utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def utf16_slice(text: str, units: int) -> str:
    return text.encode("utf-16-le", "surrogatepass")[: units * 2].decode("utf-16-le", "surrogatepass")


def quote(text: str | None) -> str | None:
    """A text as the diagnosis quotes it: secrets masked, trimmed, cut at 300 UTF-16 code units with an ellipsis."""
    if not text:
        return None
    clean = js_trim(redact_secrets(text))
    return f"{utf16_slice(clean, 299)}…" if _utf16_length(clean) > 300 else clean


def whole(value):
    """A number as JSON holds it: JavaScript has one number type, so 7260.0 is the integer 7260."""
    return int(value) if isinstance(value, float) and value.is_integer() else value


def number_text(value: float) -> str:
    """A number as a JavaScript template writes it: 600, not 600.0."""
    return str(whole(float(value)))


def js_round_text(text: str) -> int | None:
    """JavaScript's `Math.round(Number(text))` for a decimal text: halves go up; what is not a number (`1.2.3`)
    gives `None`, as JavaScript's `NaN` is written `null` in JSON."""
    try:
        value = float(text)
    except ValueError:
        return None
    return math.floor(value + 0.5) if math.isfinite(value) else None
