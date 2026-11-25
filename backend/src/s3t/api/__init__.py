"""The HTTP and WebSocket API (WP-0.7).

- ``app``: the web application. ``guard``: who may reach it. ``frontend``: the built frontend.
  ``server``: the one socket and uvicorn.
- ``routes``: the REST routes, thin wrappers over services (API-01).
- ``channel``: the messages of the browser's connection, whatever carries them. ``topics``: what can
  be subscribed. ``status`` and ``rows``: their messages. ``ws``: the WebSocket, the one carrier.

Nothing here computes a number: no module of ``pricing`` is imported (GEN-05; a test of the
repository holds it).
"""
