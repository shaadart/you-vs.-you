# pco — Interface Design

Two layers. **Mirror** is the visual identity: paper, two voices, the board. It is the
source of truth and doesn't change. **Twin** is the experience built on it: you are
not playing a program, you are meeting a version of yourself.

> I → MY GAMES → MY TWIN → THE GAME → THE REFLECTION

Rule for every element: it must help the experience of playing yourself. Anything
else is removed.

---

# Identity: "Mirror"

## Principles

1. **One object, one room.** The board is the hero; everything else recedes. No
   cards, no shadows on chrome, no decoration. Structure comes from alignment,
   whitespace and 1px hairlines.
2. **Two voices, never mixed, never explained.**
   - *Human*: Newsreader, warm paper, an ochre accent. Poetic and short: "That was
     unusual.", "It knew you well.", "We're going through them."
   - *Machine*: IBM Plex Mono, cobalt, uppercase, tabular numbers. Clinical:
     `POSITION 008341 · FAMILIARITY 0.04`, `CASTLE -1.43 · Z 23.1`. Chess notation
     keeps its case inside the machine voice (`1.d4`).

   The two often appear together, the human line first and its machine fragment
   underneath. The page never says why.
3. **Craft lives in the details.** Exact contrast ratios, tabular numerals, motion
   that eases rather than bounces, visible focus rings, and no layout shift.

## Typography

| role | family | size / line | notes |
|---|---|---|---|
| Display (home question, headlines) | Newsreader 400 | clamp(38–64) / 1.06 | optical size on, −0.02em |
| Caps headlines ("YOU WON.", "SHOW ME YOUR CHESS.") | Newsreader 400 | clamp(46–92) / 1 | |
| The two YOUs (ritual) | Newsreader 400 | clamp(64–128) | first at `opsz 72`, second at `opsz 6`, ink-2 |
| Lede, whispers (human voice) | Newsreader italic 400 | 20–30 | ink-2 |
| Status in game | Newsreader 400 | 30 / 36 | |
| Machine fragments | IBM Plex Mono 400 | 11 / 17 | UPPERCASE, +0.08em, cobalt |
| Navigation, quiet actions | IBM Plex Mono 500 | 10.5–12 | UPPERCASE, +0.14–0.18em |
| Move list, clock | IBM Plex Mono 400 | 12–12.5 | `tabular-nums` |

Fonts are bundled (OFL; `src/pco/play/static/fonts/`), so the app looks the same on
every OS and works offline.

## Color

Light theme **Paper** and dark theme **Graphite**, chosen by `prefers-color-scheme`.
Ratios are text contrast against `bg` (WCAG AA ≥ 4.5).

| token | Paper | Graphite | use |
|---|---|---|---|
| `bg` | `#F3F0E9` | `#111110` | the room |
| `surface` | `#FAF8F3` | `#191917` | inputs, popovers |
| `ink` | `#191816` (15.6) | `#ECE8E0` (15.5) | primary text |
| `ink-2` | `#4E4A44` (7.7) | `#B3AEA4` (8.6) | secondary text, the second YOU |
| `ink-3` | `#6B665D` (5.0) | `#8F8A80` (5.5) | labels, quiet actions |
| `rule` | `#DCD6CA` | `#2B2A27` | hairlines |
| `machine` | `#2747D9` (6.2) | `#93A6FF` (8.2) | machine voice, the Twin's mark and arrows, focus |
| `human` | `#8A5A12` (5.2) | `#E3AE52` (9.4) | your mark, your arrows, selection, current nav |
| `check` | `#B4331F` (5.4) | `#F07A64` (6.9) | check, destructive actions |
| `sq-light` / `sq-dark` | `#E6E0D2` / `#B3A994` | `#8C8576` / `#5F594E` | board |

## The board

cburnett SVG pieces (inline symbols); last move tinted by who made it (ochre you,
cobalt your Twin); check as a soft radial glow; coordinates in mono at 70% of the
opposite square tone. Drag (4px threshold, 30% ghost origin), click (dots for moves,
rings for captures) or type (`/`, SAN or UCI, unique prefixes). Promotion: an
on-board chooser in the order Q, N, R, B. Your move appears on the board at once,
before the server answers.

---

# Experience: "Twin"

## Vocabulary

The opponent is **you** or **your Twin**: "Current You", "Older You", "Blitz You",
"You · 2024". It is never called an AI, bot, engine or computer in any screen (a
test enforces this on the page's visible text and its copy functions). The
machine voice may name components (`maia3-5m`, Stockfish) only under Settings →
Advanced.

## Navigation

Four destinations, mono, top right: **PLAY · ARCHIVE · TWIN · SETTINGS**. The
current one is underlined in ochre. Routes are hash-based (`#/meet/year-2024`), so
the browser's back button works.

## Screens

| screen | what it holds | what it leaves out |
|---|---|---|
| **Home** | "What do you want to do with yourself?" · `PLAY YOURSELF` · one summary line (`9,499 games · 6 years · built 26.10.26`). "Return to your game" when one is open; a quiet line while a build runs. No games yet → the import screen. Games but no Twin → "Your games are here. Your Twin isn't, yet." + `BUILD YOUR TWIN`. | dashboards, stats, history |
| **Meet** (the ritual) | `YOU` ↓ `YOU` ↓ `PLAY`. Below, which version of yourself: Current You, Older You (your oldest year), Bullet/Blitz/Rapid You, `ARCHIVE →`. One machine line states what the version is (`OPENINGS + STRENGTH · 2021 · STYLE · 2026 · RATING 592 …`). Side: White · Black · Either. On PLAY the second YOU rises into the first and the board appears. | difficulty sliders, Elo inputs |
| **Game** | The board. Above: ■ Your Twin + its version. Below: ● You + typed-move input. Beside: the state in one serif sentence, elapsed time, at most one whisper. `MOVES` (collapsed), `RESIGN` (two-step), `FLIP`. | evaluation bars, probabilities, metrics |
| **Reflection** | Disclosed one line at a time: `YOU WON.` / `Barely.` → "That was very you." + `MATCH 0.90 · YOU USUALLY 0.82 · 34 DECISIONS` → "You and your Twin parted ways 4 times." + `30 OF 34 DECISIONS IDENTICAL` → `SEE THE DIFFERENCE` · `REPLAY` · `MEET ANOTHER VERSION` · `DONE`. | a move-by-move engine report |
| **See the difference** | One divergence at a time on the board: your move as an ochre arrow, your Twin's as cobalt. "You chose 18.Bf4." / "Your Twin would have chosen 18.Rd1." / "Both were plausible." / "In this position, you have played Rd1 12 times, and Bf4 twice." (or, in a new position, "You have made this kind of move 73 times before."). | |
| **Replay** | The game, step by step (`← →`, clickable moves). | |
| **Twin** (portrait) | `YOU / 2026` (the first YOU carries a faint cobalt echo, the second YOU), counts in mono, then observations: one sentence each, its evidence underneath (`CASTLE -1.43 · Z 23.1`). Opening facts from your own counts. One honest line: "A reconstruction from your moves. It imitates what you did, not who you are." | personality scores, radar charts |
| **Archive** | Time as identity: `YOU / 2025`, `YOU / 2024`… each with games, rating, time controls, signature opening, and `PLAY THIS VERSION` (or "Too few games to meet."). | |
| **Settings** | Sources (accounts, last import, look for new games, add a source) · Your Twin (built when, from how many games, new games since, rebuild) · Analysis depth (Quick/Balanced/Thorough) · Hardware (parallel analyses) · During games (whispers, sound) · Privacy · Data (sizes, Forget your Twin, Forget everything, both confirmed by typing FORGET) · Advanced (temperature, player, model facts, last game PGN). | |
| **Import** | `SHOW ME YOUR CHESS.` · Chess.com · Lichess · PGN → a username or a file → `BRING THEM`. | |
| **Build** | No progress bar. "Looking for your games." → "9,499 games found." / "We're going through them." → stages appear only when reached: REMEMBERING, FINDING PATTERNS, COMPARING DECISIONS, RECONSTRUCTING YOU, READY. Under them, the last few machine fragments, older ones fading (`DECISION 48,640 OF 66,011 · 1,658/S`). `MEET YOURSELF` when ready. | percentages, ETAs |

## Progressive disclosure

During the game, at most three whispers, never closer than 8 plies apart, each
grounded in data:

| whisper | when | machine fragment |
|---|---|---|
| New territory. | the first position you face that you have never faced in that version's games | `POSITION 224622 · SEEN 0×` |
| That was unusual. | your move had < 5% of the probability of your Twin's top choice | `POSITION 351814 · FAMILIARITY 0.04` |
| That position looks familiar. | after ply 16, a position you have faced ≥ 3 times | `POSITION 008341 · SEEN 12×` |

After the game the reflection reveals itself line by line (900 ms, then 700 ms;
instant under reduced motion), and "See the difference" opens the details.

The reflection's words come from numbers computed on your game:
- *Barely / Cleanly*: your worst evaluation during the game (≤ −1.00 is "Barely").
- *You were better, once*: a loss after having been ≥ +1.50.
- *Very you / mostly you / not quite you*: the share of your moves among the Twin's
  three likeliest, against the same share on your own recent real games
  (validation top-3). "You weren't quite yourself." replaces "It knew you well."
  when that share is low.
- *Both were plausible*: both moves had ≥ 10% probability or are within 0.50 pawns.

## Versions

"Current You" is the trained Twin. Other versions are honest approximations and say
so in the machine voice: that period's **openings** (a book built from its games)
and **strength** (its median rating) with the **current style** (the learned
weights). Years need ≥ 150 games; time-control versions use the past year of that
time control.

## Motion

Screens rise 6px and fade in (420 ms). Pieces move in 180 ms. Whispers fade in and
out over 900 ms and leave after 7 s or your next move. The Twin's reply waits
250 ms + 750 ms × (1 − p) so confident moves come fast. Sound is an optional
synthesized wooden tap. Everything is off under `prefers-reduced-motion`.

## Desktop and mobile

- **Desktop:** content centered; the game is the board (up to 720px) plus a quiet
  280px column whose controls sit at the bottom. Text screens are one 760px column
  that starts low on the page.
- **Below 860px:** the board is full width with the two strips; status, whisper and
  controls follow underneath. No card stacks; the navigation tightens to mono
  labels.

## Accessibility

- Keyboard: every control is reachable, with a 2px cobalt focus ring. `/` type a move ·
  `N` meet yourself again · `F` flip · `← →` step through replays and differences ·
  `Esc` clear · `?` shortcuts.
- The game status and whispers are `aria-live="polite"`; the board has
  `role="img"` with a text description.
- Color is never the only signal: your mark is a circle and the Twin's a square;
  every arrow and highlight is paired with text.

## Third-party assets

| asset | author | license |
|---|---|---|
| Pieces ("cburnett") | Colin M.L. Burnett (via lichess-org/lila) | GPLv2+ |
| Newsreader | The Newsreader Project Authors | SIL OFL 1.1 (`static/fonts/OFL-Newsreader.txt`) |
| IBM Plex Mono | IBM Corp. | SIL OFL 1.1 (`static/fonts/OFL-IBMPlexMono.txt`) |
