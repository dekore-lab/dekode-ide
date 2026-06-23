"""GDScript source parser for project-aware autocomplete."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class GDParam:
    name: str
    type: str | None = None
    default: str | None = None


@dataclass
class GDVar:
    name: str
    type: str | None = None
    value: str | None = None
    is_const: bool = False


@dataclass
class GDFunc:
    name: str
    params: list[GDParam] = field(default_factory=list)
    return_type: str | None = None
    is_static: bool = False


@dataclass
class GDSignal:
    name: str
    params: list[GDParam] = field(default_factory=list)


@dataclass
class GDFileInfo:
    path: Path
    class_name: str | None = None
    extends: str | None = None
    vars: list[GDVar] = field(default_factory=list)
    funcs: list[GDFunc] = field(default_factory=list)
    signals: list[GDSignal] = field(default_factory=list)


# ── Regex patterns ─────────────────────────────────────────────────────────────

_RE_CLASS_NAME = re.compile(r"^class_name\s+(\w+)", re.MULTILINE)

# extends ClassName  OR  extends "res://path.gd"
# Also handles single-line form: class_name Foo extends Bar
_RE_EXTENDS = re.compile(
    r'^(?:class_name\s+\w+\s+)?extends\s+(".*?"|[\w]+)',
    re.MULTILINE,
)

# Optional annotations (@export, @export_range(0,100), …) then const/var decl.
_RE_VAR = re.compile(
    r"^[ \t]*(?:(?:@\w+(?:\([^)]*\))?\s+)*)?"
    r"(const|var)\s+(\w+)"
    r"(?:\s*:\s*(\w+))?"
    r"(?:\s*=\s*([^\n#]+?))?"
    r"[ \t]*(?:#[^\n]*)?$",
    re.MULTILINE,
)

_RE_FUNC = re.compile(
    r"^[ \t]*(static\s+)?func\s+(\w+)\s*\(([^)]*)\)"
    r"(?:\s*->\s*(\w+))?\s*:",
    re.MULTILINE,
)

_RE_SIGNAL = re.compile(
    r"^[ \t]*signal\s+(\w+)(?:\s*\(([^)]*)\))?",
    re.MULTILINE,
)

# Type inference from constructor: Enemy.new()  →  Enemy
_RE_NEW = re.compile(r"^(\w+)\.new\(\)")
# Type inference from node path: $Sprite2D  or  $Parent/Child/Sprite2D  →  Sprite2D
_RE_DOLLAR = re.compile(r"^\$(?:\w+/)*(\w+)")


# ── Param parser ───────────────────────────────────────────────────────────────

def _parse_params(raw: str) -> list[GDParam]:
    raw = raw.strip()
    if not raw:
        return []
    params: list[GDParam] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\w+)(?:\s*:\s*(\w+))?(?:\s*=\s*(.+))?$", part)
        if m:
            params.append(GDParam(
                name    = m.group(1),
                type    = m.group(2),
                default = m.group(3).strip() if m.group(3) else None,
            ))
    return params


# ── File parser ────────────────────────────────────────────────────────────────

def parse_text(text: str, path: Path) -> GDFileInfo:
    """Parse GDScript source string and return structured declarations."""
    info = GDFileInfo(path=path)

    m = _RE_CLASS_NAME.search(text)
    if m:
        info.class_name = m.group(1)

    m = _RE_EXTENDS.search(text)
    if m:
        info.extends = m.group(1)

    for m in _RE_VAR.finditer(text):
        explicit_type = m.group(3)
        raw_value     = m.group(4).strip() if m.group(4) else None
        inferred_type = explicit_type
        if inferred_type is None and raw_value:
            mn = _RE_NEW.match(raw_value)
            if mn:
                inferred_type = mn.group(1)
            else:
                md = _RE_DOLLAR.match(raw_value)
                if md:
                    inferred_type = md.group(1)
        info.vars.append(GDVar(
            name     = m.group(2),
            type     = inferred_type,
            value    = raw_value,
            is_const = m.group(1) == "const",
        ))

    for m in _RE_FUNC.finditer(text):
        info.funcs.append(GDFunc(
            name        = m.group(2),
            params      = _parse_params(m.group(3)),
            return_type = m.group(4),
            is_static   = bool(m.group(1)),
        ))

    for m in _RE_SIGNAL.finditer(text):
        info.signals.append(GDSignal(
            name   = m.group(1),
            params = _parse_params(m.group(2) or ""),
        ))

    return info


def parse_file(path: Path) -> GDFileInfo | None:
    """Read and parse a GDScript file. Returns None on I/O error."""
    try:
        return parse_text(path.read_text(encoding="utf-8", errors="replace"), path)
    except OSError:
        return None


# ── Project index ──────────────────────────────────────────────────────────────

class ProjectIndex:
    """Live index of GDScript class_names → file paths, built in background."""

    def __init__(self) -> None:
        self._by_class: dict[str, Path]        = {}
        self._by_path:  dict[Path, GDFileInfo] = {}
        self._lock   = threading.Lock()
        self._root:  Path | None               = None
        self._stop   = threading.Event()
        self._thread: threading.Thread | None  = None

    # ── Public API ─────────────────────────────────────────────────────────

    def start_indexing(self, root: Path) -> None:
        """Start background scan of root. No-op if already scanning same root."""
        if not root.is_dir() or root == self._root:
            return
        self._stop.set()
        self._root = root
        self._stop = threading.Event()
        t = threading.Thread(target=self._worker, daemon=True)
        t.start()
        self._thread = t

    def update_file(self, path: Path, info: GDFileInfo) -> None:
        """Upsert one file's parse result (call after save or live edit)."""
        with self._lock:
            old = self._by_path.get(path)
            if old and old.class_name:
                self._by_class.pop(old.class_name, None)
            self._by_path[path] = info
            if info.class_name:
                self._by_class[info.class_name] = path

    def find_class_info(self, class_name: str) -> GDFileInfo | None:
        with self._lock:
            p = self._by_class.get(class_name)
            return self._by_path.get(p) if p else None

    def find_by_res_path(self, res_path: str, relative_to: Path) -> GDFileInfo | None:
        """Resolve a res:// or relative path string to GDFileInfo."""
        root = self._root
        if root and res_path.startswith("res://"):
            candidate = (root / res_path[6:]).resolve()
        else:
            candidate = (relative_to.parent / res_path).resolve()
        with self._lock:
            return self._by_path.get(candidate)

    # ── Background worker ───────────────────────────────────────────────────

    def _worker(self) -> None:
        root = self._root
        if root is None:
            return
        stop = self._stop
        for gd in root.rglob("*.gd"):
            if stop.is_set():
                return
            info = parse_file(gd)
            if info:
                with self._lock:
                    old = self._by_path.get(gd)
                    if old and old.class_name:
                        self._by_class.pop(old.class_name, None)
                    self._by_path[gd] = info
                    if info.class_name:
                        self._by_class[info.class_name] = gd


# Module-level singleton used by CodeEditor
_index = ProjectIndex()


def get_project_index() -> ProjectIndex:
    return _index


# ── Inheritance chain resolver ─────────────────────────────────────────────────

def resolve_chain(
    info: GDFileInfo,
    api_classes: dict,
    max_depth: int = 10,
) -> tuple[list[GDFileInfo], str | None]:
    """
    Walk the extends chain starting from *info*.

    Returns:
        user_parents  — GDFileInfo for each user-defined ancestor (in order)
        builtin_class — name of the first built-in Godot class encountered, or None
    """
    index = _index
    user_parents: list[GDFileInfo] = []
    visited: set[Path] = {info.path}
    extends = info.extends
    depth = 0

    while extends and depth < max_depth:
        depth += 1

        # Path-based: extends "res://..." or extends "path/to/file.gd"
        if extends.startswith('"') or extends.startswith("'"):
            path_str = extends.strip("\"'")
            parent   = index.find_by_res_path(path_str, info.path)
            if parent is None or parent.path in visited:
                break
            visited.add(parent.path)
            user_parents.append(parent)
            extends = parent.extends
            continue

        # Built-in Godot class (present in downloaded API JSON)
        if extends in api_classes:
            return user_parents, extends

        # User-defined class (look up by class_name in index)
        parent = index.find_class_info(extends)
        if parent is None:
            # Unknown — pass it on; editor will try it as a built-in fallback
            return user_parents, extends
        if parent.path in visited:
            break
        visited.add(parent.path)
        user_parents.append(parent)
        extends = parent.extends

    return user_parents, None
