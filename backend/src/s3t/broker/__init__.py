"""The broker module: the one way the backend reaches a broker (GEN-09).

`interface` lists everything the application asks of a broker. `tastytrade` is the
implementation for Tastytrade, read-only by construction (GEN-01). `fake` is a synthetic
market served over the same REST shapes, for the tests and for work without the broker
(implementation plan, 3.2 and 3.3).
"""
