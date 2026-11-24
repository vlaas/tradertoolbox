-- The application's settings: one row per key, the value as JSON. What a key means belongs to
-- the code that reads it.
CREATE TABLE setting (
    key TEXT NOT NULL PRIMARY KEY CHECK (key <> ''),
    value TEXT NOT NULL CHECK (json_valid(value)),
    updated_at_us INTEGER NOT NULL
) STRICT;
