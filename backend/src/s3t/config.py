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
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

ENV_VAR = "S3T_CONFIG"
DEFAULT_PATH = Path("config") / "s3t.toml"
EXAMPLE_NAME = "config/s3t.example.toml"

_HOST_LABEL = re.compile(r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)")
_RECORDING = re.compile(r"[A-Za-z0-9_-]+")


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
    """The name the browser uses to reach the backend. With it, that name is an accepted origin
    beside the listen address (SEC-02)."""

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

    @property
    def origins(self) -> frozenset[str]:
        """The origins a browser states for a page of this backend, as it writes them: lower
        case, no final dot, an IPv6 address in brackets, and no port where it is the default.
        Plain HTTP only: TLS comes with the phone step (architecture note, section 6)."""
        address = self.listen_address
        hosts = [f"[{address}]" if ":" in address else address]
        if self.host_name is not None:
            hosts.append(self.host_name.rstrip(".").lower())
        port = "" if self.port == 80 else f":{self.port}"
        return frozenset(f"http://{host}{port}" for host in hosts)


class PathsConfig(_Section):
    env_file: Path
    """The git-ignored environment file with the broker secrets."""
    data_dir: Path
    """Downloads and local recordings. Inside the repository it must be git-ignored (GEN-07)."""
    state_file: Path
    """The SQLite state store (STO-01)."""
    backup_dir: Path | None = None
    """Where copies of the state store go (STO-07); ``backups`` beside it when not given."""
    t3_root: Path | None = None
    """The optional read-only T3 store (STO-04)."""
    frontend_dir: Path | None = None
    """The built frontend that the backend serves (SEC-07); ``frontend/dist`` of the repository
    the backend runs from when not given."""

    @property
    def recordings_dir(self) -> Path:
        """Local recordings of broker sessions (implementation plan 3.2)."""
        return self.data_dir / "recordings"

    @property
    def backups(self) -> Path:
        """Where copies of the state store go."""
        return self.backup_dir or self.state_file.parent / "backups"

    @property
    def rates_dir(self) -> Path:
        """The downloaded rate series (REF-01)."""
        return self.data_dir / "rates"


class BrokerConfig(_Section):
    """Which broker the backend runs on (GEN-11), and the REST client's request limit (BRK-04,
    decided 2026-11-22, WP-0.3). The broker's real limits are not published or measured yet
    (WP-0.9). The defaults are conservative: the probe of note B ran at one call per second
    without a refusal.

    ``recording`` and ``fake_start`` may stay in the file while another source is chosen, so that
    changing the source is a change of one line. Proposed 2026-11-25 (WP-0.7).
    """

    source: Literal["live", "fake", "replay"] = "live"
    recording: str | None = None
    """Replay only: the name of a recording below ``data_dir/recordings``."""
    fake_start: datetime | None = None
    """Fake only: the instant at which the synthetic market's clock starts; the wall clock when
    not given. The synthetic market moves in the regular session only."""
    account_nickname: str | None = None
    """What the app shows for the account, never its number (GEN-03)."""
    rest_rate: float = Field(default=2.0, gt=0, le=50)
    """Requests per second, on average."""
    rest_burst: int = Field(default=4, ge=1, le=100)
    """Requests that may go at once after a quiet spell."""

    @field_validator("recording")
    @classmethod
    def _plain_name(cls, value: str | None) -> str | None:
        if value is not None and not _RECORDING.fullmatch(value):
            raise ValueError("must be a name of letters, digits, '-' and '_', not a path")
        return value

    @field_validator("fake_start")
    @classmethod
    def _an_instant(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("must state its offset, for example 2026-11-20T15:00:00Z")
        return value

    @field_validator("account_nickname")
    @classmethod
    def _not_the_number(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or len(re.findall(r"\d", value)) >= 4):
            raise ValueError("must be a name, and not the account number")
        return value

    @model_validator(mode="after")
    def _replay_names_its_recording(self) -> "BrokerConfig":
        if self.source == "replay" and self.recording is None:
            raise ValueError("the replay needs the name of a recording")
        return self


class StreamConfig(_Section):
    """The streaming session (BRK-02, BRK-03). Decided 2026-11-23 (owner, WP-0.4 review)."""

    aggregation_period: float = Field(default=0.5, ge=0.1, le=60)
    """Seconds. The period asked of the feed; the server grants it or its own."""
    stall_after: float = Field(default=60.0, ge=5, le=3600)
    """Seconds without data, while the market is open and something is subscribed, after
    which the session is taken for stalled and dropped (BRK-03)."""


class SubscriptionsConfig(_Section):
    """The subscription budget (SUB-02). Decided 2026-11-23 (owner, WP-0.4 review).

    ``limit`` is the application's own: 11,000, well below the published 25,000 (note B,
    section 7), so that the broker's own app and the owner's other programs have room on the
    same account. It is the session's hard cap. The manager grants up to ``limit`` less
    ``reserve``: 10,000. Nothing draws on the reserve; it is the distance between what the
    manager grants and where the session refuses. The candle and change limits are the
    published ones. The budget counts symbol and event pairs, the cautious reading until
    WP-0.9 has measured how the broker counts.
    """

    limit: int = Field(default=11_000, ge=1)
    reserve: int = Field(default=1_000, ge=0)
    candle_limit: int = Field(default=100, ge=0)
    candle_reserve: int = Field(default=5, ge=0)
    changes_per_minute: int = Field(default=10_000, ge=100)
    change_reserve: int = Field(default=2_000, ge=0)

    @model_validator(mode="after")
    def _reserves_fit(self) -> "SubscriptionsConfig":
        if self.reserve >= self.limit:
            raise ValueError("reserve must be below limit")
        if self.candle_reserve > self.candle_limit:
            raise ValueError("candle_reserve must not exceed candle_limit")
        if self.changes_per_minute - self.change_reserve < 100:
            raise ValueError("changes_per_minute less change_reserve must leave at least 100")
        return self

    @property
    def budget(self) -> int:
        """Subscriptions the manager may hold."""
        return self.limit - self.reserve

    @property
    def candle_budget(self) -> int:
        return self.candle_limit - self.candle_reserve

    @property
    def change_budget(self) -> int:
        """Subscription changes allowed in any 60 s."""
        return self.changes_per_minute - self.change_reserve


class CalendarConfig(_Section):
    """What the exchange calendar cannot know (REF-04). Decided 2026-11-25 (WP-0.6)."""

    closures: list[date] = Field(default_factory=list)
    """Days on which the exchanges are closed and the installed calendar does not say so. A
    closure is announced about ten days ahead, and a release of the calendar package that knows
    it is refused for two weeks (requirements, section 3, the version rule)."""


class RatesConfig(_Section):
    """The rate curve (REF-01). Decided 2026-11-25 (WP-0.6)."""

    download: bool = True
    """Whether the series are downloaded from FRED. Without it, what was downloaded before is
    used, and the flat rate after that."""
    refresh_hours: float = Field(default=12.0, ge=1, le=168)
    """Hours from a complete download to the next one."""
    max_age_days: int = Field(default=10, ge=3, le=366)
    """A series whose latest value is older than this many days is left out of the curve."""
    flat_rate: float = Field(default=0.04, ge=-0.05, le=0.5)
    """The fallback: one continuously compounded rate, as a fraction (0.04 is 4%)."""


class LoginConfig(_Section):
    """The login (SEC-01). Proposed 2026-11-25 (WP-0.7)."""

    session_days: int = Field(default=30, ge=1, le=365)
    """Days from a login to the end of its session."""


class StoreConfig(_Section):
    """The state store (STO-07). Proposed 2026-11-25 (WP-0.7)."""

    daily_copies: int = Field(default=14, ge=0, le=366)
    """Daily copies of the state file that are kept; 0 makes none."""


class Config(_Section):
    server: ServerConfig
    paths: PathsConfig
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    stream: StreamConfig = Field(default_factory=StreamConfig)
    subscriptions: SubscriptionsConfig = Field(default_factory=SubscriptionsConfig)
    calendar: CalendarConfig = Field(default_factory=CalendarConfig)
    rates: RatesConfig = Field(default_factory=RatesConfig)
    login: LoginConfig = Field(default_factory=LoginConfig)
    store: StoreConfig = Field(default_factory=StoreConfig)
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
