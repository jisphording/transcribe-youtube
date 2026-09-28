"""Direct writes into the Obsidian vault for the batch queue.

The plugin sends the absolute vault path and the resolved folders with each batch;
Obsidian's file watcher picks the new files up. Independent: only `main.py` uses it.
"""

import os
import re
import tempfile
import time
from pathlib import Path


ID_KEYS = ("youtube_id", "apple_episode_id", "episode_guid")
_HEAD_BYTES = 4096


class VaultPathError(ValueError):
    pass


def validate_paths(vault_root: str, folders: list[str]) -> None:
    """Every folder must resolve inside the vault, and the vault must exist."""
    root = Path(vault_root).resolve()
    if not vault_root or not root.is_dir():
        raise VaultPathError(f"Vault folder not found: {vault_root}")
    for folder in folders:
        resolved = Path(folder).resolve()
        if resolved != root and root not in resolved.parents:
            raise VaultPathError(f"Folder is outside the vault: {folder}")


def write_note(folder: str, filename: str, content: str) -> str:
    """Atomic write (temp file + os.replace) so Obsidian never indexes a half-written note.
    On a name collision, append -<ms timestamp> like the plugin does. Returns the path."""
    target_dir = Path(folder)
    target_dir.mkdir(parents=True, exist_ok=True)
    name = Path(filename).name or "note.md"
    path = target_dir / name
    if path.exists():
        path = target_dir / f"{path.stem}-{int(time.time() * 1000)}.md"
    fd, tmp = tempfile.mkstemp(dir=target_dir, prefix=".tmp-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return str(path)


def _safe_stub_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|#^\[\]]', " ", name).strip().lstrip(".")
    return re.sub(r"\s+", " ", name)[:120]


def create_resource_stubs(folder: str, names: list[str]) -> None:
    """Empty `<name>.md` per resource, unless a file with that name exists (case-insensitive)."""
    target_dir = Path(folder)
    target_dir.mkdir(parents=True, exist_ok=True)
    existing = {p.stem.lower() for p in target_dir.rglob("*.md")}
    for raw in names:
        name = _safe_stub_name(raw)
        if not name or name.lower() in existing:
            continue
        (target_dir / f"{name}.md").touch()
        existing.add(name.lower())


def _frontmatter(path: Path) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(_HEAD_BYTES)
    except OSError:
        return ""
    if not head.startswith("---"):
        return ""
    end = head.find("\n---", 3)
    return head[3:end] if end != -1 else ""


def ids_from_frontmatter(fm: str) -> set[str]:
    ids = set()
    for key in ID_KEYS:
        m = re.search(rf'^{key}:\s*"?([^"\n]+)"?\s*$', fm, re.M)
        if m:
            ids.add(m.group(1).strip())
    # Legacy notes without id keys: parse the ids out of `url:`
    url = re.search(r'^url:\s*"?([^"\n]+)"?', fm, re.M)
    if url:
        u = url.group(1)
        yt = re.search(r"(?:[?&]v=|youtu\.be/|/embed/)([0-9A-Za-z_-]{11})", u)
        if yt:
            ids.add(yt.group(1))
        ap = re.search(r"[?&]i=(\d+)", u)
        if ap:
            ids.add(ap.group(1))
    return ids


def scan_ids(roots: list[str]) -> set[str]:
    """Known video / episode ids of all notes below the given folders (YAML head only)."""
    ids: set[str] = set()
    for root in roots:
        base = Path(root)
        if not base.is_dir():
            continue
        for path in base.rglob("*.md"):
            ids |= ids_from_frontmatter(_frontmatter(path))
    return ids
