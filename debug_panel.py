"""Debug panel — runs Godot CLI and streams stdout/stderr in real time."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Log, Static
from textual.reactive import reactive


def _godot_binary() -> str:
    """Return the platform-appropriate Godot binary name."""
    if sys.platform == "win32":
        return "godot.exe"
    return "godot"


class DebugPanel(Vertical):
    """Collapsible panel that runs a file through the Godot CLI."""

    is_running: reactive[bool] = reactive(False)
    _process: asyncio.subprocess.Process | None = None

    def compose(self) -> ComposeResult:
        yield Static(" DEBUG OUTPUT ", id="debug-header")
        yield Log(id="debug-log", auto_scroll=True, highlight=True)

    @property
    def _log(self) -> Log:
        return self.query_one("#debug-log", Log)

    # ── Public API ────────────────────────────────────────────────────────────

    def run_file(self, file_path: Path) -> None:
        if self.is_running:
            self.stop()
        self._log.clear()
        self._log.write_line(f"$ {_godot_binary()} --headless {file_path}")
        self._log.write_line("─" * 60)
        self.app.call_later(self._start_process, file_path)

    def stop(self) -> None:
        if self._process and self._process.returncode is None:
            try:
                self._process.kill()
            except ProcessLookupError:
                pass
        self.is_running = False

    # ── Subprocess ────────────────────────────────────────────────────────────

    async def _start_process(self, file_path: Path) -> None:
        binary = _godot_binary()
        cmd = [binary, "--headless", str(file_path)]

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(file_path.parent),
            )
        except FileNotFoundError:
            self._log.write_line(
                f"[red]ERROR: '{binary}' not found. "
                "Add Godot to your PATH.[/red]"
            )
            return
        except OSError as exc:
            self._log.write_line(f"[red]ERROR: {exc}[/red]")
            return

        self.is_running = True
        self.app.call_later(self._stream_output)

    async def _stream_output(self) -> None:
        proc = self._process
        if proc is None or proc.stdout is None:
            return

        try:
            async for line in proc.stdout:
                decoded = line.decode("utf-8", errors="replace").rstrip()
                self._log.write_line(decoded)
        except Exception:
            pass

        await proc.wait()
        rc = proc.returncode
        color = "green" if rc == 0 else "red"
        self._log.write_line("─" * 60)
        self._log.write_line(f"[{color}]Process exited with code {rc}[/{color}]")
        self.is_running = False

    # ── Watch ─────────────────────────────────────────────────────────────────

    def watch_is_running(self, running: bool) -> None:
        header = self.query_one("#debug-header", Static)
        state = "RUNNING…" if running else "STOPPED"
        header.update(f" DEBUG OUTPUT  [{state}] ")
