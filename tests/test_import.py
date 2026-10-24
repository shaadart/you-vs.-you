"""Storage, import pipeline, platform adapters (offline via a fake HTTP client), CLI."""

import io
import json
import sqlite3
import urllib.error
from pathlib import Path

import pytest

from pco.cli import main
from pco.importers.http import USER_AGENT, HttpError, UrllibHttp
from pco.importers.pipeline import import_records
from pco.importers.sources import (
    RawRecord,
    SourceError,
    chesscom_records,
    lichess_records,
    pgn_records,
)
from pco.storage import games as repo
from pco.storage.db import SchemaError, connect, migrate, schema_version

FIXTURES = Path(__file__).parent / "fixtures"
PGN = FIXTURES / "games.pgn"


class FakeHttp:
    """Serves fixture files by URL; records every request."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def _find(self, url):
        for prefix, value in self.routes.items():
            if url.startswith(prefix):
                return value
        raise HttpError(404, url, "Not Found")

    def get_json(self, url, headers=None):
        self.calls.append((url, headers))
        return json.loads(self._find(url).read_text())

    def get_lines(self, url, headers=None):
        self.calls.append((url, headers))
        yield from self._find(url).read_text().splitlines()


CHESSCOM_ROUTES = {
    "https://api.chess.com/pub/player/alice/games/archives": FIXTURES / "chesscom_archives.json",
    "https://api.chess.com/pub/player/alice/games/2026/08": FIXTURES / "chesscom_2026_08.json",
    "https://api.chess.com/pub/player/alice/games/2026/09": FIXTURES / "chesscom_2026_09.json",
}
LICHESS_ROUTES = {"https://lichess.org/api/games/user/alice_l": FIXTURES / "lichess_user.ndjson"}


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


def count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


# --------------------------------------------------------------------- storage


def test_migrations_are_idempotent_and_versioned(tmp_path):
    c = connect(tmp_path / "db.sqlite3")
    assert schema_version(c) == 1
    assert migrate(c) == 1  # no-op second time
    c.close()
    c = connect(tmp_path / "db.sqlite3")  # reopen existing file
    assert schema_version(c) == 1


def test_newer_schema_is_refused(tmp_path):
    c = sqlite3.connect(tmp_path / "db.sqlite3")
    c.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    c.execute("INSERT INTO schema_version VALUES (99)")
    c.commit()
    c.close()
    with pytest.raises(SchemaError, match="newer"):
        connect(tmp_path / "db.sqlite3")


# --------------------------------------------------------------------- pipeline


def test_pgn_import_counts_and_preserves_raw_text(conn):
    report = import_records(conn, "pgn", str(PGN), pgn_records(PGN), username="Alice")
    assert (report.seen, report.new, report.duplicate, report.rejected) == (8, 5, 0, 3)
    assert count(conn, "games") == 5 and count(conn, "raw_games") == 8
    file_text = PGN.read_text()
    for (raw,) in conn.execute("SELECT raw_text FROM raw_games"):
        assert raw in file_text  # stored byte-for-byte as it appears in the file
    errors = [r[0] for r in conn.execute("SELECT normalize_error FROM raw_games")]
    assert sum(e is not None for e in errors) == 3
    run = conn.execute("SELECT status, n_new, n_rejected FROM import_runs").fetchone()
    assert tuple(run) == ("ok", 5, 3)


def test_reimport_is_idempotent(conn):
    import_records(conn, "pgn", str(PGN), pgn_records(PGN))
    before = (count(conn, "games"), count(conn, "raw_games"))
    report = import_records(conn, "pgn", str(PGN), pgn_records(PGN))
    assert (report.new, report.duplicate, report.rejected) == (0, 5, 3)
    assert (count(conn, "games"), count(conn, "raw_games")) == before


def test_same_game_from_two_sources_is_stored_once(conn):
    import_records(conn, "pgn", str(PGN), pgn_records(PGN))
    report = import_records(
        conn, "lichess", "alice_l", lichess_records(FakeHttp(LICHESS_ROUTES), "alice_l")
    )
    # AbCdEf12 is already known from the PGN file; ZyXwVu98 is new; crazyhouse rejected
    assert (report.new, report.duplicate, report.rejected) == (1, 1, 1)
    assert count(conn, "games") == 6
    # both raw copies are preserved even though the game is stored once
    assert (
        conn.execute("SELECT count(*) FROM raw_games WHERE kind='lichess-ndjson'").fetchone()[0]
        == 3
    )


def test_accounts_link_player_and_resolve_color(conn):
    report = import_records(conn, "pgn", str(PGN), pgn_records(PGN), username="alice", player="Me")
    assert report.involving_player == 4  # case-insensitive; chess960/illegal games rejected
    accounts = {(r["platform"], r["username"]) for r in conn.execute("SELECT * FROM accounts")}
    assert accounts == {("chesscom", "Alice"), ("other", "Alice")}
    colors = dict(
        conn.execute(
            "SELECT external_id, color FROM player_games pg JOIN players p ON p.id=pg.player_id"
            " WHERE p.name='Me' AND external_id IS NOT NULL"
        ).fetchall()
    )
    assert colors == {"live/222222222": "white", "daily/987654321": "white"}
    summary = repo.player_summary(conn)[0]
    assert summary["games"] == 4
    # own moves: 6 (chess.com, white of 12 plies) + 7 (OTB) + 3 (endgame) + 4 (daily)
    assert summary["decisions"] == 6 + 7 + 3 + 4


def test_account_cannot_belong_to_two_players(conn):
    repo.ensure_account(conn, "Me", "lichess", "alice_l")
    with pytest.raises(repo.AccountConflict):
        repo.ensure_account(conn, "Someone else", "lichess", "ALICE_L")


def test_interrupted_import_keeps_progress_and_marks_run_failed(conn):
    def records():
        yield from list(pgn_records(PGN))[:2]
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        import_records(conn, "pgn", str(PGN), records())
    assert count(conn, "games") == 2
    assert conn.execute("SELECT status FROM import_runs").fetchone()[0] == "failed"
    report = import_records(conn, "pgn", str(PGN), pgn_records(PGN))
    assert (report.new, report.duplicate) == (3, 2)


def test_unreadable_record_kinds_do_not_abort_import(conn):
    recs = [RawRecord("chesscom-json", "{broken"), *pgn_records(PGN)]
    report = import_records(conn, "pgn", "mixed", recs)
    assert report.rejected == 4 and report.new == 5


# --------------------------------------------------------------------- adapters


def test_lichess_request_shape_and_token():
    http = FakeHttp(LICHESS_ROUTES)
    recs = list(lichess_records(http, "alice_l", since_ms=123, max_games=2, token="tok"))
    assert len(recs) == 2  # max enforced client-side
    url, headers = http.calls[0]
    for part in ["clocks=true", "pgnInJson=true", "since=123", "max=2", "perfType="]:
        assert part in url
    assert headers == {"Accept": "application/x-ndjson", "Authorization": "Bearer tok"}


def test_lichess_unknown_user():
    with pytest.raises(SourceError, match="not found"):
        list(lichess_records(FakeHttp({}), "nobody"))


def test_chesscom_walks_archives_and_supports_incremental(conn):
    http = FakeHttp(CHESSCOM_ROUTES)
    report = import_records(conn, "chesscom", "alice", chesscom_records(http, "Alice"))
    assert (report.new, report.rejected) == (3, 1)  # chess960 record rejected
    http2 = FakeHttp(CHESSCOM_ROUTES)
    list(chesscom_records(http2, "Alice", since_month="2026-09"))
    assert [u for u, _ in http2.calls][-1].endswith("/2026/09")
    assert not any(u.endswith("/2026/08") for u, _ in http2.calls)


def test_chesscom_unknown_user():
    with pytest.raises(SourceError, match="not found"):
        list(chesscom_records(FakeHttp({}), "ghost"))


def _http_error(code, retry_after=None):
    headers = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError("u", code, "err", headers, io.BytesIO(b""))


def test_http_retries_429_then_succeeds_and_sends_user_agent():
    sleeps, seen = [], []
    responses = [_http_error(429, "7"), io.BytesIO(b'{"ok": 1}')]

    def opener(req, timeout):
        seen.append(req.get_header("User-agent"))
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    http = UrllibHttp(sleep=sleeps.append, opener=opener)
    assert http.get_json("https://example.org/x") == {"ok": 1}
    assert sleeps == [7.0] and seen == [USER_AGENT, USER_AGENT]


def test_http_does_not_retry_404():
    def opener(req, timeout):
        raise _http_error(404)

    with pytest.raises(HttpError) as exc:
        UrllibHttp(sleep=lambda s: None, opener=opener).get_json("https://example.org/x")
    assert exc.value.status == 404


# --------------------------------------------------------------------- CLI


def test_cli_import_and_summary(tmp_path, capsys):
    home = str(tmp_path / "home")
    assert main(["--home", home, "import", "pgn", str(PGN), "--username", "Alice"]) == 0
    out = capsys.readouterr().out
    assert "5 new games" in out and "3 rejected" in out and "games involving Alice: 4" in out

    # Regression: games known from the PGN file must not make the first account import
    # skip older archive months (live/111111111 lives in 2026-08).
    http = FakeHttp(CHESSCOM_ROUTES)
    assert main(["--home", home, "import", "chesscom", "Alice"], http=http) == 0
    out = capsys.readouterr().out
    assert "incremental" not in out
    assert "1 new games" in out  # live/111111111; the other two are already known

    # Second run is incremental from the last run's month (now), so no old month is read.
    http = FakeHttp(CHESSCOM_ROUTES)
    assert main(["--home", home, "import", "chesscom", "alice"], http=http) == 0
    assert "incremental: new games since last import" in capsys.readouterr().out
    assert [u for u, _ in http.calls] == [next(iter(CHESSCOM_ROUTES))]  # archive list only

    assert main(["--home", home, "games"]) == 0
    out = capsys.readouterr().out
    assert "games stored: 6" in out and "player Alice" in out


def test_cli_reports_missing_file(tmp_path, capsys):
    assert main(["--home", str(tmp_path), "import", "pgn", str(tmp_path / "nope.pgn")]) == 1
    assert "cannot read" in capsys.readouterr().err


def test_existing_account_keeps_owner_when_player_not_given(conn):
    me = repo.ensure_account(conn, "Me", "chesscom", "Alice")
    assert repo.ensure_account(conn, None, "chesscom", "alice") == me
    other = repo.ensure_account(conn, None, "lichess", "alice_l")  # new: named after username
    assert other != me
    assert conn.execute("SELECT name FROM players WHERE id=?", (other,)).fetchone()[0] == "alice_l"


def test_verify_detects_corrupted_game(tmp_path, capsys):
    home = str(tmp_path / "home")
    main(["--home", home, "import", "pgn", str(PGN)])
    assert main(["--home", home, "games", "--verify"]) == 0
    assert "replayed 5 games, 0 failures" in capsys.readouterr().out
    c = connect(Path(home) / "pco.sqlite3")
    c.execute("UPDATE games SET moves_uci = 'e2e4 e2e4' WHERE id = 1")
    c.commit()
    c.close()
    assert main(["--home", home, "games", "--verify"]) == 1
    assert "illegal move e2e4" in capsys.readouterr().out


def test_limited_lichess_import_is_not_an_incremental_base(tmp_path, capsys):
    home = str(tmp_path / "home")
    main(
        ["--home", home, "import", "lichess", "alice_l", "--max", "1"],
        http=FakeHttp(LICHESS_ROUTES),
    )
    http = FakeHttp(LICHESS_ROUTES)
    main(["--home", home, "import", "lichess", "alice_l"], http=http)
    assert "since=" not in http.calls[0][0]  # full fetch: the --max run covered 1 game only
    http = FakeHttp(LICHESS_ROUTES)
    main(["--home", home, "import", "lichess", "alice_l"], http=http)
    assert "since=" in http.calls[0][0]  # now incremental from the complete run
    assert "incremental" in capsys.readouterr().out


def test_failed_account_import_leaves_no_dangling_account(tmp_path, capsys):
    home = str(tmp_path / "home")
    assert main(["--home", home, "import", "chesscom", "ghost"], http=FakeHttp({})) == 1
    assert "not found" in capsys.readouterr().err
    c = connect(Path(home) / "pco.sqlite3")
    assert count(c, "accounts") == 0 and count(c, "players") == 0
    assert c.execute("SELECT status FROM import_runs").fetchone()[0] == "failed"


def test_conflicting_player_is_rejected_before_fetching(tmp_path, capsys):
    home = str(tmp_path / "home")
    main(
        ["--home", home, "import", "lichess", "alice_l", "--player", "Me"],
        http=FakeHttp(LICHESS_ROUTES),
    )
    http = FakeHttp(LICHESS_ROUTES)
    assert main(["--home", home, "import", "lichess", "alice_l", "--player", "Bob"], http=http) == 1
    assert "belongs to 'Me'" in capsys.readouterr().err and http.calls == []


def test_corrupt_game_database_gives_a_clear_error_and_is_left_alone(tmp_path, capsys):
    home = tmp_path / "home"
    home.mkdir()
    db = home / "pco.sqlite3"
    db.write_bytes(b"garbage" * 500)
    before = db.read_bytes()
    assert main(["--home", str(home), "games"]) == 1
    assert "unreadable" in capsys.readouterr().err
    assert db.read_bytes() == before  # evidence is never modified automatically
