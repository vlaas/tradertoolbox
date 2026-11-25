"""Broker secrets and log redaction (GEN-02, GEN-03, SEC-05).

The broker secrets live only in the environment file that the configuration names
(``paths.env_file``). It is a plain dotenv file with these variables, the names of
``prototypes/chart/README.md``:

    TASTY_CLIENT_SECRET   the OAuth client secret of the Tastytrade API application
    TASTY_REFRESH_TOKEN   the OAuth refresh token of the personal grant
    TASTY_IS_TEST         optional; true uses the sandbox host

The values are held as :class:`pydantic.SecretStr`, whose ``repr`` and ``str`` hide them. An
error about the file names the variable or the line number, never a value.

Redaction. Every secret the backend loads or receives is registered with :data:`REDACTOR`.
:class:`RedactingFilter`, installed on a logging handler with :func:`install_redaction`,
replaces registered values and token-shaped text in the message, its arguments, the exception
text and the stack text of every record that the handler emits. Account numbers in broker
paths (``/accounts/<number>``) and in OAuth scopes (``accounts:<number>:``) are masked as
``<acct>`` (GEN-03).

Decided 2026-11-22: the variable names, the sandbox switch in this file, and a warning (not a
refusal) for a file that group or others can read; the redaction patterns and the
full mask of account numbers.
"""

import logging
import re
import stat
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr

CLIENT_SECRET = "TASTY_CLIENT_SECRET"
REFRESH_TOKEN = "TASTY_REFRESH_TOKEN"
IS_TEST = "TASTY_IS_TEST"

MASK = "***"
ACCOUNT_MASK = "<acct>"

LOG = logging.getLogger(__name__)

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"", "0", "false", "no", "off"}


class SecretsError(Exception):
    """The environment file is missing, unreadable or incomplete."""


@dataclass(frozen=True)
class BrokerSecrets:
    client_secret: SecretStr
    refresh_token: SecretStr
    sandbox: bool = False


def parse_env(text: str) -> dict[str, str]:
    """Parse dotenv text: ``NAME=value`` lines, ``#`` comments, an optional ``export``.

    A value may be quoted with single or double quotes; an unquoted value ends at `` #``.
    Escapes and variable expansion are not supported. A malformed line is reported by its
    number only, because its content could be a secret.
    """
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        name, sep, value = line.partition("=")
        name = name.strip()
        if not sep or not _NAME.fullmatch(name):
            raise SecretsError(f"line {number}: not of the form NAME=value")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        values[name] = value
    return values


def load_broker_secrets(path: Path) -> BrokerSecrets:
    """Read the broker secrets from the environment file and register them for redaction."""
    try:
        mode = path.stat().st_mode
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SecretsError(f"no environment file at {path}") from None
    except OSError as e:
        raise SecretsError(f"environment file {path}: cannot be read ({e.strerror})") from None
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        LOG.warning(
            "environment file %s can be read by group or others; restrict it with chmod 600",
            path,
        )
    values = parse_env(text)
    missing = [n for n in (CLIENT_SECRET, REFRESH_TOKEN) if not values.get(n)]
    if missing:
        raise SecretsError(f"environment file {path}: missing {', '.join(missing)}")
    flag = values.get(IS_TEST, "").strip().lower()
    if flag not in _TRUE | _FALSE:
        raise SecretsError(f"environment file {path}: {IS_TEST} must be true or false")
    REDACTOR.register(values[CLIENT_SECRET])
    REDACTOR.register(values[REFRESH_TOKEN])
    return BrokerSecrets(
        client_secret=SecretStr(values[CLIENT_SECRET]),
        refresh_token=SecretStr(values[REFRESH_TOKEN]),
        sandbox=flag in _TRUE,
    )


_SECRET_KEYS = (
    "access_token|access-token|refresh_token|refresh-token|client_secret|client-secret"
    "|id_token|id-token|session-token|remember-token|token|password"
)
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # JSON members and form or query fields that carry a secret.
    (re.compile(rf"(\"(?:{_SECRET_KEYS})\"\s*:\s*)\"[^\"]*\"", re.I), rf'\1"{MASK}"'),
    (re.compile(rf"(\b(?:{_SECRET_KEYS})=)[^&\s\"']+", re.I), rf"\1{MASK}"),
    (re.compile(r"(\bBearer\s+)[A-Za-z0-9._~+/=-]+", re.I), rf"\1{MASK}"),
    # A JSON Web Token: three base64url parts, the first an encoded JSON object.
    (re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*"), MASK),
    (re.compile(r"(/accounts/)[^/?#\s\"']+", re.I), rf"\1{ACCOUNT_MASK}"),
    (re.compile(r"(\baccounts:)[^:\s\"']+(:)"), rf"\1{ACCOUNT_MASK}\2"),
)


class Redactor:
    """Replaces registered secret values and token-shaped text.

    Values registered without a kind (the environment secrets) and account numbers stay for
    the life of the process. Tokens that the broker issues at run time are registered with
    their kind (``access``, ``id``, ``quote``), and the last :attr:`KEEP_PER_KIND` of each
    kind are kept. So a process that runs for weeks does not grow (PRF-09), and a quote token
    that lives 24 hours stays masked however many access tokens follow it. Decided
    2026-11-22: kept per kind, because one shared list would drop a valid quote token after
    about eight hours of use. Older access and id tokens have expired, and they are
    JWT-shaped, so the pattern still masks them.
    """

    MIN_LENGTH = 6
    KEEP_PER_KIND = 4

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._permanent: dict[str, str] = {}
        self._recent: dict[str, deque[str]] = {}

    def register(self, value: str | None, *, kind: str | None = None) -> None:
        """Mask this value in everything redacted from now on. Short values are ignored.

        Without ``kind`` the value is kept for good; with it, among the latest of its kind.
        """
        if not value or len(value) < self.MIN_LENGTH:
            return
        with self._lock:
            if kind is None:
                self._permanent[value] = MASK
                return
            recent = self._recent.setdefault(kind, deque(maxlen=self.KEEP_PER_KIND))
            if value not in recent:
                recent.append(value)

    def register_account(self, number: str | None) -> None:
        """Mask an account number as ``<acct>`` wherever it appears (GEN-03)."""
        if number and len(number) >= 4:
            with self._lock:
                self._permanent[number] = ACCOUNT_MASK

    def redact(self, text: str) -> str:
        with self._lock:
            values = [
                *self._permanent.items(),
                *((v, MASK) for recent in self._recent.values() for v in recent),
            ]
        # Longest first, so a value that contains another is replaced whole.
        for value, mask in sorted(values, key=lambda item: len(item[0]), reverse=True):
            if value in text:
                text = text.replace(value, mask)
        for pattern, replacement in _PATTERNS:
            text = pattern.sub(replacement, text)
        return text


REDACTOR = Redactor()
"""The process-wide redactor that the broker client and the logging filter use."""


def redact(text: str) -> str:
    return REDACTOR.redact(text)


class RedactingFilter(logging.Filter):
    """A handler filter that redacts each record before it is formatted (SEC-05).

    The record is changed in place: the message is formatted with its arguments and
    redacted, and the exception and stack text are formatted and redacted ahead of the
    formatter, which then uses the cached text.
    """

    _formatter = logging.Formatter()

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a broken format string; logging would report it with the args
            message = f"{record.msg!r} (unformattable arguments)"
        record.msg = redact(message)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = self._formatter.formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def install_redaction(*handlers: logging.Handler) -> None:
    """Install :class:`RedactingFilter` on the handlers, or on all handlers of the root logger.

    Handlers added later, including those of libraries that do not propagate to the root
    logger (uvicorn's, for example), need their own call.
    """
    for handler in handlers or tuple(logging.getLogger().handlers):
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())
