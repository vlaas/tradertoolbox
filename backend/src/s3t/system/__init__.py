"""The process as a whole: its parts put together, and the services about itself (WP-0.7).

- ``backend``: the composition. It builds every part in the right order and closes them.
- ``login``: the password and the sessions (SEC-01).
- ``features``: the data tier every feature needs, and whether it is there (GEN-04).
- ``health``: the state of everything (SEC-08).

A service is a plain function or method with typed input and output. The REST routes
(``s3t.api``) and the MCP tools (R4) are thin wrappers over it (API-01), so nothing here knows
them. A test of the repository holds that.
"""
