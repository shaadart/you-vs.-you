# pco — Interface Design: "Mirror"

The play screen is the only screen. You sit across from a statistical reconstruction
of yourself. The design makes that sentence visible: one quiet object (the board),
two voices (human and machine), and an honest instrument panel between them.

## Principles

1. **One object, one room.** The board is the hero; everything else recedes. No
   cards, no shadows on chrome, no decoration. Structure comes from alignment,
   whitespace and 1px hairlines.
2. **Two voices, never mixed.**
   - *Human*: warm paper, an ochre accent, a serif (Newsreader). Used for what a
     person would say: "Your move.", "Checkmate. You won.", move names.
   - *Machine*: cobalt, monospace (IBM Plex Mono), uppercase micro-labels,
     tabular numbers. Used for what an instrument would report: probabilities,
     latency, model ids, temperature.

   A reader always knows who is speaking.
3. **Precise ambiguity.** The opponent is "You, reconstructed": named like a
   person, annotated like a machine (`model 2026-10-24 · T 0.90`). It hesitates
   longer when it is less sure, and it shows what it considered, but never
   claims intent: its likeness score is labeled as an estimate.
4. **Power without chrome.** Everything is reachable in two ways: direct
   manipulation (drag, click) and keyboard (type a move, single-key commands).
   Settings live in one sheet that is closed by default.
5. **Craft lives in the details.** Exact contrast ratios, tabular numerals,
   motion that eases rather than bounces, focus rings that are visible, and no
   layout shift when state changes.

## Layout

```
┌────────────────────────────────────────────────────────────────────────────┐
│ ■ pco  mirror                                               sound   ?      │ top bar, 56px, hairline below
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  ■ You, reconstructed         CONSIDERING ···  │  STATE                      │
│  ┌─────────────────────────────────────────┐   │  Your move.                 │  serif 30
│  │                                         │   │                             │
│  │                                         │   │  LAST DECISION · MACHINE    │
│  │               B O A R D                 │   │  Nf6                        │  serif 44
│  │        square, min(76vh, 680px)         │   │  p 0.62 · likeness 1.00 · 41 ms
│  │                                         │   │  Nf6  ━━━━━━━━━━━━━━  62.0  │  probability spectrum:
│  │                                         │   │  Bg4  ━━━━━           17.5  │  hover a row → ghost arrow
│  │                                         │   │  e6   ━━━━            15.0  │  on the board
│  └─────────────────────────────────────────┘   │                             │
│  ● You · white                  › type a move  │  MOVES                      │
│                                                │  1  e4     c5               │  mono, tabular, scrolls
│                                                │  2  Nf3    Nc6              │
│                                                │  ───────────────────────────│
│                                                │  New game  N   Resign   PGN │
│                                                │  model shaadpgn@…, a statistical imitation, not you │
└────────────────────────────────────────────────────────────────────────────┘
```

- **Grid:** the content is centered at max 1180px. Two columns: the board column
  (fluid, square board) and the instrument panel (340px). There is a 48px gutter
  and an 8px base unit.
- **Player strips:** the opponent sits above the board and you below. The
  machine's mark is a filled cobalt square; yours is an ochre circle. Their right
  sides carry live state: *considering*, latency, or the move input.
- **Below 860px:** a single column (top bar, strips, a full-width board, then
  the panel). The move list collapses, and the actions become a sticky bottom
  bar with safe-area padding.

## Interactions

| behavior | detail |
|---|---|
| Move: drag | Pointer drag with a 4px threshold. The origin piece stays as a 30% ghost, and the floating piece follows the pointer. A drop on a legal square moves; anywhere else snaps back. |
| Move: click | Click a piece, then a target. Legal targets show as dots, and captures as rings. Clicking another own piece reselects it. |
| Move: type | `/` focuses the move input. Type SAN (`Nf3`, `exd5`, `O-O`) or UCI (`g1f3`). A unique prefix shows its completion and Enter plays it. |
| Promotion | A chooser drops down over the promotion file (Q, N, R, B). `Esc` cancels. |
| Opponent turn | The opponent strip shows `CONSIDERING ···` with stepped, mechanical dots. The reply is held for a short beat proportional to the model's uncertainty, 250 ms + 750 ms × (1 − p), so confident moves come fast and hesitations feel human. |
| Piece motion | 180 ms, `cubic-bezier(.2,.7,.2,1)`. None under `prefers-reduced-motion`. |
| What it considered | Hovering a spectrum row draws a translucent cobalt arrow for that candidate on the board. |
| Highlights | The last move is tinted by who made it: ochre for you, cobalt for the machine. Check is a soft radial glow on the king. |
| Game end | The status speaks in the human voice ("Checkmate. You won."). The board softens to 92%, and `New game` becomes the primary action. |
| Resign | Two-step inline: "Resign" becomes "Confirm resign" for 3 s. No modal dialog. |
| Errors | Shown inline in the panel, never `alert()`. Opponent failures offer "Retry". |
| Shortcuts | `/` type a move · `N` new game · `F` flip board · `Esc` clear or close · `?` show shortcuts |
| Sound | Optional, synthesized (no audio files): a short wooden tap for moves and a lower one for captures. The toggle is remembered locally. |

## Typography

| role | family | size / line | weight | notes |
|---|---|---|---|---|
| Status (human voice) | Newsreader | 30 / 36 | 400 | optical size on; tracking −0.01em |
| Decision move | Newsreader | 44 / 44 | 500 | the move as a word |
| Player names | Newsreader | 17 / 22 | 500 | "You, reconstructed" |
| Body / buttons | system UI sans | 14 / 20 | 500 | native, fast |
| Micro labels (machine) | IBM Plex Mono | 10.5 / 14 | 500 | UPPERCASE, +0.14em tracking |
| Readouts, move list | IBM Plex Mono | 12.5 / 20 | 400 | `tabular-nums` |
| Board coordinates | IBM Plex Mono | 13 user units | 500 | 70% of the opposite square tone |

The fonts are bundled (OFL; `src/pco/play/static/fonts/`), so the app looks the
same on every OS and works offline.

## Color

Light theme **Paper** and dark theme **Graphite**, chosen by
`prefers-color-scheme`. Ratios are text contrast against `bg` (WCAG AA ≥ 4.5).

| token | Paper | Graphite | use |
|---|---|---|---|
| `bg` | `#F3F0E9` | `#111110` | the room |
| `surface` | `#FAF8F3` | `#191917` | sheet, inputs |
| `ink` | `#191816` (15.6) | `#ECE8E0` (15.5) | primary text |
| `ink-2` | `#4E4A44` (7.7) | `#B3AEA4` (8.6) | secondary text |
| `ink-3` | `#6B665D` (5.0) | `#8F8A80` (5.5) | micro labels |
| `rule` | `#DCD6CA` | `#2B2A27` | hairlines |
| `machine` | `#2747D9` (6.2) | `#93A6FF` (8.2) | the model: marks, bars, arrows, focus |
| `human` | `#8A5A12` (5.2) | `#E3AE52` (9.4) | you: marks, selection |
| `check` | `#B4331F` (5.4) | `#F07A64` (6.9) | check, destructive confirm |
| `sq-light` | `#E6E0D2` | `#8C8576` | board |
| `sq-dark` | `#B3A994` | `#5F594E` | board |

Highlights are the accents at low alpha: `human` at 28% and `machine` at 22% for
the last move, and `human` at 38% for selection.

## Accessibility

- Every interactive element is keyboard-reachable, with a visible focus ring
  (2px `machine`, 2px offset).
- The status is an `aria-live="polite"` region.
- The board has `role="img"` with a live FEN description, and moves can be fully
  entered by keyboard through the move input.
- Color is never the only signal: highlights pair with the text status, and the
  machine and human marks differ in shape (square vs circle).
- Motion is disabled under `prefers-reduced-motion`.
- Touch targets are ≥ 40px, and the board uses `touch-action: none`.

## Third-party assets

| asset | author | license |
|---|---|---|
| Pieces ("cburnett") | Colin M.L. Burnett (via lichess-org/lila) | GPLv2+ |
| Newsreader | The Newsreader Project Authors | SIL OFL 1.1 (`static/fonts/OFL-Newsreader.txt`) |
| IBM Plex Mono | IBM Corp. | SIL OFL 1.1 (`static/fonts/OFL-IBMPlexMono.txt`) |
