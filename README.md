# DekodeIDE

MS-DOS / Far Manager style TUI code editor for GDScript, built with Python + Textual + Rich.

## Requirements

- Python 3.11+
- Textual 0.80+
- Rich (installed with Textual)

## Install

```bash
pip install "textual>=0.80"
```

## Run

```bash
python main.py
```

## Keyboard Reference

| Key | Action |
|---|---|
| F1 | Help screen |
| F2 | Save file |
| F5 | Run in Godot (headless) |
| F8 | Stop Godot process |
| F9 | Toggle debug panel |
| Tab | Switch focus: Files ↔ Editor |
| Ctrl+Q | Quit |
| Ctrl+F | Open search/replace bar |

### File Panel
| Key | Action |
|---|---|
| ↑ / ↓ | Navigate |
| Enter | Open dir or file |
| Backspace | Parent directory |
| F7 | New directory |
| Delete | Delete entry |
| F5 | Copy to other panel |
| F6 | Move to other panel |
| Ctrl+F | Filter by name |

### Editor
| Key | Action |
|---|---|
| Ctrl+Z | Undo |
| Ctrl+Y | Redo |
| F2 | Save |
| Ctrl+F | Find / Replace |
| Escape | Close search bar |

## Godot Integration

Make sure `godot` (Linux/macOS) or `godot.exe` (Windows) is on your PATH.

F5 auto-saves the current file, then runs:

```
godot --headless <file>
```

Output streams live in the debug panel. F8 kills the process.

## Project Structure

```
main.py        — App entry point, layout, keybindings
editor.py      — CodeEditor widget (TextArea + GDScript highlighting)
file_panel.py  — FilePanel widget (directory listing, file ops, dialogs)
debug_panel.py — DebugPanel widget (async subprocess, live output)
highlighter.py — Regex tokenizer for GDScript
config.py      — Colors, CSS, GDScript vocabulary
README.md      — This file
```
