"""Strict loading for committed and machine-local frutlups configuration."""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from types import MappingProxyType

from ._paths import safe_rel

MAIN_KEYS = {
    "schema", "until", "max_corrective_rounds", "max_jobs", "max_wall_minutes",
    "commit_on_accept", "prompt_dir", "review_prompt_dir", "reviews_dir", "ledger",
    "seats", "timeouts",
}
LOCAL_KEYS = {"schema", "pi", "claude", "git", "llloom", "path_dirs", "env_passthrough"}
SEAT_KEYS = {"adapter", "provider", "model", "effort", "corrective_effort"}
TIMEOUT_KEYS = {"coder_seconds", "reviewer_seconds", "verification_seconds"}


class ConfigError(ValueError):
    """A configuration file is missing or violates its schema."""


@dataclass(frozen=True)
class SeatConfig:
    adapter: str
    model: str
    effort: str
    provider: str | None = None
    corrective_effort: str | None = None


@dataclass(frozen=True)
class Timeouts:
    coder_seconds: int
    reviewer_seconds: int
    verification_seconds: int


@dataclass(frozen=True)
class Config:
    schema: str
    until: str
    max_corrective_rounds: int
    max_jobs: int
    max_wall_minutes: int
    commit_on_accept: bool
    prompt_dir: str
    review_prompt_dir: str
    reviews_dir: str
    ledger: str
    seats: Mapping[str, SeatConfig]
    timeouts: Timeouts
    pi: str
    claude: str
    git: str
    llloom: str | None
    path_dirs: tuple[str, ...]
    env_passthrough: tuple[str, ...]

    @classmethod
    def load(cls, root: Path) -> "Config":
        main = _load(root / "frutlups.toml")
        local = _load(root / "frutlups.local.toml")
        _reject_credentials(main)
        _reject_credentials(local)
        _keys(main, MAIN_KEYS, MAIN_KEYS, "frutlups")
        _keys(local, LOCAL_KEYS, LOCAL_KEYS - {"llloom"}, "frutlups.local")
        if main["schema"] != "frutlups/1":
            raise ConfigError("schema: must be frutlups/1")
        if local["schema"] != "frutlups.local/1":
            raise ConfigError("schema: must be frutlups.local/1")
        until = _choice(main["until"], "until", {"slice", "milestone", "roadmap"})
        integers = {
            key: _integer(main[key], key, zero=key == "max_corrective_rounds")
            for key in ("max_corrective_rounds", "max_jobs", "max_wall_minutes")
        }
        if not isinstance(main["commit_on_accept"], bool):
            raise ConfigError("commit_on_accept: must be boolean")
        paths = {}
        for key in ("prompt_dir", "review_prompt_dir", "reviews_dir", "ledger"):
            try:
                paths[key] = safe_rel(main[key], directory=str(main[key]).endswith("/"))
            except ValueError as exc:
                raise ConfigError(f"{key}: unsafe repository-relative path") from exc
        raw_seats = _mapping(main["seats"], "seats")
        if not raw_seats:
            raise ConfigError("seats: must not be empty")
        seats = {name: _seat(name, value) for name, value in raw_seats.items()}
        raw_timeouts = _mapping(main["timeouts"], "timeouts")
        _keys(raw_timeouts, TIMEOUT_KEYS, TIMEOUT_KEYS, "timeouts")
        timeouts = Timeouts(**{
            key: _integer(raw_timeouts[key], f"timeouts.{key}") for key in TIMEOUT_KEYS
        })
        executables = {
            key: _absolute(local[key], key) for key in ("pi", "claude", "git")
        }
        llloom = _absolute(local["llloom"], "llloom") if "llloom" in local else None
        path_dirs = tuple(
            _absolute(item, f"path_dirs[{index}]")
            for index, item in enumerate(_strings(local["path_dirs"], "path_dirs"))
        )
        env = tuple(_strings(local["env_passthrough"], "env_passthrough"))
        return cls(
            main["schema"], until, **integers, commit_on_accept=main["commit_on_accept"],
            **paths, seats=MappingProxyType(seats), timeouts=timeouts, **executables,
            llloom=llloom, path_dirs=path_dirs, env_passthrough=env,
        )


def _load(path: Path) -> dict:
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{path.name}: {exc}") from exc
    return _mapping(data, path.name)


def _reject_credentials(value: object, prefix: str = "") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            name = f"{prefix}.{key}".lstrip(".")
            if key.lower().endswith(("_key", "_token")):
                raise ConfigError(f"{name}: credential keys are forbidden")
            _reject_credentials(item, name)
    elif isinstance(value, list):
        for item in value:
            _reject_credentials(item, prefix)


def _keys(data: dict, allowed: set[str], required: set[str], where: str) -> None:
    if unknown := sorted(set(data) - allowed):
        raise ConfigError(f"{where}.{unknown[0]}: unknown key")
    if missing := sorted(required - set(data)):
        raise ConfigError(f"{where}.{missing[0]}: missing required key")


def _mapping(value: object, key: str) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{key}: must be a mapping")
    return value


def _strings(value: object, key: str) -> list[str]:
    invalid = not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    )
    if invalid:
        raise ConfigError(f"{key}: must be a list of non-empty strings")
    return value


def _integer(value: object, key: str, *, zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < (0 if zero else 1):
        kind = "non-negative" if zero else "positive"
        raise ConfigError(f"{key}: must be a {kind} integer")
    return value


def _choice(value: object, key: str, choices: set[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ConfigError(f"{key}: must be one of {', '.join(sorted(choices))}")
    return value


def _absolute(value: object, key: str) -> str:
    absolute = isinstance(value, str) and (
        Path(value).is_absolute() or PureWindowsPath(value).is_absolute()
    )
    if not absolute:
        raise ConfigError(f"{key}: executable path must be absolute")
    return value


def _seat(name: str, value: object) -> SeatConfig:
    key = f"seats.{name}"
    data = _mapping(value, key)
    _keys(data, SEAT_KEYS, {"adapter", "model", "effort"}, key)
    adapter = _choice(data["adapter"], f"{key}.adapter", {"pi", "claude"})
    for field in ("model", "effort", "provider", "corrective_effort"):
        if field in data and (not isinstance(data[field], str) or not data[field]):
            raise ConfigError(f"{key}.{field}: must be a non-empty string")
    if adapter == "pi" and "provider" not in data:
        raise ConfigError(f"{key}.provider: missing required key")
    return SeatConfig(
        adapter, data["model"], data["effort"], data.get("provider"),
        data.get("corrective_effort"),
    )
