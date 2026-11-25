"""The state store (STO-01, STO-02, STO-07): one SQLite file with the application's state.

- ``state``: opening the file and the transactions on it.
- ``migrate``: the numbered migration files in ``migrations/`` and how they are applied.
- ``backup``: a consistent copy of the file, also while the backend runs; ``daily``: one a day.
- ``settings``: the first table. ``login``: the password hash and the sessions (WP-0.7).

Decided 2026-11-25 (owner, WP-0.6 review); requirements, section 3, hold the reasons. Rules for
every table:

- A package brings its own tables, in its own migration file. A file that was applied anywhere
  is never changed: a correction is a new file.
- Tables are STRICT. An instant is an INTEGER of microseconds since 1970 in UTC, in a column
  whose name ends in ``_at_us`` (GEN-10, ``s3t.instants``). A calendar date is ISO text.
- Nothing outside this package opens a SQLite file, so the application's state is in one file
  (a test of the repository holds it). Functions on a table take the connection and are run by
  :meth:`~s3t.store.state.StateStore.read` and :meth:`~s3t.store.state.StateStore.write`.
- No secret of the broker goes into a table (GEN-02).
"""
