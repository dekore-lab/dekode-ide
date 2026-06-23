"""Theme colors, GDScript vocabulary, and app-wide CSS."""

APP_TITLE = "dekode"
APP_VERSION = "0.1.0"

# ── GDScript vocabulary ───────────────────────────────────────────────────────

GDSCRIPT_KEYWORDS = {
    "if", "elif", "else", "for", "while", "match", "break", "continue",
    "pass", "return", "class", "class_name", "extends", "is", "in",
    "not", "and", "or", "var", "const", "func", "static", "enum",
    "signal", "tool", "await", "assert", "self", "null", "true", "false",
    "super", "as", "new",
}

GDSCRIPT_TYPES = {
    "void", "int", "float", "String", "bool", "Array", "Dictionary",
    "Object", "Node", "Node2D", "Node3D", "Vector2", "Vector3", "Vector4",
    "Color", "Rect2", "Transform2D", "Transform3D", "Basis", "Quaternion",
    "Plane", "AABB", "NodePath", "PackedScene", "Resource", "RefCounted",
    "StringName", "RID", "Callable", "Signal",
}

GDSCRIPT_BUILTINS = {
    "print", "prints", "printerr", "printraw", "print_debug",
    "push_error", "push_warning", "abs", "ceil", "floor", "round",
    "max", "min", "pow", "sqrt", "sin", "cos", "tan", "asin", "acos",
    "atan", "atan2", "deg_to_rad", "rad_to_deg", "lerp", "clamp", "range",
    "len", "typeof", "str", "get_node", "has_node", "add_child",
    "remove_child", "emit_signal", "connect", "disconnect",
    "instantiate", "queue_free", "load", "preload",
    "set_process", "set_physics_process", "is_instance_valid",
    "wrapi", "wrapf", "sign", "snapped", "move_toward",
}

# ── Far Manager–style CSS ─────────────────────────────────────────────────────

CSS = """
/* ── Screen ─────────────────────────────────────────────── */
Screen {
    background: #0000aa;
    layers: base overlay;
}

/* ── App header (dark title bar) ────────────────────────── */
#app-header {
    height: 1;
    background: #555555;
    color: white;
    content-align: center middle;
    text-style: bold;
}

/* ── Main two-panel area ─────────────────────────────────── */
#main-area {
    height: 1fr;
    layout: horizontal;
    background: #0000aa;
}

/* ── File panel ─────────────────────────────────────────── */
FilePanel {
    width: 35;
    border: solid #004488;
    background: #0000aa;
    color: white;
    padding: 0;
}

FilePanel:focus-within {
    border: solid #FFFF00;
}

FilePanel > ListView {
    background: #0000aa;
    color: white;
    height: 1fr;
    scrollbar-color: #00aaaa;
    scrollbar-background: #000055;
    padding: 0;
}

FilePanel > ListView > ListItem {
    background: #0000aa;
    color: white;
    padding: 0 0;
    height: 1;
}

FilePanel > ListView > ListItem > Label {
    height: 1;
    padding: 0;
    width: 1fr;
}

/* Selected (highlighted) row: black text on bright cyan — full row width */
FilePanel > ListView > ListItem.-highlight {
    background: #00ffff;
    color: #000000;
}

/* Label inside highlighted item must also fill the full width */
FilePanel > ListView > ListItem.-highlight > Label {
    background: #00ffff;
    color: #000000;
    width: 1fr;
}

/* Directories: bright white bold */
FilePanel > ListView > ListItem.dir {
    color: #ffffff;
    text-style: bold;
}

FilePanel > ListView > ListItem.dir.-highlight {
    background: #00ffff;
    color: #000000;
}

FilePanel > ListView > ListItem.dir.-highlight > Label {
    background: #00ffff;
    color: #000000;
    width: 1fr;
}

/* ".." parent entry: same as directories */
FilePanel > ListView > ListItem.parent {
    color: #ffffff;
    text-style: bold;
}

FilePanel > ListView > ListItem.parent.-highlight {
    background: #00ffff;
    color: #000000;
}

FilePanel > ListView > ListItem.parent.-highlight > Label {
    background: #00ffff;
    color: #000000;
    width: 1fr;
}

/* Filter input (hidden by default, shown with Ctrl+F) */
FilePanel > Input {
    display: none;
    height: 1;
    background: #000000;
    color: #00ffff;
    border: none;
    padding: 0 1;
}

FilePanel > Input.visible {
    display: block;
}

/* ── Editor container ───────────────────────────────────── */
#editor-container {
    width: 1fr;
    border: solid #004488;
    background: #000000;
    layout: vertical;
}

#editor-container:focus-within {
    border: solid #FFFF00;
}

CodeEditor {
    height: 1fr;
    background: #000000;
    color: white;
    padding: 0;
    scrollbar-color: #004488;
    scrollbar-background: #000000;
    scrollbar-corner-color: #000000;
}

/* ── Search bar ─────────────────────────────────────────── */
SearchBar {
    height: 3;
    background: #000080;
    display: none;
    padding: 0 1;
}

SearchBar.visible {
    display: block;
}

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

/* ── Debug panel ────────────────────────────────────────── */
#debug-panel {
    height: 10;
    border: solid #00ffff;
    background: #000000;
    display: none;
    layout: vertical;
}

#debug-panel.visible {
    display: block;
}

#debug-header {
    background: #00aaaa;
    color: #000000;
    height: 1;
    content-align: center middle;
    text-style: bold;
}

#debug-log {
    height: 1fr;
    background: #000000;
    color: #00ff00;
    scrollbar-color: #00aaaa;
}

/* ── Status bar ─────────────────────────────────────────── */
#status-bar {
    height: 1;
    background: #000000;
    padding: 0;
}

/* ── Key bar (Far Manager function keys) ────────────────── */
#key-bar {
    height: 1;
    background: #000000;
    color: white;
}

/* ── Modal dialogs ──────────────────────────────────────── */
InputDialog {
    align: center middle;
}

ConfirmDialog {
    align: center middle;
}

#dialog {
    background: #000080;
    border: solid #00ffff;
    padding: 1 2;
    width: 52;
    height: auto;
}

#dialog Label {
    color: white;
    padding: 0 0 1 0;
}

#dialog Input {
    border: solid #00aaaa;
    background: #000000;
    color: white;
}

#dialog Button {
    background: #00aaaa;
    color: #000000;
    border: none;
    margin: 1 1 0 0;
    min-width: 10;
}

#dialog Button:hover, #dialog Button.-active {
    background: white;
    color: #000000;
}

/* ── Help screen ─────────────────────────────────────────── */
HelpScreen {
    align: center middle;
    background: rgba(0,0,0,0.7);
}

#help-box {
    background: #000080;
    border: solid #00ffff;
    padding: 1 2;
    width: 56;
    height: auto;
    color: white;
}

#help-close {
    background: #00aaaa;
    color: #000000;
    border: none;
    margin-top: 1;
    width: 10;
}
"""
