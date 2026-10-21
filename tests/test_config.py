from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from frutlups.config import Config, ConfigError

EXAMPLE = Path(__file__).parent / "fixtures" / "v4_project" / "frutlups.toml"


def _local_config(tmp_path: Path) -> str:
    paths = {name: (tmp_path / name).as_posix() for name in ("pi", "claude", "git")}
    return f'''schema = "frutlups.local/1"
pi = "{paths["pi"]}"
claude = "{paths["claude"]}"
git = "{paths["git"]}"
path_dirs = ["{tmp_path.as_posix()}"]
env_passthrough = ["TEMP"]
'''


def _write_config(tmp_path: Path, main: str | None = None, local: str | None = None) -> None:
    (tmp_path / "frutlups.toml").write_text(
        EXAMPLE.read_text(encoding="utf-8") if main is None else main,
        encoding="utf-8",
    )
    (tmp_path / "frutlups.local.toml").write_text(
        _local_config(tmp_path) if local is None else local,
        encoding="utf-8",
    )


def test_loads_project_example_into_frozen_config(tmp_path: Path) -> None:
    _write_config(tmp_path)

    config = Config.load(tmp_path)

    assert config.schema == "frutlups/1"
    assert config.until == "milestone"
    assert config.max_corrective_rounds == 2
    assert config.seats["coder"].provider == "openai-codex"
    assert config.timeouts.verification_seconds == 1800
    assert config.path_dirs == (tmp_path.as_posix(),)
    with pytest.raises(FrozenInstanceError):
        config.until = "slice"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("main_change", "local_change", "key"),
    [
        (
            lambda text: text.replace(
                'schema = "frutlups/1"', 'schema = "frutlups/1"\nunknown_setting = true'
            ),
            None,
            "unknown_setting",
        ),
        (lambda text: text.replace('until = "milestone"\n', ""), None, "until"),
        (None, lambda text: text.replace('pi = "', 'pi = "relative/', 1), "pi"),
        (
            None,
            lambda text: text.replace(
                'schema = "frutlups.local/1"', 'schema = "frutlups.local/1"\napi_token = "secret"'
            ),
            "api_token",
        ),
    ],
    ids=("unknown-key", "missing-required-key", "relative-executable", "credential-key"),
)
def test_refuses_invalid_config_and_names_key(
    tmp_path: Path, main_change, local_change, key: str
) -> None:
    main = EXAMPLE.read_text(encoding="utf-8")
    local = _local_config(tmp_path)
    _write_config(
        tmp_path,
        main_change(main) if main_change else main,
        local_change(local) if local_change else local,
    )

    with pytest.raises(ConfigError, match=key):
        Config.load(tmp_path)


def test_refuses_unknown_nested_key(tmp_path: Path) -> None:
    main = EXAMPLE.read_text(encoding="utf-8").replace(
        'adapter = "pi"', 'adapter = "pi"\nextra = true', 1
    )
    _write_config(tmp_path, main=main)

    with pytest.raises(ConfigError, match=r"seats\.coder\.extra"):
        Config.load(tmp_path)


@pytest.mark.parametrize(("table", "mib"), [
    ("", 2), ("\n[limits]\n", 2), ("\n[limits]\npi_record_mib = 2\n", 2),
    ("\n[limits]\npi_record_mib = 32\n", 32),
    ("\n[limits]\npi_record_mib = 8589934591\n", 8589934591),
])
def test_pi_record_limit_defaults_and_exact_representation(tmp_path, table, mib):
    _write_config(tmp_path, EXAMPLE.read_text(encoding="utf-8") + table)
    cfg = Config.load(tmp_path)
    assert cfg.pi_record_bytes == mib * 1024 * 1024
    assert cfg.pi_record_bytes <= 2**53 - 1


@pytest.mark.parametrize("table", [
    "[limits]\npi_record_mib = true", "[limits]\npi_record_mib = 0",
    "[limits]\npi_record_mib = -1", "[limits]\npi_record_mib = 1.5",
    '[limits]\npi_record_mib = "32"', "[limits]\npi_record_mib = 8589934592",
    "[limits]\nextra = 1", 'limits = "32"',
])
def test_invalid_pi_record_limit_refuses_before_dispatch(tmp_path, table):
    main = EXAMPLE.read_text(encoding="utf-8")
    if table.startswith("limits ="):
        main = main.replace('schema = "frutlups/1"', 'schema = "frutlups/1"\n' + table)
    else:
        main += "\n" + table + "\n"
    _write_config(tmp_path, main)
    with pytest.raises(ConfigError, match="limits"):
        Config.load(tmp_path)
