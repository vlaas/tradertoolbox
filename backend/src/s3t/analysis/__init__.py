"""Services over the market state and the pricing core.

- ``units``: raw units of the pricing core to the units of a trader (PRC-04).
- ``surface``: the volatility surface of the underlyings that a view watches (WP-1.1).
- ``surface_task``: the fit as a task of the process pool. A worker imports only this and the
  pricing core.
"""
