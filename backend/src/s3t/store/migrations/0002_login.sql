-- The login of the one user (SEC-01): one row with the password's argon2id hash, and a row per
-- browser session. A session's token is in the browser only; here is its SHA-256.
CREATE TABLE login (
    id INTEGER NOT NULL PRIMARY KEY CHECK (id = 1),
    password_hash TEXT NOT NULL CHECK (password_hash LIKE '$argon2id$%'),
    changed_at_us INTEGER NOT NULL
) STRICT;

CREATE TABLE session (
    token_sha256 TEXT NOT NULL PRIMARY KEY CHECK (length(token_sha256) = 64),
    created_at_us INTEGER NOT NULL,
    expires_at_us INTEGER NOT NULL CHECK (expires_at_us > created_at_us)
) STRICT;
