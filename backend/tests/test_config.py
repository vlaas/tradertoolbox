"""Configuration loading (GEN-08)."""

import re
from pathlib import Path

import pytest

from s3t.config import (
    DEFAULT_PATH,
    ENV_VAR,
    ConfigError,
    PathsConfig,
    ServerConfig,
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


@pytest.mark.parametrize("section", [ServerConfig, PathsConfig])
def test_example_documents_every_key(section: type[ServerConfig] | type[PathsConfig]) -> None:
    text = EXAMPLE.read_text()
    for key in section.model_fields:
        assert re.search(rf"^#?\s*{key}\s*=", text, re.MULTILINE), f"{key} not in the example"


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
