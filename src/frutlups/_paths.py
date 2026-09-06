"""Shared repository-relative path and record access helpers."""

from collections.abc import Mapping
from pathlib import Path


def safe_rel(
    value: object, *, directory: bool = False, error: type[ValueError] = ValueError,
) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        raise error(f"unsafe repository-relative path: {value!r}")
    raw = value[:-1] if directory and value.endswith("/") else value
    parts = raw.split("/")
    unsafe = (
        not raw or raw.startswith("/") or ":" in parts[0]
        or any(part in ("", ".", "..") for part in parts)
    )
    if unsafe:
        raise error(f"unsafe repository-relative path: {value!r}")
    return raw + ("/" if directory else "")


def repo_path(
    root: Path, relative: str, *, error: type[ValueError] = ValueError,
) -> Path:
    root = root.resolve()
    relative = safe_rel(relative, error=error)
    lexical = root / relative
    try:
        lexical.resolve().relative_to(root)
    except ValueError as exc:
        raise error(f"path escapes repository: {relative}") from exc
    return lexical


def value(item: object, key: str, default: object = None) -> object:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)
