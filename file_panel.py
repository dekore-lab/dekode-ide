"""Far Manager–style file panel widget."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, Static


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_size(size: int) -> str:
    for unit in ("B", "K", "M", "G"):
        if size < 1024:
            return f"{size}{unit}"
        size //= 1024
    return f"{size}T"


def _fmt_date(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m %H:%M")


# ── File panel ────────────────────────────────────────────────────────────────

class FilePanel(Vertical):
    """Single Far Manager–style file browser panel."""

    BINDINGS = [
        Binding("backspace", "go_up",        "Up",     show=False),
        Binding("f7",        "mkdir",         "MkDir",  show=False),
        Binding("f6",        "rename_prompt", "Rename", show=False),
        Binding("delete",    "delete_entry",  "Delete", show=False),
        Binding("ctrl+c",    "copy_prompt",   "Copy",   show=False),
        Binding("ctrl+m",    "move_prompt",   "Move",   show=False),
        Binding("ctrl+f",    "search_file",   "Find",   show=False),
    ]

    current_path: reactive[Path] = reactive(Path.cwd())

    def __init__(self, panel_id: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._panel_id = panel_id
        self._entries: list[Path] = []
        self._filtered: list[Path] = []
        self._filter: str = ""
        self._highlighted_item: ListItem | None = None

    # ── Widget refs ───────────────────────────────────────────────────────────

    @property
    def _list(self) -> ListView:
        return self.query_one(f"#list-{self._panel_id}", ListView)

    @property
    def _filter_input(self) -> Input:
        return self.query_one(f"#filter-{self._panel_id}", Input)

    # ── Compose ───────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield ListView(id=f"list-{self._panel_id}")
        yield Input(placeholder="Filter: ", id=f"filter-{self._panel_id}")

    # ── Selected path ─────────────────────────────────────────────────────────

    @property
    def selected_path(self) -> Path | None:
        """Currently highlighted path (None when ".." is highlighted)."""
        idx = self._list.index
        if idx is None or idx == 0:
            return None
        file_idx = idx - 1
        if 0 <= file_idx < len(self._filtered):
            return self._filtered[file_idx]
        return None

    # ── Directory listing ─────────────────────────────────────────────────────

    def watch_current_path(self, path: Path) -> None:
        self._refresh_listing()

    def _refresh_listing(self) -> None:
        try:
            entries = sorted(
                self.current_path.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except PermissionError:
            entries = []
        self._entries = entries

        path_str = str(self.current_path)
        if len(path_str) > 30:
            path_str = "…" + path_str[-29:]
        self.border_title = f" {path_str} "

        self._apply_filter(self._filter)

    def _apply_filter(self, pattern: str) -> None:
        self._filter = pattern
        low = pattern.lower()
        self._filtered = [
            p for p in self._entries
            if not low or low in p.name.lower()
        ]
        self._repopulate()

    def _repopulate(self) -> None:
        self._highlighted_item = None   # stale ref; widgets about to be destroyed
        lst = self._list
        lst.clear()

        # ".." is always the first entry — Far Manager style
        lst.append(ListItem(Label("  ..", markup=False), classes="parent"))

        for p in self._filtered:
            if p.is_dir():
                label_text = f"  [{p.name}]"
                css_class = "dir"
            else:
                try:
                    stat = p.stat()
                    size = _fmt_size(stat.st_size)
                    date = _fmt_date(stat.st_mtime)
                except OSError:
                    size, date = "?", "?"
                label_text = f"  {p.name:<18} {size:>5} {date}"
                css_class = "file"
            lst.append(ListItem(Label(label_text, markup=False), classes=css_class))

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self._refresh_listing()

    # ── Navigation ────────────────────────────────────────────────────────────

    @on(ListView.Highlighted)
    def _on_item_highlighted(self, event: ListView.Highlighted) -> None:
        # Reset previous item to CSS-controlled colors
        prev = self._highlighted_item
        if prev is not None:
            try:
                prev.styles.background = "#0000aa"
                for lbl in prev.query("Label"):
                    lbl.styles.background = "#0000aa"
                    lbl.styles.color = "#ffffff"
            except Exception:
                pass

        self._highlighted_item = event.item

        # Apply cyan highlight inline (bypasses CSS specificity issues)
        if event.item is not None:
            event.item.styles.background = "#00ffff"
            for lbl in event.item.query("Label"):
                lbl.styles.background = "#00ffff"
                lbl.styles.color = "#000000"

    @on(ListView.Selected)
    def _on_selected(self, event: ListView.Selected) -> None:
        idx = self._list.index
        if idx is None:
            return
        if idx == 0:           # ".." entry
            self.action_go_up()
            return
        file_idx = idx - 1    # adjust for the ".." offset
        if 0 <= file_idx < len(self._filtered):
            p = self._filtered[file_idx]
            if p.is_dir():
                self.current_path = p
            else:
                self.app.open_file(p)

    def action_go_up(self) -> None:
        parent = self.current_path.parent
        if parent != self.current_path:
            self.current_path = parent

    # ── Filter ────────────────────────────────────────────────────────────────

    def action_search_file(self) -> None:
        inp = self._filter_input
        inp.add_class("visible")
        inp.focus()

    @on(Input.Changed)
    def _on_filter_changed(self, event: Input.Changed) -> None:
        self._apply_filter(event.value)

    @on(Input.Submitted)
    def _on_filter_submitted(self, _: Input.Submitted) -> None:
        self._filter_input.remove_class("visible")
        self._list.focus()

    # ── File operations ───────────────────────────────────────────────────────

    def action_mkdir(self) -> None:
        self.app.push_screen(
            InputDialog("New directory name:", ""),
            lambda name: self._do_mkdir(name) if name else None,
        )

    def _do_mkdir(self, name: str) -> None:
        target = self.current_path / name
        try:
            target.mkdir(parents=True, exist_ok=True)
            self._refresh_listing()
        except OSError as exc:
            self.notify(str(exc), severity="error")

    def action_rename_prompt(self) -> None:
        p = self.selected_path
        if p is None:
            return
        self.app.push_screen(
            InputDialog("Rename to:", p.name),
            lambda name: self._do_rename(p, name) if name else None,
        )

    def _do_rename(self, p: Path, new_name: str) -> None:
        dest = p.parent / new_name
        try:
            p.rename(dest)
            self._refresh_listing()
        except OSError as exc:
            self.notify(str(exc), severity="error")

    # Keep for external callers (main.py rename action)
    def rename_selected(self, new_name: str) -> None:
        p = self.selected_path
        if p and new_name:
            self._do_rename(p, new_name)

    def action_delete_entry(self) -> None:
        p = self.selected_path
        if p is None:
            return
        self.app.push_screen(
            ConfirmDialog(f'Delete "{p.name}"?'),
            lambda ok: self._do_delete(p) if ok else None,
        )

    def _do_delete(self, p: Path) -> None:
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            self._refresh_listing()
        except OSError as exc:
            self.notify(str(exc), severity="error")

    def action_copy_prompt(self) -> None:
        p = self.selected_path
        if p is None:
            return
        self.app.push_screen(
            InputDialog("Copy to (destination directory):", str(p.parent)),
            lambda dest: self._do_copy(p, Path(dest)) if dest else None,
        )

    def _do_copy(self, src: Path, dest_dir: Path) -> None:
        dest = dest_dir / src.name
        try:
            if src.is_dir():
                shutil.copytree(src, dest)
            else:
                shutil.copy2(src, dest)
            self._refresh_listing()
            self.notify(f"Copied → {dest_dir}", severity="information")
        except OSError as exc:
            self.notify(str(exc), severity="error")

    def action_move_prompt(self) -> None:
        p = self.selected_path
        if p is None:
            return
        self.app.push_screen(
            InputDialog("Move to (destination directory):", str(p.parent)),
            lambda dest: self._do_move(p, Path(dest)) if dest else None,
        )

    def _do_move(self, src: Path, dest_dir: Path) -> None:
        dest = dest_dir / src.name
        try:
            shutil.move(str(src), dest)
            self._refresh_listing()
            self.notify(f"Moved → {dest_dir}", severity="information")
        except OSError as exc:
            self.notify(str(exc), severity="error")


# ── Modal dialogs ─────────────────────────────────────────────────────────────

class InputDialog(ModalScreen[str | None]):
    """Single-line text input dialog."""

    def __init__(self, prompt: str, default: str = "") -> None:
        super().__init__()
        self._prompt = prompt
        self._default = default

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._prompt)
            yield Input(value=self._default, id="input-field")
            with Horizontal():
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#input-field", Input).focus()

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self.dismiss(self.query_one("#input-field", Input).value)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self) -> None:
        self._ok()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


class ConfirmDialog(ModalScreen[bool]):
    """Yes / No confirmation dialog."""

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._message)
            with Horizontal():
                yield Button("Yes", variant="error", id="yes")
                yield Button("No", variant="primary", id="no")

    @on(Button.Pressed, "#yes")
    def _yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def _no(self) -> None:
        self.dismiss(False)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(False)
