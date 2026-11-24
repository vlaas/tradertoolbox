"""Reference data: the exchange calendar (REF-04) and the rate curve (REF-01).

- ``calendar``: trading days and their sessions. ``exchange`` reads them from the library and is
  the only module that imports it.
- ``rates``: the published rates from FRED, their cache and the curve built from them.
"""
