"""The broker module: the one way the backend reaches a broker (GEN-09).

`interface` lists everything the application asks of a broker, and `instruments` holds the
instrument model its reads return. `tastytrade` is the implementation for Tastytrade,
read-only by construction (GEN-01): the REST client, the reading of its documents and the
DXLink streaming session. `fake` is a synthetic market served over the same REST and stream
shapes, for the tests and for work without the broker (implementation plan, 3.2 and 3.3).
`recording` writes a session to a local recording and `replay` serves one back (BRK-05).
"""
