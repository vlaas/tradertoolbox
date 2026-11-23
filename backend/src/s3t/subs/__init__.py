"""Feed and subscriptions: the one way the rest of the backend reaches the stream (SUB-01).

`feed` is the interface: declarative demand with a priority class, data as plain batches,
the connection state in the stream. `budget` is the pure rule that turns demand into a
grant (SUB-02, SUB-03). `local` is the in-process implementation: the subscription manager,
the only caller of subscribe and unsubscribe on the broker's stream. A socket implementation
of the same interface follows when the process is split (architecture note, section 1).
"""
