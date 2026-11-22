"""Configuration loading (GEN-08).

Machine-specific values (the listen address, the host name, paths to the environment file and
the stores, the T3 location) come from one TOML file, never from code. The documented example
is ``config/s3t.example.toml`` in the repository.

The file is found in this order: the path given to :func:`load_config`, the ``S3T_CONFIG``
environment variable, ``config/s3t.toml`` below the current directory.

Relative paths in the file are resolved against the file's own directory, and ``~`` is
expanded, so the result does not depend on where the backend was started. Unknown keys and
values of the wrong type are refused, so a misspelt key fails instead of being ignored.

Broker secrets are not part of this file. It only names the environment file that holds them.
"""

import ipaddress
import os
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

ENV_VAR = "S3T_CONFIG"
DEFAULT_PATH = Path("config") / "s3t.toml"
EXAMPLE_NAME = "config/s3t.example.toml"

_HOST_LABEL = re.compile(r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)")


class ConfigError(Exception):
    """The configuration file is missing, unreadable or invalid."""


class _Section(BaseModel):
    # Strict: a quoted number or a number where a path belongs is an error, not a conversion.
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ServerConfig(_Section):
    listen_address: str
    """One IP address to listen on; never a wildcard address (prepares SEC-03)."""
    port: int = Field(ge=1, le=65535)
    host_name: str | None = None
    """The name the browser uses to reach the backend; checked as the origin later."""

    @field_validator("listen_address")
    @classmethod
    def _one_address(cls, value: str) -> str:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            raise ValueError("must be an IP address literal, not a host name") from None
        if address.is_unspecified:
            raise ValueError("must be one address, not a wildcard that listens on all")
        return str(address)

    @field_validator("host_name")
    @classmethod
    def _host_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        labels = value.rstrip(".").split(".")
        if len(value) > 253 or not all(_HOST_LABEL.fullmatch(label) for label in labels):
            raise ValueError("must be a plain host name, without scheme, port or path")
        return value


class PathsConfig(_Section):
    env_file: Path
    """The git-ignored environment file with the broker secrets."""
    data_dir: Path
    """Downloads and local recordings. Inside the repository it must be git-ignored (GEN-07)."""
    state_file: Path
    """The SQLite state store (STO-01)."""
    t3_root: Path | None = None
    """The optional read-only T3 store (STO-04)."""


class BrokerConfig(_Section):
    """The REST client's request limit (BRK-04). Decided 2026-11-22 (WP-0.3).

    The broker's real limits are not published or measured yet (WP-0.9). The defaults are
    conservative: the probe of note B ran at one call per second without a refusal.
    """

    rest_rate: float = Field(default=2.0, gt=0, le=50)
    """Requests per second, on average."""
    rest_burst: int = Field(default=4, ge=1, le=100)
    """Requests that may go at once after a quiet spell."""


class Config(_Section):
    server: ServerConfig
    paths: PathsConfig
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    source: Path
    """The file this configuration was read from."""


def find_config(path: str | Path | None = None, environ: Mapping[str, str] | None = None) -> Path:
    """Return the configuration file to read, by the order in the module docstring."""
    if path is not None:
        return Path(path)
    env = os.environ if environ is None else environ
    if env.get(ENV_VAR):
        return Path(env[ENV_VAR])
    return DEFAULT_PATH


def load_config(path: str | Path | None = None, environ: Mapping[str, str] | None = None) -> Config:
    """Read, resolve and validate the configuration file."""
    source = find_config(path, environ).expanduser().absolute()
    try:
        with source.open("rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(
            f"no configuration file at {source}; copy {EXAMPLE_NAME} there, "
            f"or name the file in {ENV_VAR}"
        ) from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{source}: not valid TOML: {e}") from None

    _resolve_paths(raw, source.parent)
    try:
        return Config.model_validate({**raw, "source": source})
    except ValidationError as e:
        raise ConfigError(f"{source}: {_describe(e)}") from None


def _resolve_paths(raw: dict[str, Any], base: Path) -> None:
    paths = raw.get("paths")
    if not isinstance(paths, dict):
        return
    for key, value in paths.items():
        if isinstance(value, str) and key in PathsConfig.model_fields:
            p = Path(value).expanduser()
            paths[key] = p if p.is_absolute() else base / p


def _describe(error: ValidationError) -> str:
    # Field location and reason only. The offending input is left out, because a value from
    # a misplaced line could be a secret.
    parts = []
    for item in error.errors(include_input=False, include_url=False):
        where = ".".join(str(x) for x in item["loc"]) or "file"
        parts.append(f"{where}: {item['msg']}")
    return "; ".join(parts)
