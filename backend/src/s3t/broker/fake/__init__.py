"""The fake broker: a deterministic synthetic market served over Tastytrade's REST shapes.

The real client runs against it unchanged: the REST side through an httpx transport, the
stream side through an in-memory connection (no ports). ``broker.FakeBroker`` is the two
together. Implementation plan, sections 3.2 and 3.3.
"""
