"""CodeEditor widget — TextArea subclass with GDScript highlighting."""

from __future__ import annotations

import re
from pathlib import Path

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events
from textual.reactive import reactive
from textual.strip import Strip
from textual.widgets import Static, TextArea
from textual.widgets.text_area import Selection, TextAreaTheme

from highlighter import tokenize_text

# ── Completion item ───────────────────────────────────────────────────────────

from typing import NamedTuple

class _Item(NamedTuple):
    display: str        # text shown in popup
    name: str           # identifier used for prefix matching
    func_sig: str       # "name(params) -> ret:" inserted in func-declaration context
    call_text: str      # "name(param_names)" inserted in call context
    cursor_inside: bool # True → cursor before closing ) in call context

# ── GDScript completion word list ─────────────────────────────────────────────

_STATIC_ITEMS: list[_Item] = [_Item(w, w, "", "", False) for w in sorted({
    # keywords
    "func", "var", "const", "if", "elif", "else", "for", "while", "match",
    "return", "class", "class_name", "extends", "pass", "break", "continue",
    "and", "or", "not", "null", "true", "false", "self", "signal", "enum",
    "static", "await", "yield",
    # built-in types
    "int", "float", "bool", "String", "Vector2", "Vector2i", "Vector3",
    "Vector3i", "Vector4", "Rect2", "Transform2D", "Transform3D", "Color",
    "Array", "Dictionary", "NodePath", "Callable", "Signal", "Basis",
    "Quaternion", "Plane", "AABB", "RID", "Object", "Resource",
    "PackedByteArray", "PackedInt32Array", "PackedFloat32Array",
    "PackedStringArray", "PackedVector2Array", "PackedVector3Array",
    # built-in functions
    "print", "prints", "printerr", "push_error", "push_warning", "range",
    "len", "abs", "ceil", "floor", "round", "sqrt", "pow", "max", "min",
    "clamp", "lerp", "sign", "snapped", "deg_to_rad", "rad_to_deg", "sin",
    "cos", "tan", "atan2", "typeof", "type_string", "str", "load", "preload",
    "instantiate", "get_node", "find_child", "add_child", "remove_child",
    "queue_free", "emit_signal", "connect", "disconnect", "has_signal",
    "set_process", "set_physics_process", "get_parent", "get_children",
    "get_child", "get_child_count", "is_instance_valid", "weakref", "randi",
    "randf", "randi_range", "randf_range", "randomize", "seed",
    # nodes
    "Node", "Node2D", "Node3D", "CharacterBody2D", "CharacterBody3D",
    "RigidBody2D", "RigidBody3D", "StaticBody2D", "StaticBody3D",
    "Area2D", "Area3D", "Sprite2D", "Sprite3D", "AnimationPlayer",
    "AnimationTree", "Camera2D", "Camera3D", "CollisionShape2D",
    "CollisionShape3D", "Timer", "Label", "Button", "TextEdit", "LineEdit",
    "RichTextLabel", "VBoxContainer", "HBoxContainer", "Control",
    "CanvasLayer", "TileMap", "AudioStreamPlayer", "AudioStreamPlayer2D",
    "AudioStreamPlayer3D", "Path2D", "PathFollow2D", "NavigationAgent2D",
    "NavigationAgent3D", "MeshInstance3D", "DirectionalLight3D",
    "OmniLight3D", "SpotLight3D", "WorldEnvironment",
    # annotations
    "@export", "@export_range", "@export_enum", "@export_group",
    "@export_subgroup", "@onready", "@static_var", "@tool", "@warning_ignore",
})]

# Pairs for auto-complete: opener → closer
_AUTO_OPEN   = {"(": ")", "[": "]", "{": "}", '"': '"', "'": "'"}
# Non-symmetric closers only (brackets), handled separately from quotes
_CLOSERS_ONLY = {")", "]", "}"}

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

# ── Completion popup ──────────────────────────────────────────────────────────

class CompletionList(Static):
    DEFAULT_CSS = """
    CompletionList {
        layer: overlay;
        display: none;
        background: #000080;
        border: solid #00aaaa;
        width: auto;
        height: auto;
        padding: 0;
    }
    """

    def __init__(self) -> None:
        super().__init__("")

    def show_items(self, items: list[_Item], selected: int) -> None:
        t = Text()
        for i, item in enumerate(items):
            style = "bold black on #00ffff" if i == selected else "white on #000080"
            t.append(f" {item.display} \n", style=style)
        self.update(t)
        self.display = True

    def hide(self) -> None:
        self.display = False


# ── Editor widget ─────────────────────────────────────────────────────────────

class CodeEditor(TextArea):
    """Editable code area with regex-based GDScript syntax highlighting."""

    file_path: reactive[Path | None] = reactive(None)
    is_modified: reactive[bool] = reactive(False)

    def __init__(self, **kwargs) -> None:
        self._search_query: str = ""
        self._search_results: list[tuple[int, int]] = []
        self._search_index: int = 0
        self._suppress_change: bool = False
        self._suppress_completions: bool = False
        self._ac_active: bool = False
        self._ac_items: list[_Item] = []
        self._ac_index: int = 0
        self._completion_list: CompletionList | None = None
        self._api_data: dict = {}
        self._extends_class: str = ""
        self._candidates: list[_Item] = list(_STATIC_ITEMS)
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
                inner_pos = gw - col
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
        cl = CompletionList()
        self._completion_list = cl
        self.app.screen.mount(cl)

    def on_blur(self) -> None:
        self._close_completions()

    # ── Key handling ──────────────────────────────────────────────────────────
    # on_key fires before TextArea's internal _on_key; prevent_default() stops
    # both character insertion and key bindings.

    def on_key(self, event: events.Key) -> None:
        # ── Completion navigation ──────────────────────────────────────────────
        if self._ac_active:
            if event.key == "escape":
                self._close_completions()
                event.prevent_default()
                event.stop()
                return
            if event.key == "up":
                self._ac_index = max(0, self._ac_index - 1)
                self._show_completions()
                event.prevent_default()
                event.stop()
                return
            if event.key == "down":
                self._ac_index = min(len(self._ac_items) - 1, self._ac_index + 1)
                self._show_completions()
                event.prevent_default()
                event.stop()
                return
            if event.key in ("tab", "enter"):
                self._accept_completion()
                event.prevent_default()
                event.stop()
                return

        char = event.character

        # ── Auto-pairs: opening bracket / quote ───────────────────────────────
        if char and char in _AUTO_OPEN and not self._has_selection():
            closer = _AUTO_OPEN[char]
            row, col = self.cursor_location
            line = self._get_line(row)
            # Symmetric pair (" or '): if closer already sits to the right, skip
            if closer == char and col < len(line) and line[col] == closer:
                self.move_cursor((row, col + 1))
                event.prevent_default()
                event.stop()
                return
            # Insert pair and leave cursor between them
            self.replace(char + closer, (row, col), (row, col))
            self.move_cursor((row, col + 1))
            event.prevent_default()
            event.stop()
            return

        # ── Auto-pairs: skip duplicate closing bracket ────────────────────────
        if char and char in _CLOSERS_ONLY and not self._has_selection():
            row, col = self.cursor_location
            line = self._get_line(row)
            if col < len(line) and line[col] == char:
                self.move_cursor((row, col + 1))
                event.prevent_default()
                event.stop()
                return

        # ── Auto-pairs: backspace deletes matched pair ────────────────────────
        if event.key == "backspace" and not self._has_selection():
            row, col = self.cursor_location
            if col > 0:
                line = self._get_line(row)
                left  = line[col - 1]
                right = line[col] if col < len(line) else ""
                if left in _AUTO_OPEN and right == _AUTO_OPEN[left]:
                    self.replace("", (row, col - 1), (row, col + 1))
                    event.prevent_default()
                    event.stop()
                    return

    # ── Text change ───────────────────────────────────────────────────────────

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if not self._suppress_change:
            self.is_modified = True
        self._rebuild_highlights()
        if not self._suppress_completions:
            self._update_completions()

    # ── Completion internals ──────────────────────────────────────────────────

    def _update_completions(self) -> None:
        row, col = self.cursor_location
        line = self._get_line(row)
        start = col
        while start > 0 and (line[start - 1].isalnum() or line[start - 1] in "_@"):
            start -= 1
        prefix = line[start:col]

        if len(prefix) < 2:
            self._close_completions()
            return

        matches = [item for item in self._candidates if item.name.startswith(prefix)][:8]
        if not matches:
            self._close_completions()
            return

        self._ac_items  = matches
        self._ac_index  = 0
        self._ac_active = True
        self._show_completions()

    def _show_completions(self) -> None:
        if self._completion_list is None or not self._ac_items:
            return
        row, col   = self.cursor_location
        scroll     = self.scroll_offset
        region     = self.content_region
        gw         = self.gutter_width

        x = region.x + gw + col - scroll.x
        y = region.y + (row - scroll.y) + 1

        # Flip above cursor if popup would overflow screen bottom
        list_h = len(self._ac_items) + 2
        if y + list_h > self.app.screen.size.height:
            y = region.y + (row - scroll.y) - list_h

        cl = self._completion_list
        cl.styles.offset = (x, y)
        cl.show_items(self._ac_items, self._ac_index)

    def _close_completions(self) -> None:
        self._ac_active = False
        self._ac_items  = []
        if self._completion_list is not None:
            self._completion_list.hide()

    def _accept_completion(self) -> None:
        if not self._ac_items:
            return
        item = self._ac_items[self._ac_index]
        row, col = self.cursor_location
        line = self._get_line(row)
        start = col
        while start > 0 and (line[start - 1].isalnum() or line[start - 1] in "_@"):
            start -= 1

        # Detect "func " context: the part of the line before the typed word
        # (stripped) equals "func" or ends with " func"
        before = line[:start].strip()
        in_func = bool(item.func_sig) and (
            before == "func" or before.endswith(" func")
        )

        if in_func:
            text       = item.func_sig
            cursor_col = start + len(text)
        elif item.call_text:
            text       = item.call_text
            # cursor inside parens when there are params, else after closing )
            cursor_col = start + len(text) - (1 if item.cursor_inside else 0)
        else:
            text       = item.name
            cursor_col = start + len(text)

        self._suppress_completions = True
        self.replace(text, (row, start), (row, col))
        self.move_cursor((row, cursor_col))
        self._close_completions()
        self.call_after_refresh(self._reset_suppress_completions)

    def _reset_suppress_completions(self) -> None:
        self._suppress_completions = False

    # ── API / completions setup ───────────────────────────────────────────────

    def set_api_data(self, classes: dict) -> None:
        """Receive parsed Godot class data; rebuild candidate list."""
        self._api_data = classes
        self._rebuild_class_completions()

    @staticmethod
    def _parse_extends(text: str) -> str:
        first = text.split("\n")[0].strip() if text else ""
        m = re.match(r"^extends\s+(\w+)", first)
        return m.group(1) if m else ""

    def _rebuild_class_completions(self) -> None:
        """Walk inherits chain and build _Item candidates with full signatures."""
        if not self._api_data or not self._extends_class:
            self._candidates = list(_STATIC_ITEMS)
            return

        methods:    list[_Item] = []
        properties: list[_Item] = []
        seen: set[str] = set()
        current = self._extends_class

        while current:
            cls = self._api_data.get(current)
            if cls is None:
                break
            for m in cls.get("methods", []):
                name = m["name"]
                if name not in seen:
                    seen.add(name)
                    params = m.get("params", [])
                    ret    = m.get("return_type", "void")
                    # param string with types (for display and func sig)
                    parts = []
                    for p in params:
                        s = f"{p['name']}: {p['type']}" if p.get("type") else p["name"]
                        if p.get("default"):
                            s += f" = {p['default']}"
                        parts.append(s)
                    pdisplay = ", ".join(parts)
                    # call text: param names only
                    pcall = ", ".join(p["name"] for p in params)
                    methods.append(_Item(
                        display       = f"{name}({pdisplay}) -> {ret}",
                        name          = name,
                        func_sig      = f"{name}({pdisplay}) -> {ret}:",
                        call_text     = f"{name}({pcall})",
                        cursor_inside = len(params) > 0,
                    ))
            for p in cls.get("properties", []):
                name = p["name"]
                if name not in seen:
                    seen.add(name)
                    typ = p.get("type", "")
                    properties.append(_Item(
                        display       = f"{name}: {typ}" if typ else name,
                        name          = name,
                        func_sig      = "",
                        call_text     = "",
                        cursor_inside = False,
                    ))
            current = cls.get("inherits", "")

        self._candidates = methods + properties + list(_STATIC_ITEMS)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_line(self, row: int) -> str:
        lines = self.text.split("\n")
        return lines[row] if row < len(lines) else ""

    def _has_selection(self) -> bool:
        return self.selection.start != self.selection.end

    # ── File I/O ──────────────────────────────────────────────────────────────

    def load_file(self, path: Path) -> bool:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self.notify(f"Cannot open: {exc}", severity="error")
            return False
        self._suppress_change = True
        self.load_text(content)
        self.file_path = path
        self.is_modified = False
        self._rebuild_highlights()
        self._extends_class = self._parse_extends(content) if path.suffix == ".gd" else ""
        self._rebuild_class_completions()
        self.call_after_refresh(self._end_suppress)
        return True

    def _end_suppress(self) -> None:
        self._suppress_change = False

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
        self._search_query   = query
        self._search_results = []
        self._search_index   = 0

        if not query:
            return 0

        text    = self.text
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        lines   = text.splitlines()
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
        end_col   = col + len(self._search_query)
        self.selection = Selection((line, col), (line, end_col))
        self.scroll_cursor_visible(center=True)

    def replace_current(self, replacement: str) -> bool:
        if not self._search_results:
            return False
        line, col = self._search_results[self._search_index]
        end_col   = col + len(self._search_query)
        self.replace(replacement, (line, col), (line, end_col))
        self.search(self._search_query)
        return True

    def replace_all(self, replacement: str) -> int:
        if not self._search_results or not self._search_query:
            return 0
        count    = len(self._search_results)
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
