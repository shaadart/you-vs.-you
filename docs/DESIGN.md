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

   Titles are the one place the machine signs its name: they are set in **Geist
   Pixel**. Everything you read beneath them stays human.
3. **Craft lives in the details.** Exact contrast ratios, tabular numerals, motion
   that eases rather than bounces, visible focus rings, and no layout shift.

## Typography

| role | family | size / line | notes |
|---|---|---|---|
| Titles and headlines (home question, "YOU WON.", "SHOW ME YOUR CHESS.", "YOU / 2026", archive years, build status) | Geist Pixel (Square) 500 | clamp(30–46) / 1.14; large clamp(36–68) / 1.04 | −0.035 to −0.05em; titles only |
| Human-voice statements ("That was very you.") | Newsreader 400 | clamp(28–40) | stay serif even when large |
| The two YOUs (ritual) | Newsreader 400 · Geist Pixel 500 | clamp(60–128) | you in the serif; your Twin, the second YOU, in pixels, ink-2 |
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
| **Home** | Shows instead of telling. Left: "What do you want to do with yourself?" · `PLAY YOURSELF` · one summary line. Right: a real position from your recent games that you have faced often ("You've been here 19 times. Your move."). Answer it, and it shows what you usually play there ("Usually O-O: 11 of 19") and what your Twin would play, as two arrows, with the distribution underneath in the machine voice. First visit (no games): "Play against yourself." and the starting position: your first move is compared with players near 1500 ("About 41% of players near 1500 would play e4 too. Yours will learn which one you would play.") → `SHOW ME YOUR CHESS`. Games but no Twin: `BUILD YOUR TWIN` with its estimate (`ROUGHLY 30 MIN · THE FIRST TIME`). | dashboards, feature lists, explanations |
| **Meet** (the ritual) | `YOU` ↓ `YOU` ↓ `PLAY`. Below, which version of yourself: Current You, Older You (your oldest year), Bullet/Blitz/Rapid You, `ARCHIVE →`. One machine line states what the version is (`OPENINGS + STRENGTH · 2021 · STYLE · 2026 · RATING 592 …`). Side: White · Black · Either. On PLAY the second YOU rises into the first and the board appears. | difficulty sliders, Elo inputs |
| **Game** | The board. Above: ■ Your Twin + its version. Below: ● You + typed-move input. Beside: the state in one serif sentence, elapsed time, at most one whisper. `MOVES` (collapsed), `RESIGN` (two-step), `FLIP`. | evaluation bars, probabilities, metrics |
| **Reflection** | Disclosed one line at a time: `YOU WON.` / `Barely.` → "That was very you." + `MATCH 0.90 · YOU USUALLY 0.82 · 34 DECISIONS` → "You and your Twin parted ways 4 times." + `30 OF 34 DECISIONS IDENTICAL` → `SEE THE DIFFERENCE` · `REPLAY` · `MEET ANOTHER VERSION` · `DONE`. | a move-by-move engine report |
| **See the difference** | One divergence at a time on the board: your move as an ochre arrow, your Twin's as cobalt. "You chose 18.Bf4." / "Your Twin would have chosen 18.Rd1." / "Both were plausible." / "In this position, you have played Rd1 12 times, and Bf4 twice." (or, in a new position, "You have made this kind of move 73 times before."). | |
| **Replay** | The game, step by step (`← →`, clickable moves). | |
| **Twin** (portrait) | `YOU / 2026` in pixels, with a faint serif YOU in ochre behind it: you, behind your reconstruction. Then counts in mono, and observations: one sentence each, its evidence underneath (`CASTLE -1.43 · Z 23.1`). Opening facts from your own counts. One honest line: "A reconstruction from your moves. It imitates what you did, not who you are." | personality scores, radar charts |
| **Archive** | Time as identity: `YOU / 2025`, `YOU / 2024`… each with games, rating, time controls, signature opening, and `PLAY THIS VERSION` (or "Too few games to meet."). | |
| **Settings** | Sources (accounts, last import, look for new games, add a source) · Your Twin (built when, from how many games, new games since, rebuild with its cost: `ABOUT 1 H 10 MIN · EVERY GAME IS READ AGAIN AT THE NEW DEPTH`; while work runs, the live line instead) · Analysis depth (Quick/Balanced/Thorough) · Hardware (parallel analyses) · During games (whispers, sound) · Privacy · Data (sizes, Forget your Twin, Forget everything, both confirmed by typing FORGET) · Advanced (temperature, player, model facts, last game PGN). | |
| **Import** | `SHOW ME YOUR CHESS.` · Chess.com · Lichess · PGN → a username or a file → `BRING THEM`. | |
| **Build** | No progress bar; three answers. *What is happening:* "Looking for your games." → "9,499 games found." / "We're going through them." *How far along:* every stage listed: finished ones with their time (`24:50`), the current one with its count (`DECISION 26,404 OF 66,011 · 52/S`, `FIT 7 OF 25`, `TEMPERATURE 3 OF 6`), the ones to come faint. *How long:* "About 25 minutes left." and where the number comes from (`ELAPSED 3:12 · FROM THE PACE SO FAR`). First build: "The first time is the slowest. After this, only new games are read." Always: "You can leave this page. It keeps going, and it will be here when you come back." | percentages, spinners, invented precision |

## Time and continuity

- **Time left is computed, never invented.** The current stage uses its own measured
  pace once a little of it is done. Before that, and for the stages still to come,
  the plan is used: your last build's stage times (when it was a cached rebuild at
  the same depth), or this machine's pace from its last full build, or, on a machine
  with no history, the presets' measured speeds, shown as "roughly". With no basis,
  no number ("ESTIMATING").
- **Words stay coarse.** "Less than a minute", "About 4 minutes", then steps of 5
  minutes, then hours. An estimate never looks more exact than it is.
- **Work is never lost by navigating.** Background work lives on the server. Starting
  it again while it runs joins the same job (`"joined": true`), never a second one
  and never a refusal. A quiet indicator in the top bar follows you everywhere
  (`REMEMBERING · ABOUT 25 MIN`, a slowly breathing cobalt dot), and Home and
  Settings show the same live line. When it finishes elsewhere, the indicator turns
  ochre: `YOUR TWIN IS READY`.
- **Expectations before the wait.** Every button that starts work shows its cost
  first, and why (`THE FIRST TIME`, `1,200 NEW DECISIONS TO READ`, `EVERY GAME IS
  READ AGAIN AT THE NEW DEPTH`).

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
| Geist Pixel (Square) | Vercel, with basement.studio (npm `geist` 1.8.2) | SIL OFL 1.1 (`static/fonts/OFL-GeistPixel.txt`) |
