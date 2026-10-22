"""Offline unit tests: configuration, asset verification, distribution utilities."""

import hashlib
import math
import random
from dataclasses import replace

import chess
import pytest

from pco.assets import Asset, AssetError, ensure_asset, stockfish_binary
from pco.config import load_settings
from pco.priors.base import MoveDistribution
from pco.priors.maia3 import history_boards


def test_settings_resolution_order(tmp_path):
    env = {"PCO_HOME": str(tmp_path / "envhome"), "PCO_TORCH_THREADS": "2"}
    assert load_settings(env=env).home == tmp_path / "envhome"
    assert load_settings(tmp_path / "arg", env=env).home == tmp_path / "arg"
    s = load_settings(env=env)
    assert s.torch_threads == 2
    assert s.db_path.parent == s.assets_dir.parent == s.home


def _local_asset(tmp_path, payload: bytes, sha: str | None = None) -> Asset:
    src = tmp_path / "src.bin"
    src.write_bytes(payload)
    return Asset(
        name="test",
        url=src.as_uri(),
        sha256=sha or hashlib.sha256(payload).hexdigest(),
        relpath="x/test.bin",
    )


def test_ensure_asset_downloads_verifies_and_reuses(tmp_path):
    settings = load_settings(tmp_path / "home", env={})
    asset = _local_asset(tmp_path, b"weights")
    path = ensure_asset(settings, asset)
    assert path.read_bytes() == b"weights"
    (tmp_path / "src.bin").unlink()  # second call must not touch the network/source
    assert ensure_asset(settings, asset, offline=True) == path


def test_ensure_asset_rejects_checksum_mismatch(tmp_path):
    settings = load_settings(tmp_path / "home", env={})
    asset = _local_asset(tmp_path, b"tampered", sha="0" * 64)
    with pytest.raises(AssetError, match="checksum"):
        ensure_asset(settings, asset)
    assert not (settings.assets_dir / asset.relpath).exists()


def test_ensure_asset_offline_missing(tmp_path):
    settings = load_settings(tmp_path / "home", env={})
    with pytest.raises(AssetError, match="pco setup"):
        ensure_asset(settings, _local_asset(tmp_path, b"x"), offline=True)


def test_stockfish_override_must_exist(tmp_path):
    settings = replace(load_settings(tmp_path, env={}), stockfish_path=tmp_path / "nope")
    with pytest.raises(AssetError, match="PCO_STOCKFISH"):
        stockfish_binary(settings)


def _dist(probs: dict[str, float]) -> MoveDistribution:
    return MoveDistribution("test", {m: math.log(p) for m, p in probs.items()})


def test_distribution_top_and_prob():
    d = _dist({"e2e4": 0.5, "d2d4": 0.3, "g1f3": 0.2})
    assert [m for m, _ in d.top(2)] == ["e2e4", "d2d4"]
    assert d.prob("g1f3") == pytest.approx(0.2)
    assert d.prob("a2a3") == 0.0


def test_distribution_sampling_matches_probabilities():
    d = _dist({"a": 0.7, "b": 0.2, "c": 0.1})
    rng = random.Random(0)
    n = 20_000
    counts = {m: 0 for m in "abc"}
    for _ in range(n):
        counts[d.sample(rng)] += 1
    for m, p in [("a", 0.7), ("b", 0.2), ("c", 0.1)]:
        assert counts[m] / n == pytest.approx(p, abs=0.015)
    assert d.sample(rng, temperature=0) == "a"


def test_history_boards_oldest_first_and_bounded():
    board = chess.Board()
    for san in ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6", "O-O", "Be7"]:
        board.push_san(san)
    hist = history_boards(board, limit=8)
    assert len(hist) == 8
    assert hist[-1].fen() == board.fen()
    assert all(not b.move_stack for b in hist)
    assert len(history_boards(chess.Board(), limit=8)) == 1
