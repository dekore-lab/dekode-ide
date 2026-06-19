"""Regex-based GDScript tokenizer for syntax highlighting."""

import re
from config import GDSCRIPT_KEYWORDS, GDSCRIPT_BUILTINS, GDSCRIPT_TYPES

def _kw_pattern(words: set[str]) -> str:
    return r'\b(?:' + '|'.join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + r')\b'

_PATTERNS: list[tuple[str, str]] = [
    ("comment",   r'#.*$'),
    ("string",    (
        r'"""[\s\S]*?"""'
        r"|'''[\s\S]*?'''"
        r'|"(?:[^"\\]|\\.)*"'
        r"|'(?:[^'\\]|\\.)*'"
    )),
    ("number",    r'\b0x[0-9a-fA-F]+\b|\b0b[01]+\b|\b\d+\.?\d*(?:[eE][+-]?\d+)?\b'),
    ("decorator", r'@\w+'),
    ("keyword",   _kw_pattern(GDSCRIPT_KEYWORDS)),
    ("type",      _kw_pattern(GDSCRIPT_TYPES)),
    ("builtin",   _kw_pattern(GDSCRIPT_BUILTINS)),
    ("function",  r'\b[A-Za-z_]\w*(?=\s*\()'),
    ("skip",      r'[\s\S]'),
]

_COMBINED = re.compile(
    '|'.join(f'(?P<{name}>{pattern})' for name, pattern in _PATTERNS),
    re.MULTILINE,
)

# Maps token kind -> TextAreaTheme syntax_styles key
KIND_TO_STYLE: dict[str, str] = {
    "comment":   "comment",
    "string":    "string",
    "number":    "number",
    "decorator": "decorator",
    "keyword":   "keyword",
    "type":      "type",
    "builtin":   "builtin",
    "function":  "function",
}


def tokenize_line(line: str) -> list[tuple[int, int, str]]:
    """Return list of (start, end, style_name) for one source line."""
    tokens: list[tuple[int, int, str]] = []
    for m in _COMBINED.finditer(line):
        kind = m.lastgroup
        if kind and kind != "skip":
            style = KIND_TO_STYLE.get(kind)
            if style:
                tokens.append((m.start(), m.end(), style))
    return tokens


def tokenize_text(text: str) -> dict[int, list[tuple[int, int, str]]]:
    """Return highlights dict for the whole document (line_idx → highlights)."""
    result: dict[int, list[tuple[int, int, str]]] = {}
    for idx, line in enumerate(text.splitlines()):
        hl = tokenize_line(line)
        if hl:
            result[idx] = hl
    return result
