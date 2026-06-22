#!/usr/bin/env python3
"""Download and parse Godot engine class documentation from GitHub."""

from __future__ import annotations

import json
import re
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_OUTPUT_DIR = Path(__file__).parent
_TIMEOUT    = 30
_API_BASE   = "https://api.github.com/repos/godotengine/godot"
_RAW_BASE   = "https://raw.githubusercontent.com/godotengine/godot"
_HEADERS    = {
    "User-Agent": "dekode-editor/1.0",
    "Accept":     "application/vnd.github.v3+json",
}

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (429, 403):
            raise RuntimeError("rate_limit_exceeded") from exc
        raise
    except urllib.error.URLError as exc:
        reason = str(exc.reason).lower()
        if "timed out" in reason or "timeout" in reason:
            raise TimeoutError("Server response timed out") from exc
        raise ConnectionError("No internet connection") from exc


def _get_all_pages(url: str) -> list[Any]:
    """Fetch all pages of a paginated GitHub API endpoint."""
    results: list[Any] = []
    page_url: str | None = f"{url}?per_page=100"
    while page_url:
        req = urllib.request.Request(page_url, headers=_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                results.extend(json.loads(resp.read().decode()))
                link       = resp.headers.get("Link", "")
                page_url   = None
                for part in link.split(","):
                    if 'rel="next"' in part:
                        m = re.search(r"<([^>]+)>", part)
                        if m:
                            page_url = m.group(1)
                            break
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 403):
                raise RuntimeError("rate_limit_exceeded") from exc
            raise
        except urllib.error.URLError as exc:
            reason = str(exc.reason).lower()
            if "timed out" in reason or "timeout" in reason:
                raise TimeoutError("Server response timed out") from exc
            raise ConnectionError("No internet connection") from exc
    return results


# ── Version discovery ─────────────────────────────────────────────────────────

_STABLE_TAG = re.compile(r"^refs/tags/(4\.\d+(?:\.\d+)?-stable)$")


def fetch_stable_versions() -> list[str]:
    """Return stable Godot 4.x version tags sorted newest-first.

    Raises ConnectionError, TimeoutError, RuntimeError on failure.
    """
    refs     = _get_all_pages(f"{_API_BASE}/git/refs/tags")
    versions = []
    for ref in refs:
        m = _STABLE_TAG.match(ref.get("ref", ""))
        if m:
            versions.append(m.group(1))

    def _key(v: str) -> tuple[int, ...]:
        return tuple(int(n) for n in re.findall(r"\d+", v.split("-")[0]))

    versions.sort(key=_key, reverse=True)
    return versions


# ── XML parsing ───────────────────────────────────────────────────────────────

def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def parse_class_xml(xml_bytes: bytes) -> dict | None:
    """Parse a Godot class XML blob into a dict. Returns None on parse failure."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    if root.tag != "class":
        return None

    methods: list[dict] = []
    for m in root.findall("./methods/method"):
        params = [
            {"name": p.get("name", ""), "type": p.get("type", ""), "default": p.get("default", "")}
            for p in m.findall("param")
        ]
        ret = m.find("return")
        methods.append({
            "name":        m.get("name", ""),
            "params":      params,
            "return_type": ret.get("type", "void") if ret is not None else "void",
            "description": _text(m.find("description")),
        })

    properties: list[dict] = []
    for p in root.findall("./members/member"):
        properties.append({
            "name":        p.get("name", ""),
            "type":        p.get("type", ""),
            "default":     p.get("default", ""),
            "description": "".join(p.itertext()).strip(),
        })

    signals: list[dict] = []
    for s in root.findall("./signals/signal"):
        params = [{"name": p.get("name", ""), "type": p.get("type", "")} for p in s.findall("param")]
        signals.append({
            "name":        s.get("name", ""),
            "params":      params,
            "description": _text(s.find("description")),
        })

    constants: list[dict] = []
    for c in root.findall("./constants/constant"):
        constants.append({
            "name":        c.get("name", ""),
            "value":       c.get("value", ""),
            "description": "".join(c.itertext()).strip(),
        })

    annotations: list[dict] = []
    for a in root.findall("./annotations/annotation"):
        annotations.append({
            "name":        a.get("name", ""),
            "description": _text(a.find("description")),
        })

    return {
        "inherits":    root.get("inherits", ""),
        "brief":       _text(root.find("brief_description")),
        "description": _text(root.find("description")),
        "methods":     methods,
        "properties":  properties,
        "signals":     signals,
        "constants":   constants,
        "annotations": annotations,
    }


# ── Progress bar ──────────────────────────────────────────────────────────────

def _bar(done: int, total: int, width: int = 20) -> str:
    pct    = done / total if total else 0
    filled = int(width * pct)
    return f"[{'█' * filled}{'░' * (width - filled)}] {int(pct * 100)}% ({done}/{total} classes)"


# ── Download pipeline ─────────────────────────────────────────────────────────

def download_version(
    tag: str,
    version: str,
    verbose: bool = False,
    on_progress=None,
) -> Path:
    """Download all class XML files for *tag*, parse and save JSON.

    Args:
        tag:         full tag string, e.g. "4.2-stable"
        version:     short version string, e.g. "4.2"
        verbose:     print status messages to stdout
        on_progress: optional callable(done: int, total: int)

    Returns the path to the saved JSON file.
    Raises ConnectionError, TimeoutError, RuntimeError on unrecoverable failure.
    """
    if verbose:
        print(f"Fetching class list for {tag}...")

    entries   = json.loads(_get(f"{_API_BASE}/contents/doc/classes?ref={tag}").decode())
    xml_files = [e["name"] for e in entries if e["name"].endswith(".xml")]
    total     = len(xml_files)
    classes: dict[str, dict] = {}
    failed    = 0

    if verbose:
        print(f"Downloading Godot {version} documentation...")

    for i, fname in enumerate(xml_files, 1):
        class_name = fname[:-4]
        url        = f"{_RAW_BASE}/{tag}/doc/classes/{fname}"
        try:
            data   = _get(url)
            parsed = parse_class_xml(data)
            if parsed:
                classes[class_name] = parsed
        except Exception as exc:
            failed += 1
            if verbose:
                print(f"\nWarning: Failed to download {fname}: {exc}. Skipping and continuing.")

        if on_progress:
            on_progress(i, total)
        elif verbose:
            print(f"\r {_bar(i, total)}", end="", flush=True)

    if verbose:
        print()
        success = len(classes)
        if failed:
            print(f"Done: successfully loaded {success} out of {total} classes.")
        else:
            print(f"Done: {success} classes loaded.")

    out_path = _OUTPUT_DIR / f"godot_api_{version}.json"
    out_path.write_text(
        json.dumps(
            {
                "version":      version,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "classes":      classes,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    if verbose:
        print(f"Saved: {out_path}")

    return out_path


# ── Version selection (CLI) ───────────────────────────────────────────────────

def _pick_version(versions: list[str]) -> str | None:
    print("Available Godot 4.x stable versions:")
    for i, tag in enumerate(versions, 1):
        ver    = tag.replace("-stable", "")
        cached = (_OUTPUT_DIR / f"godot_api_{ver}.json").exists()
        mark   = " [cached]" if cached else ""
        print(f"  {i:2}. {ver}{mark}")
    print()
    while True:
        raw = input(f"Select (1-{len(versions)}, or q to quit): ").strip()
        if raw.lower() == "q":
            return None
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(versions):
                return versions[idx]
        except ValueError:
            pass
        print(f"Please enter a number between 1 and {len(versions)}.")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Download Godot API documentation.")
    parser.add_argument("--version", "-v", help="Tag to download, e.g. 4.2-stable or 4.2")
    parser.add_argument("--list",    "-l", action="store_true", help="List available versions and exit")
    args = parser.parse_args()

    # ── Fetch version list ────────────────────────────────────────────────────
    print("Fetching available Godot versions...")
    try:
        versions = fetch_stable_versions()
    except ConnectionError:
        print("Error: No internet connection.\nPlease check your connection and try again.")
        sys.exit(1)
    except TimeoutError:
        print("Error: Server response timed out.")
        sys.exit(1)
    except RuntimeError as exc:
        if "rate_limit" in str(exc):
            print("Error: GitHub API rate limit exceeded.\nPlease try again later or use a GitHub token.")
        else:
            print(f"Error: {exc}")
        sys.exit(1)

    if not versions:
        print("No stable Godot 4.x versions found.")
        sys.exit(1)

    if args.list:
        for tag in versions:
            print(tag.replace("-stable", ""))
        return

    # ── Resolve target tag ────────────────────────────────────────────────────
    if args.version:
        tag = args.version if args.version.endswith("-stable") else f"{args.version}-stable"
        if tag not in versions:
            print(f"Version '{tag}' not found in available stable releases.")
            sys.exit(1)
    else:
        tag = _pick_version(versions)
        if tag is None:
            return

    version  = tag.replace("-stable", "")
    out_path = _OUTPUT_DIR / f"godot_api_{version}.json"

    if out_path.exists():
        ans = input("File already exists. Re-download? (y/n): ").strip().lower()
        if ans != "y":
            print("Keeping existing file.")
            return

    # ── Download ──────────────────────────────────────────────────────────────
    try:
        download_version(tag, version, verbose=True)
    except ConnectionError:
        print("Error: No internet connection.\nPlease check your connection and try again.")
        sys.exit(1)
    except TimeoutError:
        print("Error: Server response timed out.")
        sys.exit(1)
    except RuntimeError as exc:
        if "rate_limit" in str(exc):
            print("Error: GitHub API rate limit exceeded.\nPlease try again later or use a GitHub token.")
        else:
            print(f"Error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
