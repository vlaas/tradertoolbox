"""The market state: what the backend knows about instruments and their latest values.

`state` holds the latest value per symbol and event type and says, once per period of the
feed, which of them changed (MKT-02, MKT-03). `registry` holds the instruments and option
chains that were read from the broker (MKT-01; the records are in
``s3t.broker.instruments``). `settlement` is the rule for the instant an option settles at.
Own IV and Greeks join with the chain service (WP-1.2).
"""
