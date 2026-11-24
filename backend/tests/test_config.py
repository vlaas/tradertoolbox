"""Configuration loading (GEN-08)."""

import re
from datetime import date
from pathlib import Path

import pytest
from pydantic import BaseModel

from s3t.config import (
    DEFAULT_PATH,
    ENV_VAR,
    BrokerConfig,
    CalendarConfig,
    Config,
    ConfigError,
    RatesConfig,
    find_config,
    load_config,
)

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "config" / "s3t.example.toml"

VALID = """
[server]
listen_address = "{address}"
port = 8740

[paths]
env_file = "secrets.env"
data_dir = "data"
state_file = "/abs/state.sqlite"
"""


def write(tmp_path: Path, text: str, name: str = "s3t.toml") -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


def test_example_file_loads() -> None:
    config = load_config(EXAMPLE)
    assert config.server.listen_address == "127.0.0.1"
    assert config.paths.env_file == (EXAMPLE.parent / "../.env")
    assert config.paths.t3_root is None


@pytest.mark.parametrize("section", sorted(set(Config.model_fields) - {"source"}))
def test_example_documents_every_key(section: str) -> None:
    """Every section, so that one added later is covered too. Keys are looked for below their
    own heading."""
    blocks = re.split(r"^\[(\w+)\]$", EXAMPLE.read_text(), flags=re.MULTILINE)
    text = dict(zip(blocks[1::2], blocks[2::2], strict=True))[section]
    model = Config.model_fields[section].annotation
    assert isinstance(model, type) and issubclass(model, BaseModel)
    for key in model.model_fields:
        assert re.search(rf"^#?\s*{key}\s*=", text, re.MULTILINE), f"{key} not in the example"


def test_the_examples_commented_keys_are_the_defaults(tmp_path: Path) -> None:
    """The optional sections with every comment mark removed from the keys still load, and a
    key that the example shows with its default has that default."""
    text = re.sub(r"^# (\w+ = )", r"\1", EXAMPLE.read_text(), flags=re.MULTILINE)
    shown, plain = load_config(write(tmp_path, text)), load_config(EXAMPLE)
    for section in ("broker", "stream", "subscriptions", "rates"):
        assert getattr(shown, section) == getattr(plain, section), section
    assert shown.calendar.closures == [date(2025, 1, 9)] and plain.calendar.closures == []
    assert shown.paths.backups.name == plain.paths.backups.name == "backups"


def test_search_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    given = tmp_path / "given.toml"
    from_env = tmp_path / "env.toml"
    assert find_config(given, {ENV_VAR: str(from_env)}) == given
    assert find_config(None, {ENV_VAR: str(from_env)}) == from_env
    assert find_config(None, {}) == DEFAULT_PATH

    (tmp_path / "config").mkdir()
    write(tmp_path / "config", VALID.format(address="127.0.0.1"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert load_config().source == tmp_path / "config" / "s3t.toml"


def test_relative_paths_follow_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write(tmp_path, VALID.format(address="127.0.0.1"))
    monkeypatch.chdir(REPO)  # elsewhere: the result must not depend on the current directory
    config = load_config(path)
    assert config.paths.env_file == tmp_path / "secrets.env"
    assert config.paths.data_dir == tmp_path / "data"
    assert config.paths.state_file == Path("/abs/state.sqlite")


def test_home_is_expanded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    text = VALID.format(address="127.0.0.1") + 't3_root = "~/t3"\n'
    config = load_config(write(tmp_path, text))
    assert config.paths.t3_root == tmp_path / "t3"


@pytest.mark.parametrize("address", ["192.0.2.10", "::1", "2001:db8::1"])
def test_one_address_is_accepted(tmp_path: Path, address: str) -> None:
    assert load_config(write(tmp_path, VALID.format(address=address))).server.listen_address


@pytest.mark.parametrize("address", ["0.0.0.0", "::", "0:0:0:0:0:0:0:0", "localhost", ""])
def test_wildcards_and_names_are_refused(tmp_path: Path, address: str) -> None:
    with pytest.raises(ConfigError, match="listen_address"):
        load_config(write(tmp_path, VALID.format(address=address)))


def test_unknown_key_is_refused(tmp_path: Path) -> None:
    text = VALID.format(address="127.0.0.1").replace("port =", "prot = 1\nport =")
    with pytest.raises(ConfigError, match=r"server\.prot"):
        load_config(write(tmp_path, text))


@pytest.mark.parametrize("port", ["0", "65536", '"8740"'])
def test_port_range(tmp_path: Path, port: str) -> None:
    text = VALID.format(address="127.0.0.1").replace("port = 8740", f"port = {port}")
    with pytest.raises(ConfigError, match="port"):
        load_config(write(tmp_path, text))


@pytest.mark.parametrize("name", ["trader.example.internal", "host-1"])
def test_host_name_accepted(tmp_path: Path, name: str) -> None:
    text = VALID.format(address="127.0.0.1").replace("port =", f'host_name = "{name}"\nport =')
    assert load_config(write(tmp_path, text)).server.host_name == name


@pytest.mark.parametrize("name", ["http://trader", "trader:8740", "trader/x", "-bad", ""])
def test_host_name_refused(tmp_path: Path, name: str) -> None:
    text = VALID.format(address="127.0.0.1").replace("port =", f'host_name = "{name}"\nport =')
    with pytest.raises(ConfigError, match="host_name"):
        load_config(write(tmp_path, text))


def test_missing_section(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="paths"):
        load_config(write(tmp_path, '[server]\nlisten_address = "127.0.0.1"\nport = 1\n'))


def test_missing_file_names_the_example(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="s3t.example.toml"):
        load_config(tmp_path / "absent.toml")


def test_invalid_toml(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(write(tmp_path, "[server\n"))


def test_errors_do_not_echo_values(tmp_path: Path) -> None:
    canary = "canary-value-7f3a"
    text = VALID.format(address="127.0.0.1").replace("port = 8740", f'port = "{canary}"')
    with pytest.raises(ConfigError) as info:
        load_config(write(tmp_path, text))
    assert canary not in str(info.value)


def test_broker_section_is_optional(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, VALID.format(address="127.0.0.1")))
    assert config.broker == BrokerConfig()
    assert (config.broker.rest_rate, config.broker.rest_burst) == (2.0, 4)


def test_broker_section(tmp_path: Path) -> None:
    text = VALID.format(address="127.0.0.1") + "\n[broker]\nrest_rate = 1\nrest_burst = 2\n"
    config = load_config(write(tmp_path, text))
    assert (config.broker.rest_rate, config.broker.rest_burst) == (1.0, 2)


@pytest.mark.parametrize("line", ["rest_rate = 0", "rest_burst = 0", 'rest_rate = "2"', "x = 1"])
def test_broker_section_refused(tmp_path: Path, line: str) -> None:
    text = VALID.format(address="127.0.0.1") + f"\n[broker]\n{line}\n"
    with pytest.raises(ConfigError, match="broker"):
        load_config(write(tmp_path, text))


def test_stream_and_subscription_sections(tmp_path: Path) -> None:
    base = (
        '[server]\nlisten_address = "127.0.0.1"\nport = 8740\n'
        '[paths]\nenv_file = ".env"\ndata_dir = "data"\nstate_file = "data/state.sqlite"\n'
    )
    file = tmp_path / "s3t.toml"
    file.write_text(base)
    config = load_config(file)
    assert config.stream.aggregation_period == 0.5 and config.stream.stall_after == 60.0
    subs = config.subscriptions
    assert (subs.budget, subs.candle_budget, subs.change_budget) == (10_000, 95, 8_000)
    assert config.paths.recordings_dir == tmp_path / "data" / "recordings"

    file.write_text(
        base + "[stream]\nstall_after = 30.0\n[subscriptions]\nlimit = 25000\nreserve = 5000\n"
    )
    config = load_config(file)
    assert config.stream.stall_after == 30.0 and config.subscriptions.budget == 20_000

    for section in (
        "[subscriptions]\nreserve = 11000\n",
        "[subscriptions]\ncandle_reserve = 101\n",
        "[subscriptions]\nchange_reserve = 9950\n",
        "[stream]\nstall_after = 1.0\n",
        "[stream]\nperiod = 1.0\n",
    ):
        file.write_text(base + section)
        with pytest.raises(ConfigError):
            load_config(file)


def test_calendar_and_rates_sections(tmp_path: Path) -> None:
    base = VALID.format(address="127.0.0.1")
    config = load_config(write(tmp_path, base))
    assert config.calendar == CalendarConfig() and config.rates == RatesConfig()
    assert config.paths.rates_dir == tmp_path / "data" / "rates"
    assert config.paths.backups == Path("/abs/backups")

    more = (
        "[calendar]\nclosures = [2027-01-09, 2027-03-01]\n"
        "[rates]\ndownload = false\nrefresh_hours = 24\nflat_rate = 0.035\n"
    )
    config = load_config(write(tmp_path, base + more))
    assert config.calendar.closures == [date(2027, 1, 9), date(2027, 3, 1)]
    assert (config.rates.download, config.rates.refresh_hours) == (False, 24.0)
    assert config.rates.flat_rate == 0.035

    with_dir = base.replace("[paths]", '[paths]\nbackup_dir = "copies"')
    assert load_config(write(tmp_path, with_dir)).paths.backups == tmp_path / "copies"


@pytest.mark.parametrize(
    "lines",
    [
        '[calendar]\nclosures = ["2027-01-09"]',
        "[calendar]\nclosures = [2027-01-09T00:00:00Z]",
        "[calendar]\nclosed = [2027-01-09]",
        "[rates]\nflat_rate = 4.0",  # a percentage where a fraction belongs
        "[rates]\nrefresh_hours = 0.1",
        '[rates]\ndownload = "no"',
    ],
)
def test_calendar_and_rates_sections_refused(tmp_path: Path, lines: str) -> None:
    with pytest.raises(ConfigError, match=r"calendar|rates"):
        load_config(write(tmp_path, VALID.format(address="127.0.0.1") + lines + "\n"))
