"""DekodeIDE — Far Manager style TUI code editor for GDScript."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
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


@dataclass
class TabState:
    path:          Path
    text:          str            = ""
    is_modified:   bool           = False
    cursor:        tuple[int,int] = field(default_factory=lambda: (0, 0))
    file_info:     object         = None
    extends_class: str            = ""


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
        text         = Text(no_wrap=True, overflow="crop", end="")
        total_w      = self.size.width or 80
        n_keys       = len(_KEYBAR_KEYS)
        slot_w       = total_w // n_keys
        extra        = total_w - slot_w * n_keys
        panel        = getattr(self.app, "_active_panel", "files")
        for i, (num, label) in enumerate(_KEYBAR_KEYS):
            if num == "4":
                label = "New" if panel == "files" else "Files"
            w       = slot_w + (1 if i < extra else 0)
            num_len = len(num)
            lbl_w   = max(1, w - num_len)
            text.append(num,               style="bold cyan on black")
            text.append(f"{label:<{lbl_w}}", style="white on black")
        return text

    def on_resize(self, _) -> None:
        self.refresh()


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


# ── Tab bar ───────────────────────────────────────────────────────────────────

class TabBar(Static):
    DEFAULT_CSS = """
    TabBar {
        height: 1;
        background: #000000;
        padding: 0;
    }
    """

    def _tab_slot(self, state: TabState) -> tuple[str, int]:
        name    = ("*" if state.is_modified else "") + state.path.name
        content = f" {name} "
        return content, len(content) + 1   # +1 for trailing │

    def _count_rows(self, tabs: list, width: int) -> int:
        if not tabs:
            return 1
        pos = rows = 1
        for state in tabs:
            _, tw = self._tab_slot(state)
            if pos + tw > width and pos > 1:
                rows += 1
                pos   = 1
            pos += tw
        return rows

    def refresh_tabs(self) -> None:
        tabs  = getattr(self.app, "_tabs", [])
        width = self.size.width or 80
        self.styles.height = max(1, self._count_rows(tabs, width))
        self.refresh()

    def render(self) -> Text:
        app    = self.app
        tabs   = getattr(app, "_tabs",       [])
        active = getattr(app, "_active_tab", 0)
        width  = self.size.width or 80

        if not tabs:
            return Text(" " * width, style="on #000000", end="")

        result = Text(no_wrap=True, overflow="crop", end="")
        row    = Text(end="")
        row.append("│", style="#555555 on #000000")
        pos    = 1

        for i, state in enumerate(tabs):
            content, tw = self._tab_slot(state)
            if pos + tw > width and pos > 1:
                row.append(" " * max(0, width - pos), style="on #000000")
                result.append_text(row)
                result.append("\n")
                row = Text(end="")
                row.append("│", style="#555555 on #000000")
                pos = 1
            if i == active:
                row.append(content, style="bold #000000 on #FFFF00")
            elif state.is_modified:
                row.append(content, style="#FFFF00 on #000044")
            else:
                row.append(content, style="white on #000044")
            row.append("│", style="#555555 on #000000")
            pos += tw

        row.append(" " * max(0, width - pos), style="on #000000")
        result.append_text(row)
        return result

    def on_resize(self, _) -> None:
        self.refresh_tabs()

    def on_click(self, event) -> None:
        app  = self.app
        tabs = getattr(app, "_tabs", [])
        if not tabs:
            return
        width      = self.size.width or 80
        cx, cy     = event.x, event.y
        pos        = 1
        cur_row    = 0
        for i, state in enumerate(tabs):
            _, tw = self._tab_slot(state)
            if pos + tw > width and pos > 1:
                cur_row += 1
                pos      = 1
            if cur_row == cy and pos <= cx < pos + tw:
                app._switch_tab(i)
                return
            pos += tw


# ── Status bar ────────────────────────────────────────────────────────────────

class StatusBar(Static):
    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: #000000;
        padding: 0;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._file_path:   Path | None = None
        self._cursor:      str  = "Ln 1, Col 1"
        self._modified:    bool = False
        self._api_version: str  = ""

    def set_info(
        self,
        file_path: Path | None,
        cursor: str,
        modified: bool,
        active_panel: str,
        api_version: str = "",
    ) -> None:
        self._file_path   = file_path
        self._cursor      = cursor
        self._modified    = modified
        self._api_version = api_version
        self.refresh()

    def render(self) -> Text:
        width = self.size.width or 80

        # ── Left zone ─────────────────────────────────────────
        left = Text(no_wrap=True, overflow="crop", end="")
        if self._file_path:
            left.append(str(self._file_path), style="#FFFFFF on #000000")
        else:
            left.append("No file open", style="#FFFFFF on #000000")
        if self._modified:
            left.append(" [*]", style="bold #FFFF00 on #000000")
        left.append("  ", style="on #000000")
        left.append("│", style="#444444 on #000000")
        left.append("  ", style="on #000000")
        left.append(self._cursor, style="#00FFFF on #000000")

        # ── Right zone ────────────────────────────────────────
        right = Text(no_wrap=True, overflow="crop", end="")
        if self._api_version:
            right.append(f"GDScript v{self._api_version}", style="#00FFFF on #000000")
            right.append(" (Ctrl+G: change version)", style="#888888 on #000000")
        else:
            right.append("Ctrl+G: install GDScript", style="#888888 on #000000")
        gap = max(0, width - len(left.plain) - len(right.plain))

        result = Text(no_wrap=True, overflow="crop", end="")
        result.append_text(left)
        result.append(" " * gap, style="on #000000")
        result.append_text(right)
        return result

    def on_resize(self, _) -> None:
        self.refresh()


# ── Main App ──────────────────────────────────────────────────────────────────

class DekodeApp(App):
    """Far Manager style TUI IDE for GDScript."""

    TITLE = APP_TITLE
    CSS   = CSS

    BINDINGS = [
        Binding("f1",     "show_help",        "Help",       show=False),
        Binding("f2",     "save_file",         "Save",       show=False),
        Binding("f3",     "toggle_fullscreen", "Fullscreen", show=False),
        Binding("f4",     "context_f4",                      show=False),
        Binding("f5",     "run_godot",         "Run",        show=False),
        Binding("f8",     "stop_godot",        "Stop",       show=False),
        Binding("f9",     "toggle_debug",      "Debug",      show=False),
        Binding("f10",    "quit",              "Quit",       show=False),
        Binding("ctrl+q", "quit",              "Quit",       show=False),
        Binding("ctrl+f", "open_search",       "Find",       show=False),
        Binding("ctrl+g", "switch_api",        "API",        show=False),
        Binding("escape", "close_search",                    show=False),
        Binding("alt+right", "tab_next",  show=False, priority=True),
        Binding("alt+left",  "tab_prev",  show=False, priority=True),
        Binding("alt+w",     "tab_close", show=False, priority=True),
        Binding("alt+1",  "tab_go(1)",   show=False, priority=True),
        Binding("alt+2",  "tab_go(2)",   show=False, priority=True),
        Binding("alt+3",  "tab_go(3)",   show=False, priority=True),
        Binding("alt+4",  "tab_go(4)",   show=False, priority=True),
        Binding("alt+5",  "tab_go(5)",   show=False, priority=True),
        Binding("alt+6",  "tab_go(6)",   show=False, priority=True),
        Binding("alt+7",  "tab_go(7)",   show=False, priority=True),
        Binding("alt+8",  "tab_go(8)",   show=False, priority=True),
        Binding("alt+9",  "tab_go(9)",   show=False, priority=True),
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
                yield TabBar(id="tab-bar")
                yield CodeEditor(id="editor")
                yield SearchBar(id="search-bar")
        yield DebugPanel(id="debug-panel")
        yield StatusBar("", id="status-bar")
        yield FarKeyBar(id="key-bar")

    def on_mount(self) -> None:
        self._tabs:       list[TabState] = []
        self._active_tab: int            = 0
        self._focus_files()
        self._load_api_version()
        self._load_api_data()
        self.call_after_refresh(self._check_first_launch)
        from gdscript_parser import get_project_index
        from file_panel import FilePanel
        get_project_index().start_indexing(
            self.query_one("#left-panel", FilePanel).current_path
        )
        self.call_after_refresh(self._restore_tabs)

    # ── Focus helpers ─────────────────────────────────────────────────────────

    def _focus_files(self) -> None:
        self._active_panel = "files"
        self.query_one("#left-panel", FilePanel).query_one("ListView").focus()
        self._refresh_status()
        self._refresh_keybar()

    def _focus_editor(self) -> None:
        self._active_panel = "editor"
        self.query_one("#editor", CodeEditor).focus()
        self._refresh_status()
        self._refresh_keybar()

    def _refresh_keybar(self) -> None:
        try:
            self.query_one("#key-bar", FarKeyBar).refresh()
        except Exception:
            pass

    def action_context_f4(self) -> None:
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
        for i, tab in enumerate(self._tabs):
            if tab.path == path:
                self._switch_tab(i)
                return
        self._open_new_tab(path)

    def _open_new_tab(self, path: Path, switch: bool = True) -> bool:
        editor = self.query_one("#editor", CodeEditor)
        if switch:
            self._save_current_tab_state()
        if not editor.load_file(path):
            return False
        state = TabState(
            path          = path,
            text          = editor.text,
            is_modified   = False,
            cursor        = (0, 0),
            file_info     = editor._file_info,
            extends_class = editor._extends_class,
        )
        self._tabs.append(state)
        if switch:
            self._active_tab = len(self._tabs) - 1
            self._focus_editor()
            self._refresh_status()
            self._save_tab_config()
            self._refresh_tab_bar()
        return True

    # ── Global actions ────────────────────────────────────────────────────────

    def action_save_file(self) -> None:
        editor = self.query_one("#editor", CodeEditor)
        if editor.save_file():
            if self._tabs and 0 <= self._active_tab < len(self._tabs):
                self._tabs[self._active_tab].is_modified = False
            self._refresh_status()
            self._refresh_tab_bar()

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
        if bar.has_class("visible"):
            bar.remove_class("visible")
            self._focus_editor()
        elif self._active_panel == "editor":
            self._focus_files()
        # if already in files panel: do nothing

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
        editor    = self.query_one("#editor", CodeEditor)
        container = self.query_one("#editor-container")
        if editor.file_path:
            name = editor.file_path.name
            if editor.is_modified:
                container.border_title = f" [bold #FFFF00]*{name}[/] "
            else:
                container.border_title = f" {name} "
        else:
            container.border_title = " No file "
        self.query_one("#status-bar", StatusBar).set_info(
            editor.file_path,
            editor.cursor_info,
            editor.is_modified,
            self._active_panel,
            self._godot_api_ver,
        )

    @on(CodeEditor.Changed)
    def _on_editor_changed(self, _: object) -> None:
        editor = self.query_one("#editor", CodeEditor)
        if self._tabs and 0 <= self._active_tab < len(self._tabs):
            self._tabs[self._active_tab].is_modified = editor.is_modified
        self._refresh_status()
        self._refresh_tab_bar()

    # ── Quit ──────────────────────────────────────────────────────────────────

    def action_quit(self) -> None:
        self._save_current_tab_state()
        modified = [t for t in self._tabs if t.is_modified]
        if modified:
            names = ", ".join(t.path.name for t in modified)
            self.push_screen(
                ConfirmDialog(f"Unsaved changes in: {names}. Quit anyway?"),
                lambda ok: self.exit() if ok else None,
            )
        else:
            self.exit()

    # ── Tab management ────────────────────────────────────────────────────────

    def _save_current_tab_state(self) -> None:
        if not self._tabs or not (0 <= self._active_tab < len(self._tabs)):
            return
        editor = self.query_one("#editor", CodeEditor)
        state  = self._tabs[self._active_tab]
        state.text        = editor.text
        state.is_modified = editor.is_modified
        state.cursor      = editor.cursor_location

    def _switch_tab(self, index: int) -> None:
        if not (0 <= index < len(self._tabs)) or index == self._active_tab:
            return
        self._save_current_tab_state()
        self._active_tab = index
        state  = self._tabs[index]
        editor = self.query_one("#editor", CodeEditor)
        editor.restore_state(
            path=state.path, text=state.text,
            file_info=state.file_info, extends_class=state.extends_class,
            is_modified=state.is_modified, cursor=state.cursor,
        )
        self._focus_editor()
        self._refresh_status()
        self._save_tab_config()
        self._refresh_tab_bar()

    def _close_tab(self, index: int) -> None:
        if not (0 <= index < len(self._tabs)):
            return
        if index == self._active_tab:
            self._save_current_tab_state()
        state = self._tabs[index]
        if state.is_modified:
            self.push_screen(
                CloseTabDialog(state.path.name),
                lambda result, idx=index: self._on_close_tab_result(result, idx),
            )
        else:
            self._do_close_tab(index)

    def _on_close_tab_result(self, result: str | None, index: int) -> None:
        if result == "save":
            editor = self.query_one("#editor", CodeEditor)
            if self._active_tab != index:
                self._save_current_tab_state()
                self._active_tab = index
                s = self._tabs[index]
                editor.restore_state(
                    path=s.path, text=s.text,
                    file_info=s.file_info, extends_class=s.extends_class,
                    is_modified=s.is_modified, cursor=s.cursor,
                )
            editor.save_file()
            self._tabs[index].is_modified = False
            self._do_close_tab(index)
        elif result == "dont_save":
            self._do_close_tab(index)

    def _do_close_tab(self, index: int) -> None:
        self._tabs.pop(index)
        editor = self.query_one("#editor", CodeEditor)
        if not self._tabs:
            self._active_tab = 0
            editor.clear_file()
            self._focus_files()
        else:
            new_idx = min(index, len(self._tabs) - 1)
            self._active_tab = new_idx
            s = self._tabs[new_idx]
            editor.restore_state(
                path=s.path, text=s.text,
                file_info=s.file_info, extends_class=s.extends_class,
                is_modified=s.is_modified, cursor=s.cursor,
            )
            self._focus_editor()
        self._refresh_status()
        self._save_tab_config()
        self._refresh_tab_bar()

    def _refresh_tab_bar(self) -> None:
        try:
            self.query_one("#tab-bar", TabBar).refresh_tabs()
        except Exception:
            pass

    def _save_tab_config(self) -> None:
        try:
            try:
                cfg = json.loads(_CFG.read_text(encoding="utf-8"))
            except Exception:
                cfg = {}
            cfg["tabs"]       = [str(t.path) for t in self._tabs]
            cfg["active_tab"] = self._active_tab
            _CFG.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def _restore_tabs(self) -> None:
        try:
            cfg          = json.loads(_CFG.read_text(encoding="utf-8"))
            raw_tabs     = cfg.get("tabs", [])
            saved_active = int(cfg.get("active_tab", 0))
        except Exception:
            return
        valid = [Path(p) for p in raw_tabs if Path(p).exists()]
        if not valid:
            return
        for path in valid:
            self._open_new_tab(path, switch=False)
        if self._tabs:
            idx   = min(saved_active, len(self._tabs) - 1)
            self._active_tab = idx
            s     = self._tabs[idx]
            editor = self.query_one("#editor", CodeEditor)
            editor.restore_state(
                path=s.path, text=s.text,
                file_info=s.file_info, extends_class=s.extends_class,
                is_modified=s.is_modified, cursor=s.cursor,
            )
            self._focus_editor()
            self._refresh_status()
            self._refresh_tab_bar()

    def action_tab_next(self) -> None:
        if self._tabs:
            self._switch_tab((self._active_tab + 1) % len(self._tabs))

    def action_tab_prev(self) -> None:
        if self._tabs:
            self._switch_tab((self._active_tab - 1) % len(self._tabs))

    def action_tab_close(self) -> None:
        self._close_tab(self._active_tab)

    def action_tab_go(self, index: str) -> None:
        self._switch_tab(int(index) - 1)

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


# ── Close tab dialog ─────────────────────────────────────────────────────────

class CloseTabDialog(ModalScreen[str | None]):
    DEFAULT_CSS = """
    CloseTabDialog { align: center middle; }
    CloseTabDialog #ctd-box {
        background: #000080; border: solid #00ffff;
        padding: 1 2; width: 52; height: auto;
    }
    CloseTabDialog #ctd-msg { color: white; padding: 0 0 1 0; }
    CloseTabDialog #ctd-buttons { height: auto; margin-top: 1; }
    CloseTabDialog Button {
        background: #00aaaa; color: #000000;
        border: none; margin-right: 1; min-width: 14;
    }
    CloseTabDialog Button:hover, CloseTabDialog Button.-active {
        background: white; color: #000000;
    }
    """
    BINDINGS = [
        Binding("s",      "save",      show=False),
        Binding("d",      "dont_save", show=False),
        Binding("c",      "cancel",    show=False),
        Binding("escape", "cancel",    show=False),
    ]

    def __init__(self, filename: str) -> None:
        super().__init__()
        self._filename = filename

    def compose(self) -> ComposeResult:
        with Vertical(id="ctd-box"):
            yield Static(f"Unsaved changes in {self._filename}", id="ctd-msg")
            with Horizontal(id="ctd-buttons"):
                yield Button("[S] Save",       id="ctd-save")
                yield Button("[D] Don't Save", id="ctd-dont")
                yield Button("[C] Cancel",     id="ctd-cancel")

    def on_mount(self) -> None:
        self.query_one("#ctd-save", Button).focus()

    def on_key(self, event) -> None:
        if event.key in ("left", "right"):
            btns = list(self.query(Button))
            foc  = self.focused
            idx  = btns.index(foc) if foc in btns else 0
            btns[(idx + (1 if event.key == "right" else -1)) % len(btns)].focus()
            event.stop()

    def action_save(self)      -> None: self.dismiss("save")
    def action_dont_save(self) -> None: self.dismiss("dont_save")
    def action_cancel(self)    -> None: self.dismiss(None)

    @on(Button.Pressed, "#ctd-save")
    def _on_save(self)   -> None: self.dismiss("save")
    @on(Button.Pressed, "#ctd-dont")
    def _on_dont(self)   -> None: self.dismiss("dont_save")
    @on(Button.Pressed, "#ctd-cancel")
    def _on_cancel(self) -> None: self.dismiss(None)


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
