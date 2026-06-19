"""CodeEditor widget — TextArea subclass with GDScript highlighting."""

from __future__ import annotations

import re
from pathlib import Path

from rich.segment import Segment
from rich.style import Style
from textual.reactive import reactive
from textual.strip import Strip
from textual.widgets import TextArea
from textual.widgets.text_area import Selection, TextAreaTheme

from highlighter import tokenize_text

# ── Far Manager–style TextArea theme ─────────────────────────────────────────

_DOS_THEME = TextAreaTheme(
    name="dos",
    base_style=Style.parse("white on black"),
    gutter_style=Style.parse("bright_cyan on black"),
    cursor_style=Style.parse("black on bright_cyan"),
    cursor_line_style=Style.parse("on grey7"),
    bracket_matching_style=Style.parse("bold on dark_orange3"),
    selection_style=Style.parse("on navy_blue"),
    syntax_styles={
        "keyword":   Style.parse("bold bright_blue"),
        "builtin":   Style.parse("bold cyan"),
        "type":      Style.parse("bright_cyan"),
        "string":    Style.parse("dark_orange"),
        "comment":   Style.parse("green"),
        "number":    Style.parse("pale_green1"),
        "function":  Style.parse("yellow"),
        "decorator": Style.parse("magenta"),
    },
)

# Separator style for the │ between line numbers and code
_SEP_STYLE = Style.parse("bright_cyan on black")

# ── Editor widget ─────────────────────────────────────────────────────────────

class CodeEditor(TextArea):
    """Editable code area with regex-based GDScript syntax highlighting."""

    file_path: reactive[Path | None] = reactive(None)
    is_modified: reactive[bool] = reactive(False)

    def __init__(self, **kwargs) -> None:
        self._search_query: str = ""
        self._search_results: list[tuple[int, int]] = []
        self._search_index: int = 0
        super().__init__(
            "",
            language=None,
            theme="css",        # builtin placeholder; switched below
            show_line_numbers=True,
            **kwargs,
        )
        self.register_theme(_DOS_THEME)
        self.theme = "dos"

    # ── Highlighting ──────────────────────────────────────────────────────────

    def _rebuild_highlights(self) -> None:
        try:
            hl = self._highlights
            hl.clear()
            try:
                self._line_cache.clear()
            except AttributeError:
                pass
            for line_idx, tokens in tokenize_text(self.text).items():
                hl[line_idx].extend(tokens)
            self.refresh()
        except Exception:
            pass

    def _build_highlight_map(self) -> None:
        self._rebuild_highlights()

    # ── Line rendering: inject │ between gutter and code ─────────────────────

    def _render_line(self, y: int) -> Strip:
        strip = super()._render_line(y)
        if not self.show_line_numbers:
            return strip

        gw = self.gutter_width
        if gw <= 0:
            return strip

        # Walk the segments and replace the last char of the gutter with │
        segments = list(strip)
        new_segs: list[Segment] = []
        col = 0
        modified = False

        for seg in segments:
            if modified:
                new_segs.append(seg)
                continue

            text = seg.text
            seg_len = len(text)

            if col + seg_len >= gw and not modified:
                # This segment straddles or ends the gutter boundary.
                # Replace the character at position (gw - 1) within this seg
                # with '│'.
                inner_pos = gw - col   # end of gutter within this segment
                if inner_pos >= 1:
                    before = text[: inner_pos - 1]
                    after  = text[inner_pos:]
                    if before:
                        new_segs.append(Segment(before, seg.style))
                    new_segs.append(Segment("│", _SEP_STYLE))
                    if after:
                        new_segs.append(Segment(after, seg.style))
                else:
                    new_segs.append(seg)
                modified = True
            else:
                new_segs.append(seg)

            col += seg_len

        return Strip(new_segs)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self._rebuild_highlights()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self.is_modified = True
        self._rebuild_highlights()

    # ── File I/O ──────────────────────────────────────────────────────────────

    def load_file(self, path: Path) -> bool:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self.notify(f"Cannot open: {exc}", severity="error")
            return False
        self.load_text(content)
        self.file_path = path
        self.is_modified = False
        self._rebuild_highlights()
        return True

    def save_file(self) -> bool:
        if self.file_path is None:
            return False
        try:
            self.file_path.write_text(self.text, encoding="utf-8")
            self.is_modified = False
            self.notify(f"Saved \"{self.file_path.name}\"", severity="information")
            return True
        except OSError as exc:
            self.notify(f"Save failed: {exc}", severity="error")
            return False

    # ── Search ────────────────────────────────────────────────────────────────

    def search(self, query: str) -> int:
        """Find all occurrences of query; move cursor to first match. Returns count."""
        self._search_query = query
        self._search_results = []
        self._search_index = 0

        if not query:
            return 0

        text = self.text
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        lines = text.splitlines()
        for line_idx, line in enumerate(lines):
            for m in pattern.finditer(line):
                self._search_results.append((line_idx, m.start()))

        if self._search_results:
            self._jump_to_match(0)
        return len(self._search_results)

    def search_next(self) -> None:
        if not self._search_results:
            return
        self._search_index = (self._search_index + 1) % len(self._search_results)
        self._jump_to_match(self._search_index)

    def search_prev(self) -> None:
        if not self._search_results:
            return
        self._search_index = (self._search_index - 1) % len(self._search_results)
        self._jump_to_match(self._search_index)

    def _jump_to_match(self, idx: int) -> None:
        line, col = self._search_results[idx]
        end_col = col + len(self._search_query)
        self.selection = Selection((line, col), (line, end_col))
        self.scroll_cursor_visible(center=True)

    def replace_current(self, replacement: str) -> bool:
        if not self._search_results:
            return False
        line, col = self._search_results[self._search_index]
        end_col = col + len(self._search_query)
        self.replace(replacement, (line, col), (line, end_col))
        self.search(self._search_query)
        return True

    def replace_all(self, replacement: str) -> int:
        if not self._search_results or not self._search_query:
            return 0
        count = len(self._search_results)
        new_text = re.sub(
            re.escape(self._search_query),
            replacement,
            self.text,
            flags=re.IGNORECASE,
        )
        self.load_text(new_text)
        self.is_modified = True
        self._rebuild_highlights()
        return count

    # ── Helpers ───────────────────────────────────────────────────────────────

    @property
    def cursor_info(self) -> str:
        row, col = self.cursor_location
        return f"Ln {row + 1}, Col {col + 1}"

    @property
    def modified_marker(self) -> str:
        return " [*]" if self.is_modified else ""

    @property
    def title_info(self) -> str:
        name = self.file_path.name if self.file_path else "No file"
        return f"{name}{self.modified_marker}"
