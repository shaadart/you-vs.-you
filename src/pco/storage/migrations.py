"""Ordered schema migrations. Never edit a released migration; append a new one."""

_V1 = """
CREATE TABLE import_runs (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,              -- 'pgn' | 'lichess' | 'chesscom'
    locator TEXT NOT NULL,             -- file path or username
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',  -- running | ok | failed
    complete INTEGER NOT NULL DEFAULT 1,     -- 0 when limited (e.g. --max): not an incremental base
    n_seen INTEGER NOT NULL DEFAULT 0,
    n_new INTEGER NOT NULL DEFAULT 0,
    n_duplicate INTEGER NOT NULL DEFAULT 0,
    n_rejected INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

-- Raw layer: each game exactly as received (PGN text, or one JSON record).
CREATE TABLE raw_games (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,                -- 'pgn' | 'lichess-ndjson' | 'chesscom-json'
    content_sha256 TEXT NOT NULL UNIQUE,
    raw_text TEXT NOT NULL,
    first_import_run_id INTEGER REFERENCES import_runs(id),
    fetched_at TEXT NOT NULL,
    normalize_error TEXT               -- NULL when the latest normalization succeeded
);

-- Normalized layer: one row per distinct real game, rebuildable from raw_games.
CREATE TABLE games (
    id INTEGER PRIMARY KEY,
    raw_game_id INTEGER NOT NULL REFERENCES raw_games(id),
    dedup_key TEXT NOT NULL UNIQUE,
    platform TEXT NOT NULL,            -- 'lichess' | 'chesscom' | 'other'
    external_id TEXT,
    played_at TEXT,                    -- UTC 'YYYY-MM-DDTHH:MM:SSZ', or 'YYYY-MM-DD', or NULL
    white TEXT NOT NULL,
    black TEXT NOT NULL,
    white_elo INTEGER,
    black_elo INTEGER,
    result TEXT NOT NULL,              -- '1-0' | '0-1' | '1/2-1/2' | '*'
    termination TEXT,
    tc_base_s INTEGER,
    tc_inc_s INTEGER,
    time_class TEXT,                   -- ultrabullet|bullet|blitz|rapid|classical|correspondence
    rated INTEGER,                     -- 1 | 0 | NULL (unknown)
    start_fen TEXT NOT NULL,
    final_fen TEXT NOT NULL,
    moves_uci TEXT NOT NULL,           -- space-separated UCI, mainline only
    n_plies INTEGER NOT NULL,
    clocks_s TEXT,                     -- JSON list per ply (seconds left after the move) or NULL
    normalizer_version INTEGER NOT NULL
);
CREATE INDEX games_white ON games (platform, lower(white));
CREATE INDEX games_black ON games (platform, lower(black));
CREATE INDEX games_played_at ON games (played_at);

CREATE TABLE players (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE accounts (
    id INTEGER PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES players(id),
    platform TEXT NOT NULL,
    username TEXT NOT NULL,
    username_lower TEXT NOT NULL,
    UNIQUE (platform, username_lower)
);

-- A player's games with their color resolved from linked accounts.
CREATE VIEW player_games AS
SELECT a.player_id AS player_id,
       CASE WHEN lower(g.white) = a.username_lower THEN 'white' ELSE 'black' END AS color,
       g.*
FROM games g
JOIN accounts a
  ON a.platform = g.platform AND a.username_lower IN (lower(g.white), lower(g.black))
"""

MIGRATIONS: list[str] = [_V1]
