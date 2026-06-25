"""Far Manager–style file panel widget."""

from __future__ import annotations

import json
import platform
import shutil
from datetime import datetime
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent / "config.json"


def _load_last_dir() -> Path:
    try:
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        p = Path(data["last_directory"])
        if p.is_dir():
            return p
    except Exception:
        pass
    return Path.home()


def _save_last_dir(path: Path) -> None:
    try:
        try:
            cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
        cfg["last_directory"] = str(path)
        _CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass

_WIN_HIDDEN = 0x2   # FILE_ATTRIBUTE_HIDDEN
_WIN_SYSTEM = 0x4   # FILE_ATTRIBUTE_SYSTEM


def _is_hidden_system(path: Path) -> bool:
    """True when path carries both HIDDEN and SYSTEM Windows attributes."""
    if platform.system() != "Windows":
        return False
    try:
        attrs = path.stat().st_file_attributes
        return bool(attrs & _WIN_HIDDEN) and bool(attrs & _WIN_SYSTEM)
    except (OSError, AttributeError):
        return False

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


# ── Godot 4 node list ─────────────────────────────────────────────────────────

_GODOT_NODES: list[str] = [
    "Node", "Node2D", "Node3D",
    "AnimationPlayer", "AnimationTree",
    "AudioStreamPlayer", "AudioStreamPlayer2D", "AudioStreamPlayer3D",
    "Camera2D", "Camera3D",
    "CanvasLayer", "CanvasModulate",
    "CharacterBody2D", "CharacterBody3D",
    "CollisionShape2D", "CollisionShape3D", "CollisionPolygon2D",
    "Control", "CPUParticles2D", "CPUParticles3D",
    "DirectionalLight3D",
    "GPUParticles2D", "GPUParticles3D",
    "GraphEdit", "GridContainer",
    "HBoxContainer", "HScrollBar", "HSlider", "HSplitContainer",
    "HTTPRequest",
    "ItemList",
    "Label", "Light2D", "Line2D", "LineEdit",
    "MarginContainer", "MeshInstance3D",
    "MultiplayerSpawner", "MultiplayerSynchronizer",
    "NavigationAgent2D", "NavigationAgent3D",
    "NavigationRegion2D", "NavigationRegion3D",
    "NinePatchRect", "OmniLight3D",
    "PanelContainer", "Path2D", "Path3D",
    "PathFollow2D", "PathFollow3D",
    "Point2D", "Polygon2D", "PopupMenu", "ProgressBar",
    "RayCast2D", "RayCast3D", "RichTextLabel",
    "RigidBody2D", "RigidBody3D",
    "ScrollContainer",
    "ShapeCast2D", "ShapeCast3D",
    "Skeleton2D", "Skeleton3D",
    "SpotLight3D", "SpringArm3D",
    "Sprite2D", "Sprite3D",
    "StaticBody2D", "StaticBody3D",
    "SubViewport", "SubViewportContainer",
    "TabBar", "TabContainer",
    "TextEdit", "TextureButton", "TextureProgressBar", "TextureRect",
    "TileMap", "Timer", "TouchScreenButton", "Tree",
    "VBoxContainer", "VScrollBar", "VSlider", "VSplitContainer",
    "Viewport",
    "VisibleOnScreenNotifier2D", "VisibleOnScreenNotifier3D",
    "Window", "WorldEnvironment",
    "Resource", "RefCounted", "Object",
]


# ── File panel ────────────────────────────────────────────────────────────────

class FilePanel(Vertical):
    """Single Far Manager–style file browser panel."""

    BINDINGS = [
        Binding("backspace", "go_up",        "Up",       show=False),
        Binding("f4",        "new_file",      "New File", show=False),
        Binding("f7",        "mkdir",         "MkDir",    show=False),
        Binding("f6",        "rename_prompt", "Rename",   show=False),
        Binding("delete",    "delete_entry",  "Delete",   show=False),
        Binding("ctrl+c",    "copy_prompt",   "Copy",     show=False),
        Binding("ctrl+m",    "move_prompt",   "Move",     show=False),
        Binding("ctrl+f",    "search_file",   "Find",     show=False),
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
        _save_last_dir(path)
        self._refresh_listing()

    def _refresh_listing(self) -> None:
        try:
            entries = sorted(
                (p for p in self.current_path.iterdir() if not _is_hidden_system(p)),
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
                label_text = f"  {p.name}"
                css_class = "file"
            lst.append(ListItem(Label(label_text, markup=False), classes=css_class))

        self.call_after_refresh(self._highlight_first)

    def _highlight_first(self) -> None:
        """Step 1 (after repopulate): set ListView index to 0.
        This posts a Highlighted event; step 2 fires after it's processed."""
        lst = self._list
        if not lst.query("ListItem"):
            return
        lst.index = 0
        self.call_after_refresh(self._apply_first_highlight)

    def _apply_first_highlight(self) -> None:
        """Step 2: directly paint cyan on items[0].
        Runs after all Highlighted events from lst.index=0 have processed,
        so it is guaranteed to be the last write to items[0]'s colors."""
        items = list(self._list.query("ListItem"))
        if not items:
            return
        item = items[0]
        self._highlighted_item = item
        item.styles.background = "#00ffff"
        for lbl in item.query("Label"):
            lbl.styles.background = "#00ffff"
            lbl.styles.color = "#000000"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        start = _load_last_dir()
        if start != self.current_path:
            self.current_path = start   # triggers watch_current_path → _refresh_listing
        else:
            self._refresh_listing()

    # ── Navigation ────────────────────────────────────────────────────────────

    @on(ListView.Highlighted)
    def _on_item_highlighted(self, event: ListView.Highlighted) -> None:
        # item=None fires when lst.clear() runs; ignore it so it can't undo
        # a highlight that _highlight_first already applied.
        if event.item is None:
            return

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

    def action_new_file(self) -> None:
        self.app.push_screen(
            InputDialog("New file:", ""),
            lambda name: self._do_new_file(name) if name else None,
        )

    def _do_new_file(self, name: str) -> None:
        if not name:
            return
        target = self.current_path / name
        if name.endswith(".gd"):
            self.app.push_screen(
                ExtendsDialog(),
                lambda extends: self._create_file(target, extends),
            )
        else:
            self._create_file(target, None)

    def _create_file(self, target: Path, extends: str | None) -> None:
        if target.exists():
            self.notify(f'"{target.name}" already exists.', severity="error")
            return
        content = f"extends {extends}\n" if extends else ""
        try:
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            self.notify(str(exc), severity="error")
            return
        self._refresh_listing()
        self.app.open_file(target)

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

    def on_mount(self) -> None:
        self.query_one("#yes", Button).focus()

    @on(Button.Pressed, "#yes")
    def _yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def _no(self) -> None:
        self.dismiss(False)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(False)
        elif event.key in ("left", "right"):
            btns = list(self.query(Button))
            foc  = self.focused
            idx  = btns.index(foc) if foc in btns else 0
            btns[(idx + (1 if event.key == "right" else -1)) % len(btns)].focus()
            event.stop()


class ExtendsDialog(ModalScreen[str | None]):
    """Searchable 'extends' picker shown when creating a new .gd file."""

    DEFAULT_CSS = """
    ExtendsDialog {
        align: center middle;
    }
    ExtendsDialog #extends-dialog-box {
        background: #000080;
        border: solid #00ffff;
        padding: 1 2;
        width: 52;
        height: auto;
    }
    ExtendsDialog #extends-dialog-box > Label {
        color: white;
        padding: 0;
    }
    ExtendsDialog #extends-dialog-box > Input {
        border: solid #00aaaa;
        background: #000000;
        color: white;
        margin-top: 1;
    }
    ExtendsDialog #extends-list {
        height: 12;
        background: #000080;
        border: solid #00aaaa;
        margin-top: 1;
    }
    ExtendsDialog #extends-list > ListItem {
        background: #000080;
        color: white;
        height: 1;
        padding: 0;
    }
    ExtendsDialog #extends-list > ListItem > Label {
        height: 1;
        padding: 0;
        color: white;
    }
    ExtendsDialog #extends-list > ListItem.-highlight {
        background: #00ffff;
        color: #000000;
    }
    ExtendsDialog #extends-list > ListItem.-highlight > Label {
        background: #00ffff;
        color: #000000;
    }
    """

    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(self) -> None:
        super().__init__()
        self._all_items: list[str] = ["(without extends)"] + _GODOT_NODES
        self._filtered:  list[str] = list(self._all_items)

    def compose(self) -> ComposeResult:
        with Vertical(id="extends-dialog-box"):
            yield Label("extends:")
            yield Input(placeholder="Search node…", id="extends-search")
            yield ListView(id="extends-list")

    def on_mount(self) -> None:
        self._repopulate()
        self.query_one("#extends-search", Input).focus()

    def _repopulate(self) -> None:
        lst = self.query_one("#extends-list", ListView)
        lst.clear()
        for node in self._filtered:
            lst.append(ListItem(Label(node, markup=False)))

    @on(Input.Changed, "#extends-search")
    def _on_search(self, event: Input.Changed) -> None:
        q = event.value.lower()
        self._filtered = [
            n for n in self._all_items
            if not q or q in n.lower()
        ]
        self._repopulate()

    @on(Input.Submitted, "#extends-search")
    def _on_submitted(self, _: Input.Submitted) -> None:
        self._confirm()

    @on(ListView.Selected, "#extends-list")
    def _on_list_selected(self, _: ListView.Selected) -> None:
        self._confirm()

    def on_key(self, event) -> None:
        lst = self.query_one("#extends-list", ListView)
        if event.key == "down":
            lst.action_cursor_down()
            event.stop()
        elif event.key == "up":
            lst.action_cursor_up()
            event.stop()

    def action_cancel(self) -> None:
        self.dismiss("Node")

    def _confirm(self) -> None:
        lst = self.query_one("#extends-list", ListView)
        idx = lst.index
        if idx is not None and 0 <= idx < len(self._filtered):
            selected = self._filtered[idx]
        elif self._filtered:
            selected = self._filtered[0]
        else:
            selected = "Node"
        self.dismiss(None if selected == "(without extends)" else selected)
