import pytest

from frutlups import __version__
from frutlups.cli import main


def test_version() -> None:
    assert __version__ == "0.3.0"


@pytest.mark.parametrize("argv", [[], ["unknown"]])
def test_invalid_verb_prints_usage_and_exits_2(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as error:
        main(argv)

    assert error.value.code == 2
    assert capsys.readouterr().out == "usage: frutlups {preflight,run,status}\n"
