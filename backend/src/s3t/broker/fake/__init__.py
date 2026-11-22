"""The fake broker: a deterministic synthetic market served over Tastytrade's REST shapes.

The real client runs against it unchanged, through an httpx transport (no ports). The stream
side follows at WP-0.4. Implementation plan, sections 3.2 and 3.3.
"""
