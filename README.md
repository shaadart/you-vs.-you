# Personal Chess Opponent (pco)

**Import your chess games → learn how you play → play against yourself.**

pco trains a chess opponent that plays the way *you* play: your openings, your
preferences and tendencies, and roughly your strength and blunder rate. It runs
entirely on your own computer, on an ordinary CPU, with no GPU and no cloud.

How it works, in one paragraph: [Maia-3](https://github.com/CSSLab/maia3), a neural
network trained on millions of human games, predicts what a human at your rating
would play. pco then learns, from your own games, how *your* choices differ from
that typical human: which kinds of moves you favor or avoid, judged with Stockfish's
evaluation of the alternatives. It also learns your opening repertoire. The
opponent samples moves from that personal distribution, at a "temperature" fitted
so its move quality matches yours.

## Quick start (Chess.com and/or Lichess)

You need [uv](https://docs.astral.sh/uv/getting-started/installation/), which
installs the right Python and CPU-only PyTorch for you.

```sh
git clone <this repository> && cd <repository>
uv sync
uv run pco quickstart --chesscom YOUR_CHESSCOM_NAME --lichess YOUR_LICHESS_NAME --play
```

`quickstart` runs four steps:
1. It downloads and checksum-verifies the model (21 MB) and Stockfish 19 (Linux
   x86-64; see Troubleshooting for other systems).
2. It imports your public games. Both accounts are linked to one local player.
3. It trains on your 2,000 most recent games, with progress and an upfront time
   estimate.
4. It opens the board in your browser.

The first training run analyzes every one of your moves, which takes a while (see
the speed table). Later runs only analyze new games.

Afterwards:

```sh
uv run pco play --player YOUR_CHESSCOM_NAME      # play against yourself again
uv run pco import chesscom YOUR_CHESSCOM_NAME    # fetch new games...
uv run pco train YOUR_CHESSCOM_NAME              # ...and update your opponent (incremental)
uv run pco doctor                                # check setup, data, profiles
```

## Speed presets

`--speed` sets how much analysis each of your moves gets: how many alternative
moves are considered, and how deeply Stockfish checks them. The learned model is
the same under every preset. Play uses the preset your profile was trained with.

| preset | analysis per move | first training, 2,000 games (~60k moves)* | live move | held-out accuracy** |
|---|---|---|---|---|
| **lite** (default) | 3 candidates, shallow search | ~20–25 min (estimated) | ~40 ms | same as standard |
| standard | 5 candidates, 10k-node search | ~40 min (measured) | ~50 ms | reference |
| deep | 6 candidates, 30k-node search | ~2 h (estimated, ~3× standard) | slower | same as standard |

\* On a 10-core laptop CPU (Intel i5-1235U), with 4 engine processes. Retraining
after new games only analyzes those games: about a minute for +50 games.
\** On three players' held-out games, the three presets' final-model likelihoods
differed by ≤ 0.001 (`AI_CONTEXTS/EXPERIMENTS.md`, E7.2). Deeper engine analysis did
not make the imitation more accurate, so lite is the default.

## How well does it imitate you?

- **Measured on future games.** Each player's newest 20% of games were held out,
  and the model was trained only on earlier ones. On all three players tested
  (1,000 games each), the personal model predicted their actual moves better than
  every alternative tried: generic Maia, Maia at a fitted rating, and Maia with
  their opening book. The differences are statistically significant.
- **Openings are the strongest personal signal.** In positions you have played
  before, it predicts your move about 76–83% of the time.
- **Elsewhere the gain is real but small.** In positions you have never reached,
  it beats Maia by roughly 1% in likelihood. Most of your "style" outside the
  opening is still shared with similar-rated humans.
- **Its strength is matched to you.** Sampled moves lose about as much (in engine
  terms) as your real moves do.
- **Time controls and opponent rating didn't help.** Conditioning on them gave no
  measurable gain, so it isn't used.

Run `uv run pco eval YOUR_NAME` to measure it on your own games. All experiments
and their exact numbers are in `AI_CONTEXTS/EXPERIMENTS.md`.

## The interface

`pco play` opens a single, quiet screen ("Mirror"; full spec in `docs/DESIGN.md`).
- **The board:** drag or click to move, or press `/` and type a move (`Nf3`,
  `exd5`, `O-O`).
- **The panel:** shows what the model just decided, the probability it gave its
  top candidates, and the move it actually played. Hover a candidate to see it
  drawn on the board.
- **Pacing:** the opponent replies after a pause that grows with its uncertainty.
- **Shortcuts:** `N` new game, `F` flip board, `?` all shortcuts.
- **Theme:** light and dark follow your system.

## What it is not

- **Not a copy of your mind.** It is a statistical imitation of your observable
  move choices. The *player-likeness* score shown after each move is a model
  estimate, not a claim about intent.
- **Not a strength engine.** It deliberately plays like you, including your mistakes.
- **No clocks during play.** Your time-pressure habits are learned from your
  games, but untimed play never triggers them.
- **Standard chess only.** Chess960 and other variants are skipped at import.

## Privacy

Everything stays on your machine, under `$PCO_HOME` (default `~/.local/share/pco`):
your games, the analysis cache (safe to delete), and your trained profiles. The
network is used only to download your public games and the model and engine
files. The play page is served on `127.0.0.1` only.

## All commands

| command | what it does |
|---|---|
| `pco quickstart --chesscom U [--lichess U] [--speed S] [--play]` | everything at once |
| `pco setup` / `pco doctor` | install and verify assets; check setup, data and profiles |
| `pco import chesscom U` / `lichess U` / `pgn FILE --username U` | import games; repeatable, only new games are added |
| `pco games [--verify]` | summary, plus a replay check of every stored game |
| `pco train NAME [--speed S] [--recent N \| --all]` | train or update your personal opponent |
| `pco play [--player NAME] [--temperature T]` | play in the browser; without `--player`, a generic Maia opponent |
| `pco eval NAME` | held-out evaluation report vs baselines |
| `pco build NAME [--export f.jsonl]` | analyzed decision dataset, for research |

Use `--player NAME` on imports to link several accounts to one person. Settings
via environment: `PCO_HOME`, `PCO_STOCKFISH` (an existing Stockfish binary),
`PCO_TORCH_THREADS` (training), `PCO_LIVE_THREADS` (play, default 1) and
`PCO_LICHESS_TOKEN` (optional, higher rate limit).

## Troubleshooting

- **Stockfish on macOS or Windows:** install Stockfish (e.g. `brew install
  stockfish`, or the official download) and either put it on `PATH` or set
  `PCO_STOCKFISH=/path/to/stockfish`. `pco doctor` confirms it works.
- **Interrupted training or import:** run the same command again. Finished work is
  cached and resumes.
- **"analysis cache was unreadable":** it was moved aside automatically and is
  being rebuilt. Your games are untouched.
- **"game database is unreadable":** pco never modifies it automatically. Restore a
  backup, or move it away and re-import (imports are repeatable).
- **Slow first training:** use `--speed lite`, or a smaller `--recent N`.

## Development

```sh
uv run pytest -q                                  # tests (asset tests skip without `pco setup`)
uv run ruff check . && uv run ruff format --check .
```

Engineering notes, decisions, experiments and handoff for contributors live in
`AI_CONTEXTS/`. Start with `HANDOFF.md`. The original specification is
`docs/MASTER_SPEC.md`.

## Licensing

Bundled interface assets: the chess pieces are by Colin M.L. Burnett (GPLv2+, via
lichess-org/lila); the Newsreader and IBM Plex Mono fonts are SIL OFL 1.1 (license
texts in `src/pco/play/static/fonts/`).

Maia-3 (CSSLab) is AGPL-3.0 and is imported in-process; Stockfish is GPL-3.0 and
runs as a separate process. If you distribute pco, it must be AGPL-compatible. The
project's own license has not been chosen yet (owner's decision; see
`AI_CONTEXTS/PROJECT.md`).
