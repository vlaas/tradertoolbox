"""The built frontend, served by the backend (SEC-07)."""

from pathlib import Path

import pytest

from s3t.api.frontend import DEFAULT_DIR, Frontend, content_policy
from s3t.broker.fake.clock import VirtualClock

from .conftest import ApiRig, make_config, running

pytestmark = pytest.mark.anyio

PAGE = '<!doctype html><script type="module" src="./assets/index-1a2b.js"></script>'


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    directory = tmp_path / "dist"
    (directory / "assets").mkdir(parents=True)
    (directory / "index.html").write_text(PAGE)
    (directory / "assets" / "index-1a2b.js").write_text("export {}")
    (tmp_path / "secret.txt").write_text("outside")
    return directory


async def test_the_page_and_its_files(dist: Path, rig: ApiRig) -> None:
    """Without a session: the login screen is one of them."""
    page = await rig.http.get("/")
    assert page.status_code == 200 and page.text == PAGE
    assert page.headers["content-type"].startswith("text/html")
    assert page.headers["cache-control"] == "no-cache"
    script = await rig.http.get("/assets/index-1a2b.js")
    assert script.status_code == 200 and "javascript" in script.headers["content-type"]
    assert "immutable" in script.headers["cache-control"]
    assert (await rig.http.head("/")).status_code == 200
    again = await rig.http.get("/", headers={"if-none-match": page.headers["etag"]})
    assert again.status_code == 304


@pytest.mark.parametrize(
    "path",
    ["/nothing", "/assets/nothing.js", "/assets/", "/../secret.txt", "/%2e%2e/secret.txt"]
    + ["/assets/../../secret.txt", "/assets/..%2f..%2fsecret.txt", "/%00"],
)
async def test_there_is_nothing_else(dist: Path, rig: ApiRig, path: str) -> None:
    answer = await rig.http.get(path)
    assert answer.status_code == 404 and "outside" not in answer.text


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
async def test_files_are_only_read(dist: Path, rig: ApiRig, method: str) -> None:
    assert (await rig.http.request(method, "/")).status_code == 405


async def test_a_page_may_talk_to_this_backend_only(dist: Path, rig: ApiRig) -> None:
    for path in ("/", "/assets/index-1a2b.js", "/nothing"):
        headers = (await rig.http.get(path)).headers
        assert headers["content-security-policy"] == content_policy(
            ["http://127.0.0.1:8740", "http://trader.example.internal:8740"]
        )
        assert headers["x-content-type-options"] == "nosniff"
    policy = content_policy(["http://127.0.0.1:8740"])
    assert "connect-src 'self' ws://127.0.0.1:8740;" in policy
    assert "default-src 'self'" in policy and "frame-ancestors 'none'" in policy
    assert "script-src" not in policy  # so it is the default: no inline script


async def test_before_it_is_built(tmp_path: Path, clock: VirtualClock) -> None:
    """The backend runs, and says what is missing."""
    async with running(make_config(tmp_path), clock) as rig:
        answer = await rig.http.get("/")
        assert answer.status_code == 503 and "not built" in answer.text
        assert (await rig.http.get("/api/session")).status_code == 401
        (tmp_path / "dist").mkdir()
        (tmp_path / "dist" / "index.html").write_text(PAGE)
        assert (await rig.http.get("/")).text == PAGE  # without a restart


def test_the_default_is_the_build_of_the_repository() -> None:
    repo = Path(__file__).resolve().parents[3]
    assert repo / "frontend" / "dist" == DEFAULT_DIR
    assert Frontend(None, origins=[])._dir == DEFAULT_DIR
