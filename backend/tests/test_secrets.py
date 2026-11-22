"""The environment file and log redaction (GEN-02, GEN-03, SEC-05)."""

import io
import logging
from pathlib import Path

import pytest

from s3t.secrets import (
    ACCOUNT_MASK,
    MASK,
    REDACTOR,
    Redactor,
    SecretsError,
    install_redaction,
    load_broker_secrets,
    parse_env,
    redact,
)

SECRET = "env-canary-secret-31f0c2"
REFRESH = "env-canary-refresh-77ad5e"


def write_env(tmp_path: Path, text: str, mode: int = 0o600) -> Path:
    path = tmp_path / "broker.env"
    path.write_text(text)
    path.chmod(mode)
    return path


def test_parse_env_forms() -> None:
    text = (
        "# comment\n\nA=1\nexport B = two \nC='quoted # not a comment'\n"
        'D="double"\nE=plain # trailing comment\nF=\n'
    )
    assert parse_env(text) == {
        "A": "1",
        "B": "two",
        "C": "quoted # not a comment",
        "D": "double",
        "E": "plain",
        "F": "",
    }


@pytest.mark.parametrize("line", [f"no equals {SECRET}", f"1BAD={SECRET}", f"=={SECRET}"])
def test_a_malformed_line_is_reported_by_number_only(line: str) -> None:
    with pytest.raises(SecretsError) as info:
        parse_env(f"A=1\n{line}\n")
    assert "line 2" in str(info.value)
    assert SECRET not in str(info.value)


def test_load_and_register(tmp_path: Path) -> None:
    env = write_env(
        tmp_path, f"TASTY_CLIENT_SECRET={SECRET}\nTASTY_REFRESH_TOKEN={REFRESH}\nOTHER=x\n"
    )
    secrets = load_broker_secrets(env)
    assert secrets.client_secret.get_secret_value() == SECRET
    assert secrets.sandbox is False
    assert SECRET not in repr(secrets) and REFRESH not in str(secrets)
    assert redact(f"a {SECRET} b {REFRESH}") == f"a {MASK} b {MASK}"


@pytest.mark.parametrize(("flag", "sandbox"), [("true", True), ("1", True), ("false", False)])
def test_sandbox_flag(tmp_path: Path, flag: str, sandbox: bool) -> None:
    env = write_env(
        tmp_path,
        f"TASTY_CLIENT_SECRET={SECRET}\nTASTY_REFRESH_TOKEN={REFRESH}\nTASTY_IS_TEST={flag}\n",
    )
    assert load_broker_secrets(env).sandbox is sandbox


def test_bad_sandbox_flag(tmp_path: Path) -> None:
    env = write_env(
        tmp_path, f"TASTY_CLIENT_SECRET={SECRET}\nTASTY_REFRESH_TOKEN={REFRESH}\nTASTY_IS_TEST=y\n"
    )
    with pytest.raises(SecretsError, match="TASTY_IS_TEST"):
        load_broker_secrets(env)


def test_missing_variables_are_named(tmp_path: Path) -> None:
    env = write_env(tmp_path, f"TASTY_CLIENT_SECRET={SECRET}\nTASTY_REFRESH_TOKEN=\n")
    with pytest.raises(SecretsError) as info:
        load_broker_secrets(env)
    assert "TASTY_REFRESH_TOKEN" in str(info.value)
    assert "TASTY_CLIENT_SECRET" not in str(info.value)
    assert SECRET not in str(info.value)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SecretsError, match="no environment file"):
        load_broker_secrets(tmp_path / "absent.env")


def test_open_file_mode_draws_a_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    body = f"TASTY_CLIENT_SECRET={SECRET}\nTASTY_REFRESH_TOKEN={REFRESH}\n"
    with caplog.at_level(logging.WARNING, logger="s3t.secrets"):
        load_broker_secrets(write_env(tmp_path, body, 0o600))
        assert not caplog.records
        load_broker_secrets(write_env(tmp_path, body, 0o644))
    assert "chmod 600" in caplog.text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Authorization: Bearer abc.def-ghi", f"Authorization: Bearer {MASK}"),
        ('{"access_token": "xyz", "a": 1}', f'{{"access_token": "{MASK}", "a": 1}}'),
        ('{"token":"dx-123"}', f'{{"token":"{MASK}"}}'),
        ("client_secret=abc&x=1", f"client_secret={MASK}&x=1"),
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2ln", MASK),
        ("GET /accounts/5WZ99173/positions", f"GET /accounts/{ACCOUNT_MASK}/positions"),
        ("scope read accounts:5WZ99173:owner", f"scope read accounts:{ACCOUNT_MASK}:owner"),
        ("GET /instruments/equities/SPY", "GET /instruments/equities/SPY"),
    ],
)
def test_patterns(text: str, expected: str) -> None:
    assert Redactor().redact(text) == expected


def test_registered_values() -> None:
    r = Redactor()
    r.register("short")  # below the minimum length: ignored
    r.register("abcdef-secret")
    r.register_account("5WZ12345")
    assert r.redact("short abcdef-secret 5WZ12345") == f"short {MASK} {ACCOUNT_MASK}"


def test_transient_values_are_bounded() -> None:
    r = Redactor()
    first = "transient-token-first"
    r.register(first, transient=True)
    assert r.redact(first) == MASK
    for i in range(Redactor.TRANSIENT_KEEP):
        r.register(f"transient-token-{i:03d}", transient=True)
    assert r.redact(first) == first  # dropped; long expired by then
    assert r.redact("transient-token-031") == MASK


def test_filter_redacts_message_arguments_exceptions_and_stacks() -> None:
    REDACTOR.register("filter-canary-value")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    install_redaction(handler)
    install_redaction(handler)  # idempotent
    assert len(handler.filters) == 1
    log = logging.getLogger("s3t.test.filter")
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    try:
        log.info("value %s", "filter-canary-value")
        log.info("dict %(v)s", {"v": "filter-canary-value"})
        try:
            raise ValueError("filter-canary-value")
        except ValueError:
            log.exception("failed")
        log.info("stack", stack_info=True)
        log.info("broken %s %s", "filter-canary-value")  # too few arguments
    finally:
        log.removeHandler(handler)
    text = stream.getvalue()
    assert "filter-canary-value" not in text
    assert "ValueError" in text and "unformattable" in text


def test_install_on_root_handlers() -> None:
    # Only this handler on the root logger, so pytest's own handlers stay untouched.
    handler = logging.StreamHandler(io.StringIO())
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers = [handler]
    try:
        install_redaction()
        assert any(type(f).__name__ == "RedactingFilter" for f in handler.filters)
    finally:
        root.handlers = saved
