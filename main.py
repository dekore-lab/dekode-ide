"""DekodeIDE — Far Manager style TUI code editor for GDScript."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Input, Label, ListItem, ListView, Log, Static
from textual.worker import Worker, WorkerState

from config import APP_TITLE, CSS
from debug_panel import DebugPanel
from editor import CodeEditor
from file_panel import ConfirmDialog, FilePanel, InputDialog

_CFG = Path(__file__).parent / "config.json"


def _fetch_versions_blocking() -> list[str]:
    import io, sys
    _out, sys.stdout = sys.stdout, io.StringIO()
    try:
        from fetch_godot_api import fetch_stable_versions
        return fetch_stable_versions()
    except (SystemExit, Exception):
        return []
    finally:
        sys.stdout = _out


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
    ("3",  "Full"),
    ("4",  "New"),
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
        api_version: str = "",
    ) -> None:
        name  = str(file_path) if file_path else "No file open"
        mod   = " [*]" if modified else ""
        panel = f"[{active_panel.upper()}]"
        ver   = f"  │  GDScript {api_version}" if api_version else ""
        self.update(f"{panel}  {name}{mod}  │  {cursor}{ver}")


# ── Main App ──────────────────────────────────────────────────────────────────

class DekodeApp(App):
    """Far Manager style TUI IDE for GDScript."""

    TITLE = APP_TITLE
    CSS   = CSS

    BINDINGS = [
        Binding("f1",     "show_help",         "Help",       show=False),
        Binding("f2",     "save_file",          "Save",       show=False),
        Binding("f3",     "toggle_fullscreen",  "Fullscreen", show=False),
        Binding("f5",     "run_godot",          "Run",        show=False),
        Binding("f8",     "stop_godot",         "Stop",       show=False),
        Binding("f9",     "toggle_debug",       "Debug",      show=False),
        Binding("f10",    "quit",               "Quit",       show=False),
        Binding("tab",    "switch_panel",       "Switch",     show=False),
        Binding("ctrl+q", "quit",               "Quit",       show=False),
        Binding("ctrl+f", "open_search",        "Find",       show=False),
        Binding("ctrl+g", "switch_api",         "API",        show=False),
        Binding("escape", "close_search",       "Close",      show=False),
    ]

    _active_panel:  str  = "files"
    _fullscreen:    bool = False
    _godot_api_ver: str  = ""

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
        self.query_one("#editor-container").border_title = " No file "
        self._focus_files()
        self._load_api_version()
        self._load_api_data()
        self.call_after_refresh(self._check_first_launch)
        from gdscript_parser import get_project_index
        from file_panel import FilePanel
        get_project_index().start_indexing(
            self.query_one("#left-panel", FilePanel).current_path
        )

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

    def action_toggle_fullscreen(self) -> None:
        left = self.query_one("#left-panel", FilePanel)
        editor_cont = self.query_one("#editor-container")

        if not self._fullscreen:
            if self._active_panel == "files":
                editor_cont.display = False
                left.styles.width = "1fr"
            else:
                left.display = False
            self._fullscreen = True
        else:
            left.display = True
            left.styles.width = 35
            editor_cont.display = True
            self._fullscreen = False

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
            self._godot_api_ver,
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

    # ── Godot API version management ──────────────────────────────────────────

    def _load_api_version(self) -> None:
        """Read saved api version from config.json; fall back to any cached file."""
        ver = ""
        try:
            cfg = json.loads(_CFG.read_text(encoding="utf-8"))
            ver = cfg.get("godot_api_version", "")
        except Exception:
            pass
        # Validate: JSON must exist for the saved version
        if ver and not (_CFG.parent / f"godot_api_{ver}.json").exists():
            ver = ""
        # Auto-detect any cached version
        if not ver:
            for f in sorted(_CFG.parent.glob("godot_api_*.json"), reverse=True):
                stem = f.stem  # "godot_api_4.2"
                if stem.startswith("godot_api_"):
                    ver = stem[len("godot_api_"):]
                    break
        self._godot_api_ver = ver
        self._refresh_status()

    def _set_api_version(self, version: str) -> None:
        self._godot_api_ver = version
        try:
            try:
                cfg = json.loads(_CFG.read_text(encoding="utf-8"))
            except Exception:
                cfg = {}
            cfg["godot_api_version"] = version
            _CFG.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        self._refresh_status()
        self._load_api_data()

    def _load_api_data(self) -> None:
        """Load godot_api_<version>.json and push class data to the editor."""
        version = self._godot_api_ver
        if not version:
            return
        path = _CFG.parent / f"godot_api_{version}.json"
        if not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.query_one("#editor", CodeEditor).set_api_data(
                payload.get("classes", {})
            )
        except Exception:
            pass

    def _check_first_launch(self) -> None:
        if not self._godot_api_ver:
            self.push_screen(
                ConfirmDialog("Godot documentation not found. Download now?"),
                self._on_first_launch_answer,
            )

    def _on_first_launch_answer(self, ok: bool) -> None:
        if ok:
            self.action_switch_api()

    def action_switch_api(self) -> None:
        self.notify("Fetching Godot version list...")
        self.run_worker(_fetch_versions_blocking, thread=True, name="godot_versions", exclusive=True)

    def _on_version_picked(self, tag: str | None) -> None:
        if not tag:
            return
        version    = tag.replace("-stable", "")
        local_path = _CFG.parent / f"godot_api_{version}.json"
        if local_path.exists():
            self._set_api_version(version)
            self.notify(f"Switched to GDScript {version}.", severity="information")
        else:
            self.push_screen(DownloadProgressScreen(tag, version), self._on_download_done)

    def _on_download_done(self, version: str | None) -> None:
        if version:
            self._set_api_version(version)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name == "godot_versions":
            if event.state == WorkerState.SUCCESS:
                versions: list[str] = event.worker.result or []
                if versions:
                    self.push_screen(GodotVersionDialog(versions), self._on_version_picked)
                else:
                    self.notify("No Godot versions found. Check connection.", severity="warning")
            elif event.state == WorkerState.ERROR:
                self.notify("Failed to fetch version list.", severity="error")


# ── Download progress modal ───────────────────────────────────────────────────

class DownloadProgressScreen(ModalScreen[str | None]):
    """Blocking download modal with live progress. Dismisses with version or None."""

    DEFAULT_CSS = """
    DownloadProgressScreen { align: center middle; }
    DownloadProgressScreen #dl-box {
        background: #000080;
        border: solid #00ffff;
        padding: 1 2;
        width: 56;
        height: auto;
    }
    DownloadProgressScreen #dl-title {
        color: #00ffff;
        text-style: bold;
        width: 100%;
        text-align: center;
        padding-bottom: 1;
    }
    DownloadProgressScreen #dl-action { color: white; }
    DownloadProgressScreen #dl-bar    { color: #00ffff; margin-top: 1; }
    DownloadProgressScreen #dl-file   { color: #888888; }
    DownloadProgressScreen #dl-result { color: white; margin-top: 1; }
    DownloadProgressScreen #dl-buttons {
        display: none;
        margin-top: 1;
        height: auto;
    }
    DownloadProgressScreen #dl-buttons Button {
        background: #00aaaa;
        color: #000000;
        border: none;
        margin-right: 1;
        min-width: 14;
    }
    DownloadProgressScreen #dl-buttons Button:hover,
    DownloadProgressScreen #dl-buttons Button.-active {
        background: white;
        color: #000000;
    }
    """

    def __init__(self, tag: str, version: str) -> None:
        super().__init__()
        self._tag           = tag
        self._version       = version
        self._state         = "downloading"   # "downloading" | "success" | "error"
        self._partial_data: dict = {}
        self._partial_count = 0
        self._total_count   = 0
        self._thread: threading.Thread | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="dl-box"):
            yield Static("Downloading Godot Documentation", id="dl-title")
            yield Static("", id="dl-action")
            yield Static("", id="dl-bar")
            yield Static("", id="dl-file")
            yield Static("", id="dl-result")
            with Horizontal(id="dl-buttons"):
                yield Button("Retry",            id="dl-retry")
                yield Button("Use partial data", id="dl-partial")
                yield Button("Close",            id="dl-close")

    def on_mount(self) -> None:
        self._start_thread()

    def _start_thread(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        import json as _json
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from datetime import datetime as _dt, timezone as _tz
        from fetch_godot_api import (
            _get, _API_BASE, _RAW_BASE, _OUTPUT_DIR, parse_class_xml,
        )

        try:
            self.app.call_from_thread(self._set_action, "Fetching class list...")
            raw      = _get(f"{_API_BASE}/contents/doc/classes?ref={self._tag}")
            entries  = _json.loads(raw.decode())
            xml_files = [e["name"] for e in entries if e["name"].endswith(".xml")]
            total     = len(xml_files)
            self._total_count = total

            self.app.call_from_thread(self._set_action, "Downloading classes...")
            classes: dict = {}
            done_count = 0

            def _fetch_one(fname: str) -> tuple[str, dict | None]:
                url = f"{_RAW_BASE}/{self._tag}/doc/classes/{fname}"
                try:
                    return fname, parse_class_xml(_get(url))
                except Exception:
                    return fname, None

            with ThreadPoolExecutor(max_workers=16) as executor:
                futures = {executor.submit(_fetch_one, f): f for f in xml_files}
                for future in as_completed(futures):
                    fname, parsed = future.result()
                    if parsed:
                        classes[fname[:-4]] = parsed
                    done_count += 1
                    self._partial_data  = classes
                    self._partial_count = len(classes)
                    self.app.call_from_thread(self._set_progress, done_count, total, fname)

            self.app.call_from_thread(self._set_action, "Saving...")
            out_path = _OUTPUT_DIR / f"godot_api_{self._version}.json"
            out_path.write_text(
                _json.dumps(
                    {
                        "version":      self._version,
                        "generated_at": _dt.now(_tz.utc).isoformat(),
                        "classes":      classes,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"Saved to: {out_path}", flush=True)
            self.app.call_from_thread(self._on_success, len(classes), total)

        except Exception as exc:
            self.app.call_from_thread(self._on_error, str(exc))

    def _set_action(self, msg: str) -> None:
        self.query_one("#dl-action", Static).update(Text(msg))

    def _set_progress(self, done: int, total: int, fname: str) -> None:
        pct    = done / total if total else 0
        filled = int(20 * pct)
        bar    = f"[{'█' * filled}{'░' * (20 - filled)}] {int(pct * 100)}% ({done}/{total} classes)"
        self.query_one("#dl-bar",  Static).update(Text(bar))
        self.query_one("#dl-file", Static).update(Text(fname))

    def _on_success(self, success: int, total: int) -> None:
        self._state = "success"
        for wid_id in ("#dl-action", "#dl-bar", "#dl-file"):
            self.query_one(wid_id, Static).update(Text(""))
        msg = Text()
        msg.append(f"Download complete: {success} classes loaded.", style="green")
        msg.append("\nPress any key to continue.")
        self.query_one("#dl-result", Static).update(msg)
        self.focus()

    def _on_error(self, error_msg: str) -> None:
        self._state = "error"
        msg = Text()
        msg.append("Error: ", style="bold red")
        msg.append(error_msg)
        self.query_one("#dl-result", Static).update(msg)
        partial = self._partial_count
        if partial > 0:
            self.query_one("#dl-partial", Button).label = f"Use partial ({partial} classes)"
        else:
            self.query_one("#dl-partial", Button).display = False
        self.query_one("#dl-buttons").display = True

    def on_key(self, event) -> None:
        if self._state == "downloading":
            event.prevent_default()
            event.stop()
        elif self._state == "success":
            self.dismiss(self._version)

    @on(Button.Pressed, "#dl-retry")
    def _do_retry(self) -> None:
        if self._state != "error":
            return
        self._state = "downloading"
        self.query_one("#dl-buttons").display = False
        for wid_id in ("#dl-result", "#dl-bar", "#dl-file"):
            self.query_one(wid_id, Static).update(Text(""))
        self.query_one("#dl-partial", Button).display = True
        self._partial_data  = {}
        self._partial_count = 0
        self._total_count   = 0
        self._start_thread()

    @on(Button.Pressed, "#dl-partial")
    def _do_use_partial(self) -> None:
        if self._state != "error" or not self._partial_data:
            return
        import json as _json
        from datetime import datetime as _dt, timezone as _tz
        from fetch_godot_api import _OUTPUT_DIR
        out_path = _OUTPUT_DIR / f"godot_api_{self._version}.json"
        out_path.write_text(
            _json.dumps(
                {
                    "version":      self._version,
                    "generated_at": _dt.now(_tz.utc).isoformat(),
                    "classes":      self._partial_data,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Saved to: {out_path}", flush=True)
        self.dismiss(self._version)

    @on(Button.Pressed, "#dl-close")
    def _do_close(self) -> None:
        if self._state != "error":
            return
        self.dismiss(None)


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


# ── Godot version picker dialog ───────────────────────────────────────────────

class GodotVersionDialog(ModalScreen[str | None]):
    """Keyboard-navigable list of available Godot 4.x stable versions."""

    DEFAULT_CSS = """
    GodotVersionDialog { align: center middle; }
    GodotVersionDialog #gvd-box {
        background: #000080;
        border: solid #00ffff;
        padding: 1 2;
        width: 38;
        height: auto;
    }
    GodotVersionDialog #gvd-box Label {
        color: white;
        padding: 0;
    }
    GodotVersionDialog #gvd-list {
        height: 10;
        background: #000080;
        border: solid #00aaaa;
        margin-top: 1;
    }
    GodotVersionDialog #gvd-list > ListItem {
        background: #000080; color: white; height: 1; padding: 0;
    }
    GodotVersionDialog #gvd-list > ListItem > Label {
        height: 1; padding: 0; color: white;
    }
    GodotVersionDialog #gvd-list > ListItem.-highlight {
        background: #00ffff; color: #000000;
    }
    GodotVersionDialog #gvd-list > ListItem.-highlight > Label {
        background: #00ffff; color: #000000;
    }
    """

    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(self, versions: list[str]) -> None:
        super().__init__()
        self._versions = versions

    def compose(self) -> ComposeResult:
        with Vertical(id="gvd-box"):
            yield Label("Select Godot API version:")
            yield ListView(id="gvd-list")

    def on_mount(self) -> None:
        local_dir = _CFG.parent
        lst       = self.query_one("#gvd-list", ListView)
        for tag in self._versions:
            ver    = tag.replace("-stable", "")
            cached = (local_dir / f"godot_api_{ver}.json").exists()
            mark   = " [cached]" if cached else ""
            lst.append(ListItem(Label(f"  {ver}{mark}", markup=False)))
        lst.focus()

    @on(ListView.Selected, "#gvd-list")
    def _on_selected(self, _: ListView.Selected) -> None:
        self._confirm()

    def on_key(self, event) -> None:
        lst = self.query_one("#gvd-list", ListView)
        if event.key == "up":
            lst.action_cursor_up()
            event.stop()
        elif event.key == "down":
            lst.action_cursor_down()
            event.stop()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _confirm(self) -> None:
        lst = self.query_one("#gvd-list", ListView)
        idx = lst.index
        if idx is not None and 0 <= idx < len(self._versions):
            self.dismiss(self._versions[idx])
        else:
            self.dismiss(None)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    DekodeApp().run()
