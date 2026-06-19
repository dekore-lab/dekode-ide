"""DekodeIDE — Far Manager style TUI code editor for GDScript."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Log, Static

from config import APP_TITLE, CSS
from debug_panel import DebugPanel
from editor import CodeEditor
from file_panel import ConfirmDialog, FilePanel, InputDialog


# ── Top header bar ────────────────────────────────────────────────────────────

class AppHeader(Static):
    """Dark-gray title bar with centered app name."""

    DEFAULT_CSS = """
    AppHeader {
        height: 1;
        background: #555555;
        color: white;
        content-align: center middle;
        text-style: bold;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(APP_TITLE, **kwargs)


# ── Bottom function key bar ───────────────────────────────────────────────────

_KEYBAR_KEYS = [
    ("1",  "Help"),
    ("2",  "Save"),
    ("3",  ""),
    ("4",  ""),
    ("5",  "Run"),
    ("6",  "Rename"),
    ("7",  "MkDir"),
    ("8",  "Stop"),
    ("9",  "Debug"),
    ("10", "Quit"),
]

class FarKeyBar(Static):
    """Far Manager–style function-key bar.

    Each key occupies 8 terminal columns:
      1-char (or 2-char for F10) number in black-on-cyan,
      rest of the 8 chars as white label on black.
    """

    DEFAULT_CSS = """
    FarKeyBar {
        height: 1;
        background: #000000;
        color: white;
    }
    """

    def render(self) -> Text:
        text = Text(no_wrap=True, overflow="crop")
        for num, label in _KEYBAR_KEYS:
            n = len(num)
            label_width = 8 - n          # each key block = 8 cols
            text.append(num, style="bold black on bright_cyan")
            text.append(f"{label:<{label_width}}", style="white on black")
        return text


# ── Inline search bar (inside editor container) ───────────────────────────────

class SearchBar(Horizontal):
    """Inline find/replace bar at the bottom of the editor pane."""

    DEFAULT_CSS = """
    SearchBar {
        height: 3;
        background: #000080;
        display: none;
        padding: 0 1;
    }
    SearchBar.visible { display: block; }
    SearchBar Label {
        color: #ffff00;
        width: auto;
        padding: 1 1 0 0;
    }
    SearchBar Input {
        width: 25;
        border: solid #00aaaa;
        background: #000000;
        color: white;
        height: 1;
        margin: 1 1 0 0;
    }
    SearchBar #search-info {
        color: #00ffff;
        width: auto;
        padding: 1 1 0 0;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("Find:")
        yield Input(placeholder="search…",      id="search-input")
        yield Label("Replace:")
        yield Input(placeholder="replacement…", id="replace-input")
        yield Static("", id="search-info")

    def focus_search(self) -> None:
        self.query_one("#search-input", Input).focus()

    def update_info(self, text: str) -> None:
        self.query_one("#search-info", Static).update(text)

    @property
    def replace_text(self) -> str:
        return self.query_one("#replace-input", Input).value


# ── Status bar ────────────────────────────────────────────────────────────────

class StatusBar(Static):
    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: #000000;
        color: #00aaaa;
        padding: 0 1;
    }
    """

    def set_info(
        self,
        file_path: Path | None,
        cursor: str,
        modified: bool,
        active_panel: str,
    ) -> None:
        name = str(file_path) if file_path else "No file open"
        mod  = " [*]" if modified else ""
        panel = f"[{active_panel.upper()}]"
        self.update(f"{panel}  {name}{mod}  │  {cursor}")


# ── Main App ──────────────────────────────────────────────────────────────────

class DekodeApp(App):
    """Far Manager style TUI IDE for GDScript."""

    TITLE = APP_TITLE
    CSS   = CSS

    BINDINGS = [
        Binding("f1",     "show_help",    "Help"),
        Binding("f2",     "save_file",    "Save"),
        Binding("f5",     "run_godot",    "Run"),
        Binding("f8",     "stop_godot",   "Stop"),
        Binding("f9",     "toggle_debug", "Debug"),
        Binding("f10",    "quit",         "Quit"),
        Binding("tab",    "switch_panel", "Switch", show=False),
        Binding("ctrl+q", "quit",         "Quit",   show=False),
        Binding("ctrl+f", "open_search",  "Find",   show=False),
        Binding("escape", "close_search", "Close",  show=False),
    ]

    _active_panel: str = "files"

    # ── Layout ────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield AppHeader(id="app-header")
        with Horizontal(id="main-area"):
            yield FilePanel(panel_id="left", id="left-panel")
            with Vertical(id="editor-container"):
                yield CodeEditor(id="editor")
                yield SearchBar(id="search-bar")
        yield DebugPanel(id="debug-panel")
        yield StatusBar("", id="status-bar")
        yield FarKeyBar(id="key-bar")

    def on_mount(self) -> None:
        # Set editor pane title
        self.query_one("#editor-container").border_title = " No file "
        self._focus_files()

    # ── Focus helpers ─────────────────────────────────────────────────────────

    def _focus_files(self) -> None:
        self._active_panel = "files"
        self.query_one("#left-panel", FilePanel).query_one("ListView").focus()
        self._refresh_status()

    def _focus_editor(self) -> None:
        self._active_panel = "editor"
        self.query_one("#editor", CodeEditor).focus()
        self._refresh_status()

    def action_switch_panel(self) -> None:
        if self._active_panel == "files":
            self._focus_editor()
        else:
            self._focus_files()

    # ── File open ─────────────────────────────────────────────────────────────

    def open_file(self, path: Path) -> None:
        editor = self.query_one("#editor", CodeEditor)
        if editor.is_modified:
            self.push_screen(
                ConfirmDialog(f'Unsaved changes. Open "{path.name}" anyway?'),
                lambda ok: self._do_open(path) if ok else None,
            )
        else:
            self._do_open(path)

    def _do_open(self, path: Path) -> None:
        editor = self.query_one("#editor", CodeEditor)
        if editor.load_file(path):
            container = self.query_one("#editor-container")
            container.border_title = f" {path.name} "
            self._focus_editor()
            self._refresh_status()

    # ── Global actions ────────────────────────────────────────────────────────

    def action_save_file(self) -> None:
        if self.query_one("#editor", CodeEditor).save_file():
            self._refresh_status()

    def action_run_godot(self) -> None:
        editor = self.query_one("#editor", CodeEditor)
        if editor.file_path is None:
            self.notify("No file open.", severity="warning")
            return
        if editor.is_modified:
            editor.save_file()
        debug = self.query_one("#debug-panel", DebugPanel)
        debug.add_class("visible")
        debug.run_file(editor.file_path)

    def action_stop_godot(self) -> None:
        self.query_one("#debug-panel", DebugPanel).stop()

    def action_toggle_debug(self) -> None:
        self.query_one("#debug-panel", DebugPanel).toggle_class("visible")

    # ── Search ────────────────────────────────────────────────────────────────

    def action_open_search(self) -> None:
        bar = self.query_one("#search-bar", SearchBar)
        bar.add_class("visible")
        bar.focus_search()

    def action_close_search(self) -> None:
        bar = self.query_one("#search-bar", SearchBar)
        bar.remove_class("visible")
        self._focus_editor()

    @on(Input.Submitted, "#search-input")
    def _on_search_submitted(self, event: Input.Submitted) -> None:
        editor = self.query_one("#editor", CodeEditor)
        bar    = self.query_one("#search-bar", SearchBar)
        count  = editor.search(event.value)
        bar.update_info(f"{count} match{'es' if count != 1 else ''}")

    @on(Input.Submitted, "#replace-input")
    def _on_replace_submitted(self, _: Input.Submitted) -> None:
        editor = self.query_one("#editor", CodeEditor)
        bar    = self.query_one("#search-bar", SearchBar)
        count  = editor.replace_all(bar.replace_text)
        bar.update_info(f"Replaced {count}")

    # ── Help ──────────────────────────────────────────────────────────────────

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

    # ── Status refresh ────────────────────────────────────────────────────────

    def _refresh_status(self) -> None:
        editor = self.query_one("#editor", CodeEditor)
        self.query_one("#status-bar", StatusBar).set_info(
            editor.file_path,
            editor.cursor_info,
            editor.is_modified,
            self._active_panel,
        )

    @on(CodeEditor.Changed)
    def _on_editor_changed(self, _: object) -> None:
        self._refresh_status()

    # ── Quit ──────────────────────────────────────────────────────────────────

    def action_quit(self) -> None:
        editor = self.query_one("#editor", CodeEditor)
        if editor.is_modified:
            self.push_screen(
                ConfirmDialog("Unsaved changes. Quit anyway?"),
                lambda ok: self.exit() if ok else None,
            )
        else:
            self.exit()


# ── Help screen ───────────────────────────────────────────────────────────────

HELP_TEXT = """\
  dekode — Keyboard Reference
  ══════════════════════════════════════════════

  GLOBAL
    F1           This help screen
    F2           Save current file
    F5           Run file in Godot (headless)
    F8           Stop Godot process
    F9           Toggle debug panel
    F10 / Ctrl+Q Quit
    Tab          Switch focus: Files ↔ Editor
    Ctrl+F       Open search / replace bar
    Escape       Close search bar

  FILE PANEL
    ↑ / ↓        Navigate
    Enter        Open dir or file  (".."/Backspace = up)
    Backspace    Go to parent directory
    F6           Rename selected entry
    F7           Create new directory
    Delete       Delete selected entry
    Ctrl+C       Copy to path (dialog)
    Ctrl+M       Move to path (dialog)
    Ctrl+F       Filter by name

  EDITOR
    Ctrl+Z / Ctrl+Y  Undo / Redo
    F2               Save
    Ctrl+F           Find / Replace
    Enter in Find    Execute search

  ══════════════════════════════════════════════
  Press Escape or F1 to close.
"""


class HelpScreen(Screen):
    BINDINGS = [
        Binding("escape", "close", show=False),
        Binding("f1",     "close", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Static(HELP_TEXT)
            yield Button("Close", id="help-close")

    @on(Button.Pressed, "#help-close")
    def _close(self) -> None:
        self.dismiss()

    def action_close(self) -> None:
        self.dismiss()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    DekodeApp().run()
