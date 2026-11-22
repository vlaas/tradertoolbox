"""The FastAPI application.

WP-0.1 needs an application only so that the OpenAPI schema and the TypeScript types can be
generated and checked (API-02, QA-05). It has no routes yet; WP-0.7 adds them.

The schema is generated offline by ``tools/generate.py`` and is not served: an
unauthenticated ``/openapi.json`` or docs page would break SEC-02.
"""

from fastapi import FastAPI

from s3t import __version__


def create_app() -> FastAPI:
    return FastAPI(
        title="Self-3-Trader",
        version=__version__,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
