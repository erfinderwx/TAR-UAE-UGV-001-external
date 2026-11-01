"""Check document change discipline without treating metadata as human approval."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml

MANIFEST = "sources/records/source-manifest.json"
VERSION = re.compile(r"v(0|[1-9]\d*)(?:\.(?:0|[1-9]\d*)){1,3}\Z")
PLACEHOLDERS = {"待确认", "待补", "待补充", "待填写", "tbd", "todo", "pending"}


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


def governed(path: str) -> bool:
    parts = Path(path).parts
    return (len(parts) > 1 and parts[0] == "docs" and path.endswith(".md")
            and not {"assets", "_templates"}.intersection(parts)
            and parts[-1] not in {"README.md", "INDEX.md"})


def metadata(data: bytes) -> dict:
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", data.decode("utf-8"), re.S)
    if not match:
        raise ValueError("missing front matter")
    fields = yaml.safe_load(match.group(1))
    if not isinstance(fields, dict):
        raise ValueError("front matter is not a mapping")
    return fields


def version(value: object) -> tuple[int, ...]:
    if not isinstance(value, str) or not VERSION.fullmatch(value):
        raise ValueError("version must be vMAJOR.MINOR[.PATCH[.REVISION]]")
    parts = tuple(int(x) for x in value[1:].split("."))
    return parts + (0,) * (4 - len(parts))


def date(value: object, field: str) -> dt.date:
    text = str(value)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError(f"{field} must be YYYY-MM-DD")
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{field} is not a valid calendar date") from None


def local_path(root: Path, path: str) -> Path:
    candidate = (root / path).resolve()
    if Path(path).is_absolute() or not candidate.is_relative_to(root) or ".git" in candidate.relative_to(root).parts:
        raise ValueError(f"path must stay inside the repository: {path}")
    return candidate


def approval_reference(root: Path, value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    parts = urlsplit(value.strip())
    if parts.scheme == "https" and parts.netloc:
        return True
    if parts.scheme or parts.netloc or not parts.path or parts.path.startswith("/"):
        return False
    try:
        return local_path(root, unquote(parts.path)).is_file()
    except ValueError:
        return False


def manifest(data: bytes, label: str, errors: list[str]) -> dict[str, dict]:
    if not data:
        return {}
    try:
        payload = json.loads(data)
        records = payload["files"]
        if not isinstance(records, list):
            raise ValueError("files must be a list")
        found, ids = {}, set()
        for item in records:
            if not isinstance(item, dict):
                raise ValueError("every source record must be an object")
            path, identity = item.get("path"), item.get("id")
            if not isinstance(path, str) or not path:
                raise ValueError("source path must be non-empty text")
            if path in found:
                errors.append(f"{label}: duplicate source path {path}")
            if not isinstance(identity, str) or not identity or identity in ids:
                errors.append(f"{label}: missing or duplicate source id for {path}")
            ids.add(identity)
            if not isinstance(item.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
                errors.append(f"{label}: invalid SHA-256 for {path}")
            if type(item.get("bytes")) is not int or item["bytes"] < 0:
                errors.append(f"{label}: invalid byte size for {path}")
            found[path] = item
        return found
    except (ValueError, TypeError, KeyError) as exc:
        errors.append(f"{label}: invalid manifest: {exc}")
        return {}


def validate(root: Path, base: str | None = None) -> dict:
    root = root.resolve()
    errors: list[str] = []
    base_sha = None
    old_paths: set[str] = set()
    if base:
        try:
            base_sha = git(root, "rev-parse", "--verify", "--end-of-options", base + "^{commit}").decode().strip()
            old_paths = set(git(root, "ls-tree", "-r", "--name-only", "-z", base_sha).decode("utf-8").split("\0"))
        except ValueError as exc:
            return {"documents": 0, "base": base, "errors": [f"Cannot resolve comparison base: {exc}"]}

    checked = 0
    for path in sorted((root / "docs").rglob("*.md")):
        relative = path.relative_to(root).as_posix()
        if not governed(relative):
            continue
        checked += 1
        data = path.read_bytes()
        try:
            fields = metadata(data)
            current_version = version(fields.get("version"))
            current_date = date(fields.get("updated"), "updated")
            if fields.get("status") == "定稿":
                approver = fields.get("approved_by")
                if not isinstance(approver, str) or not approver.strip() or approver.strip().lower() in PLACEHOLDERS:
                    errors.append(f"{relative}: final document needs approved_by")
                date(fields.get("approved_on"), "approved_on")
                if not approval_reference(root, fields.get("approval_ref")):
                    errors.append(f"{relative}: final document needs approval_ref (HTTPS URL or existing repository-relative file)")
            if base_sha and relative in old_paths:
                previous = git(root, "show", base_sha + ":" + relative)
                # Git may check out Markdown with CRLF on Windows. A line-ending
                # conversion alone is not a document revision. Source originals
                # below deliberately retain byte-for-byte comparisons.
                if previous.replace(b"\r\n", b"\n") != data.replace(b"\r\n", b"\n"):
                    old = metadata(previous)
                    if current_version <= version(old.get("version")):
                        errors.append(f"{relative}: changed document must increase version")
                    if current_date < date(old.get("updated"), "previous updated"):
                        errors.append(f"{relative}: updated must not move backwards")
        except (ValueError, TypeError, yaml.YAMLError) as exc:
            errors.append(f"{relative}: {exc}")

    agent, claude = root / "AGENTS.md", root / "CLAUDE.md"
    if agent.exists() or claude.exists():
        if not agent.is_file() or not claude.is_file() or agent.read_bytes() != claude.read_bytes():
            errors.append("AGENTS.md and CLAUDE.md must both exist and be byte-identical")

    manifest_path = root / MANIFEST
    sources = manifest(manifest_path.read_bytes() if manifest_path.exists() else b"", MANIFEST, errors)
    old_sources = manifest(git(root, "show", base_sha + ":" + MANIFEST), "base " + MANIFEST, errors) if base_sha and MANIFEST in old_paths else {}
    # Compare against the base inventory, so changing a file and its hash together
    # cannot erase the original evidence. Descriptive fields may be supplemented.
    for path, old in old_sources.items():
        current = sources.get(path)
        if current is None:
            errors.append(f"{path}: registered original must remain in the manifest")
        else:
            for key in ("id", "sha256", "bytes", "original_filename"):
                if current.get(key) != old.get(key):
                    errors.append(f"{path}: registered original {key} is immutable; add a new source path instead")

    for path in sorted(set(sources) | set(old_sources)):
        try:
            original = local_path(root, path)
            if not original.is_file():
                raise ValueError("registered original is missing")
            data = original.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            for label, record in (("current", sources.get(path)), ("base", old_sources.get(path))):
                if record and (digest != record.get("sha256") or len(data) != record.get("bytes")):
                    errors.append(f"{path}: bytes or SHA-256 differ from {label} registered original")
        except (ValueError, OSError) as exc:
            errors.append(f"{path}: {exc}")

    return {"documents": checked, "base": base_sha, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="Compare current worktree with this Git commit/ref (for example origin/main)")
    args = parser.parse_args()
    report = validate(Path(__file__).resolve().parents[1], args.base)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(report["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
