// Exercises the page script (docs/DESIGN.md "Twin") against a small fake DOM and a fake
// server: routing, the home summary, onboarding redirect, the YOU -> YOU -> PLAY ritual and
// its versions, click-to-move with the move shown before the server answers, whispers,
// promotion order, typed moves, pacing, two-step resign, status phrasing, the reflection's
// progressive copy, the difference and replay boards, build stages, and that alert() is
// never used. Run by tests/test_ui_js.py.
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8").match(/<script>([\s\S]*)<\/script>/)[1];

class FakeEl {
  constructor(tag) {
    Object.assign(this, { tagName: String(tag).toUpperCase(), children: [], attrs: {}, style: {}, dataset: {},
      listeners: {}, hidden: false, textContent: "", innerHTML: "", className: "", value: "", disabled: false,
      open: false, scrollTop: 0, scrollHeight: 0, placeholder: "" });
    const set = new Set();
    this.classList = { add: (...c) => c.forEach(x => set.add(x)), remove: (...c) => c.forEach(x => set.delete(x)),
      toggle: (c, on) => { const v = on === undefined ? !set.has(c) : !!on; v ? set.add(c) : set.delete(c); return v; },
      contains: c => set.has(c) };
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
  dispatch(t, ev = {}) { return Promise.all((this.listeners[t] || []).map(f => f({ target: this, preventDefault() {}, ...ev }))); }
  click() { return this.dispatch("click"); }
  appendChild(c) { this.children.push(c); return c; }
  append(...c) { this.children.push(...c); }
  replaceChildren(...c) { this.children = c; }
  get firstChild() { return this.children[0] || { textContent: "" }; }
  focus() {} blur() {} setPointerCapture() {}
  createSVGPoint() { return { matrixTransform: () => ({ x: 0, y: 0 }) }; }
  getScreenCTM() { return { inverse: () => ({}) }; }
}
const byId = {};
global.document = {
  getElementById: id => (byId[id] ||= new FakeEl("div")),
  createElement: t => new FakeEl(t), createElementNS: (_, t) => new FakeEl(t),
  querySelector: () => null, querySelectorAll: () => [], addEventListener() {},
  documentElement: new FakeEl("html"),
};
global.window = global;
const winListeners = {};
global.addEventListener = (t, f) => (winListeners[t] ||= []).push(f);
let hash = "";
global.location = { get hash() { return hash; },
  set hash(v) { hash = v; setTimeout(() => (winListeners.hashchange || []).forEach(f => f()), 0); } };
global.localStorage = { getItem: () => null, setItem() {} };
global.requestAnimationFrame = cb => setTimeout(cb, 0);
global.alert = m => { throw new Error("alert() must not be used: " + m); };

const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const HOME = { player: "me", players: ["me"], has_twin: true, games: 10734, decisions: 400000,
  years: ["2021", "2023", "2024", "2025", "2026", "2022"], built: "2026-10-24T06:41:24Z", built_games: 2000,
  new_games: 0, versions: 8, job: null, game: null, last_game: "2026-10-24T05:00:00Z" };
const VERSIONS = [
  { id: "current", kind: "current", label: "Current You", period: "2026", games: 2000, rating: 1135,
    signature: { move: "1.e4", share: 0.87 }, basis: "STYLE · OPENINGS · STRENGTH · 2026" },
  { id: "year-2025", kind: "year", label: "You · 2025", period: "2025", games: 2795, rating: 1050, signature: null, basis: "OPENINGS + STRENGTH · 2025 · STYLE · 2026" },
  { id: "year-2021", kind: "year", label: "You · 2021", period: "2021", games: 838, rating: 600, signature: null, basis: "OPENINGS + STRENGTH · 2021 · STYLE · 2026" },
  { id: "tempo-blitz", kind: "tempo", label: "Blitz You", period: "blitz", games: 5000, rating: 1100, signature: null, basis: "OPENINGS + STRENGTH · BLITZ · STYLE · 2026" },
];
const base = {
  game: true, fen: START, turn: "white", human_color: "white", human_to_move: true, opponent_to_move: false,
  legal_moves: ["e2e4", "e2e3", "g1f3"],
  legal: [{ uci: "e2e4", san: "e4" }, { uci: "e2e3", san: "e3" }, { uci: "g1f3", san: "Nf3" }],
  san_moves: [], last_move: null, check: false, over: null, error: null, saved_pgn: null, last_choice: null,
  whisper: null, elapsed: 0, version: { ...VERSIONS[0], temperature: 0.9 },
};
const REFLECTION = {
  outcome: "won", reason: "checkmate", decisions: 20, match: 0.9, usual_match: 0.82, differed: 4,
  lowest_eval: -180, highest_eval: 400, human_color: "white",
  positions: [START, "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"], moves: ["e2e4"], san: ["e4"],
  divergences: [{ ply: 0, fen: START, position: "008341", human: "e2e4", human_san: "1.e4", twin: "d2d4", twin_san: "1.d4",
    p_human: 0.3, p_twin: 0.45, rank: 2, verdict: "both", kind_twin: ["pawn", "quiet", "opening", "d4"], kind_twin_count: 73,
    here_human: 0, here_twin: 0, here_total: 0 }],
  version: VERSIONS[0],
};
const GLIMPSE = { id: 7, mode: "yours", fen: START, orientation: "white", last_move: null, seen: 80, position: "008341",
  number: "1.", legal: [{ uci: "e2e4", san: "e4" }, { uci: "d2d4", san: "d4" }, { uci: "g1f3", san: "Nf3" }] };
const PEOPLE = { ...GLIMPSE, id: 8, mode: "people", seen: 0 };
let glimpseMode = "yours";
const RUNNING = { id: 3, kind: "build", stage: "REMEMBERING", stages: ["REMEMBERING"], status: "running", error: null,
  result: {}, plan: { REMEMBERING: 1500, "FINDING PATTERNS": 300 }, meta: { first: true, basis: "this machine" },
  durations: {}, fraction: 0.4, fragments: ["DECISION 26,404 OF 66,011 · 52/S"], eta: 1560, eta_basis: "live", seconds: 192 };
const YEARS = [  // newest first, as the server sends them
  { year: "2026", games: 2158, rating: 1125, signature: { move: "1.d4", share: 0.66 }, tempos: ["blitz"], version: "current", now: true },
  { year: "2022", games: 121, rating: 486, signature: null, tempos: ["bullet"], version: null, now: false },
  { year: "2021", games: 838, rating: 592, signature: { move: "1.e3", share: 0.6 }, tempos: ["bullet"], version: "year-2021", now: false },
];
let server = { ...base }, home = { ...HOME }, job = null;
const calls = [];
const reply = (s, ok = true) => ({ ok, json: async () => s });
global.fetch = async (path, opts) => {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  calls.push([path, body]);
  if (path === "/api/home") return reply(home);
  if (path === "/api/versions") return reply({ versions: VERSIONS });
  if (path === "/api/reflection") return reply(REFLECTION);
  if (path === "/api/job") return reply({ job });
  if (path === "/api/archive") return reply({ player: "me", years: YEARS });
  if (path === "/api/settings" && !body) return reply({ player: "me", players: ["me"], prefs: { speed: "lite", workers: null, temperature: null },
    presets: { lite: { candidates: 3, nodes: 3000, rate: 70 } }, accounts: [{ platform: "chesscom", username: "me", last_import: null }],
    storage: { home: "/h", games_bytes: 1, analysis_bytes: 1, twin_bytes: 1, played_games: 0 }, cores: 12, default_workers: 4,
    twin: { built: "2026-10-26T00:00:00Z", depth: "lite" }, can_import: true, forget_word: "FORGET",
    estimate: { seconds: 100, new_decisions: 0, first: false, rereads: false } });
  if (path === "/api/glimpse" && !body) return reply(glimpseMode === "yours" ? GLIMPSE : PEOPLE);
  if (path === "/api/glimpse") return reply(glimpseMode === "yours"
    ? { mode: "yours", you: { uci: body.uci, san: "e4", count: 2, p: 0.1 }, usual: { uci: "d2d4", san: "d4", count: 63 }, seen: 80,
        twin: [{ uci: "d2d4", san: "d4", p: 0.71 }, { uci: "e2e4", san: "e4", p: 0.12 }, { uci: "g1f3", san: "Nf3", p: 0.08 }] }
    : { mode: "people", you: { uci: body.uci, san: "e4", p: 0.41 }, rating: 1500,
        people: [{ uci: "e2e4", san: "e4", p: 0.41 }, { uci: "d2d4", san: "d4", p: 0.31 }, { uci: "g1f3", san: "Nf3", p: 0.09 }] });
  if (path === "/api/build" || path === "/api/import") { job = RUNNING; return reply({ job, joined: true }); }
  if (path === "/api/new") server = { ...base, human_color: body.color, human_to_move: body.color === "white", opponent_to_move: body.color !== "white" };
  if (path === "/api/move") {
    await new Promise(r => setTimeout(r, 5));  // the server takes a moment
    server = { ...server, human_to_move: false, opponent_to_move: true, last_move: body.uci,
      fen: "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1", turn: "black", san_moves: ["e4"],
      whisper: { kind: "unusual", position: "008341", likeness: 0.04, rank: 7, p: 0.01, ply: 0 } };
  }
  if (path === "/api/opponent") server = { ...server, human_to_move: true, opponent_to_move: false, last_move: "e7e5", turn: "white",
    fen: "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2", san_moves: ["e4", "e5"],
    last_choice: { uci: "e7e5", san: "e5", probability: 0.5, latency_ms: 12 } };
  if (path === "/api/resign") server = { ...server, over: { result: "0-1", reason: "resignation" }, human_to_move: false };
  return reply(server);
};

eval(src);
const P = window.__pco;
const tick = () => new Promise(r => setTimeout(r, 15));
const assert = (cond, msg) => { if (!cond) throw new Error(msg); };
const FORBIDDEN = /\b(ai|bot|engine|computer)\b/i;

(async () => {
  P.ui.noPace = true;
  await tick(); await tick();
  assert(!byId["s-home"].hidden && byId["s-game"].hidden, "home shown first");
  assert(byId["home-question"].textContent === "What do you want to do with yourself?", "the question");
  assert(byId["ticker-line"].textContent === "10,734 games · 6 years · built 24.10.26", "summary " + byId["ticker-line"].textContent);

  // your years live on the home now: oldest first, each playable, too-small years quiet
  const tiles = byId.years.children;
  assert(tiles.map(t => t.attrs["aria-label"]).join() === "Play You · 2021,2022: too few games to meet,Play You · 2026", "years in order");
  assert(tiles[1].disabled && !tiles[0].disabled && tiles[2].className.includes("now"), "playable vs not, and now");
  assert(tiles[0].innerHTML.includes("1.e3") && tiles[0].innerHTML.includes("width:39%"), "opening + volume bar");
  assert(!byId["home-years"].hidden, "years row shown");

  // the summary line keeps telling you things, and holds still while you hover it
  const facts = P.tickerFacts(HOME, YEARS);
  assert(facts[0] === "10,734 games · 6 years · built 24.10.26" && facts.length >= 5, "facts " + facts);
  assert(facts.includes("1.d4 in 66% of your games as White this year") && facts.includes("Most games in 2026: 2,158"), "real facts");
  P.tickTo(facts[1]);
  assert(byId["ticker-line"].textContent === facts[1], "ticks");
  // it moves like something with weight: a spring that settles, with one small overshoot
  const path = [];
  await new Promise(done => P.spring(14, 0, { k: 80, c: 13 }, x => { path.push(x); return true; }, done));
  const low = Math.min(...path);
  assert(path[path.length - 1] === 0 && low < 0 && low > -1.5, "spring settles softly, lowest " + low);
  assert(P.dwellFor("a longer line takes longer to read") > P.dwellFor("short") && P.dwellFor("short") >= 5000, "slow, reading-paced");
  await byId["home-summary"].dispatch("mouseenter");
  assert(P.ui.tickerHold === true, "hover holds");
  await byId["home-summary"].dispatch("mouseleave");
  assert(P.ui.tickerHold === false, "and lets go");

  // theme and board, chosen in Settings, live on the page root
  P.ui.theme = "dark"; P.ui.board = "walnut"; P.applyAppearance();
  assert(document.documentElement.dataset.theme === "dark" && document.documentElement.dataset.board === "walnut", "appearance applied");
  P.ui.theme = "system"; P.ui.board = "paper"; P.applyAppearance();
  assert(!("theme" in document.documentElement.dataset) && !("board" in document.documentElement.dataset), "system + paper are the defaults");
  // the pink and blue rooms apply at once, and bring their own board when you're still on Paper
  P.chooseTheme("pink");
  assert(document.documentElement.dataset.theme === "pink" && P.ui.board === "blush" && document.documentElement.dataset.board === "blush", "pink room + blush board");
  P.ui.board = "moss"; P.chooseTheme("blue");
  assert(document.documentElement.dataset.theme === "blue" && P.ui.board === "moss", "a board you chose is kept");
  P.ui.board = "paper"; P.chooseTheme("blue");
  assert(P.ui.board === "terminal", "blue room + terminal board");
  P.ui.board = "paper"; P.chooseTheme("system");
  assert(!("theme" in document.documentElement.dataset) && !("board" in document.documentElement.dataset), "back to system");

  await tiles[0].click(); await tick(); await tick();
  assert(P.ui.route === "meet" && P.ui.version === "year-2021", "a year goes straight to meeting that you");
  P.ui.version = "current";
  P.go(""); await tick(); await tick();
  assert(byId["home-play"].textContent === "Play yourself", "primary action");

  // show, don't tell: a real position you know; answer it, and see yourself and your Twin
  await tick();
  assert(byId["glimpse-prompt"].textContent === "You've been here 80 times. Your move.", "glimpse prompt");
  await P.glimpseClick("e2"); await P.glimpseClick("e4"); await tick();
  assert(JSON.stringify(calls.find(c => c[0] === "/api/glimpse" && c[1])[1]) === '{"id":7,"uci":"e2e4"}', "glimpse answer sent");
  assert(byId["glimpse-you"].textContent === "You've played e4 here twice. Usually d4: 63 of 80.", "you line: " + byId["glimpse-you"].textContent);
  assert(byId["glimpse-twin"].textContent === "Your Twin would play d4.", "twin line");
  assert(byId["glimpse-machine"].innerHTML.includes('<span class="san">d4</span> 0.71'), "twin distribution keeps SAN case");
  assert(byId["glimpse-board"].children.find(g => g.attrs.class === "arrows").children.length === 2, "two arrows");
  assert(!byId["glimpse-again"].hidden && byId["glimpse-again"].textContent === "Another position", "another");

  // first visit: nothing to redirect to; the home itself shows how it works
  home = { ...HOME, games: 0, has_twin: false, player: null, built: null, years: [] }; glimpseMode = "people";
  P.go(""); await tick(); await tick(); await tick();
  assert(P.ui.route === "home" && byId["home-question"].textContent === "Play against yourself.", "first-visit headline");
  assert(byId["home-play"].textContent === "Show me your chess", "first-visit action");
  assert(byId["glimpse-prompt"].textContent === "Make a first move.", "people prompt");
  await P.glimpseClick("e2"); await P.glimpseClick("e4"); await tick();
  assert(byId["glimpse-you"].textContent === "About 41% of players near 1500 would play e4 too.", "people line");
  assert(byId["glimpse-twin"].textContent === "Yours will learn which one you would play.", "why line");
  assert(byId["glimpse-again"].textContent === "Try another move", "try again");
  home = { ...HOME }; glimpseMode = "yours";

  // the ritual: YOU -> YOU -> PLAY, with versions framed as yourself
  P.go("meet"); await tick(); await tick();
  const names = byId.versions.children.map(b => b.textContent);
  assert(JSON.stringify(names) === JSON.stringify(["Current You", "Older You", "Blitz You"]), "versions " + names);
  assert(byId["meet-basis"].innerHTML.startsWith("STYLE · OPENINGS · STRENGTH · 2026 · RATING 1135"), "basis line");
  await byId.versions.children[1].click();
  assert(P.ui.version === "year-2021", "older you = the oldest year");
  await byId.versions.children[0].click();
  P.ui.side = "white";
  await P.meet(); await tick(); await tick();
  assert(JSON.stringify(calls.find(c => c[0] === "/api/new")[1]) === '{"version":"current","color":"white"}', "new game sent");
  assert(P.ui.route === "game" && !byId["s-game"].hidden, "game shown");
  assert(P.layers.squares.children.length === 64 && P.layers.pieces.children.length === 32, "board drawn");
  assert(byId.status.textContent === "Your move.", "status " + byId.status.textContent);
  assert(byId["twin-meta"].textContent === "Current You · 1135", "opponent identity " + byId["twin-meta"].textContent);

  // click-to-move: the piece moves before the server answers; then a whisper and the reply
  P.clickSquare("e2");
  assert(P.ui.selected === "e2" && P.layers.dots.children.length === 2, "e2 selected with 2 targets");
  const moved = P.playMove("e2e4");
  assert(P.layers.pieces.children.some(u => u.attrs["data-sq"] === "e4") && !P.layers.pieces.children.some(u => u.attrs["data-sq"] === "e2"),
    "move shown at once");
  await moved; await tick();
  assert(JSON.stringify(calls.find(c => c[0] === "/api/move")[1]) === '{"uci":"e2e4"}', "move sent");
  assert(calls.some(c => c[0] === "/api/opponent"), "the Twin replies after your move");
  assert(byId["whisper-text"].textContent === "That was unusual.", "whisper " + byId["whisper-text"].textContent);
  assert(byId["whisper-machine"].textContent === "POSITION 008341 · FAMILIARITY 0.04", "whisper machine voice");
  assert(!byId.whisper.classList.contains("off"), "whisper visible");

  // local move application: castling, en passant, promotion
  let pcs = P.afterMove(P.parseBoard("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"), "e1g1");
  assert(pcs.g1 === "K" && pcs.f1 === "R" && !pcs.h1 && !pcs.e1, "castling moves the rook");
  pcs = P.afterMove(P.parseBoard("8/8/8/3pP3/8/8/8/4K2k w - d6 0 1"), "e5d6");
  assert(pcs.d6 === "P" && !pcs.d5, "en passant removes the pawn");
  pcs = P.afterMove(P.parseBoard("8/P6k/8/8/8/8/8/K7 w - - 0 1"), "a7a8n");
  assert(pcs.a8 === "N", "promotion shows the chosen piece");

  // promotion: nothing is sent until a piece is chosen; order Q, N, R, B
  server = { ...base, fen: "8/P6k/8/8/8/8/7r/K7 w - - 0 1", legal_moves: ["a7a8q", "a7a8r", "a7a8b", "a7a8n", "a1b1"],
    legal: [{ uci: "a7a8q", san: "a8=Q" }, { uci: "a7a8r", san: "a8=R" }, { uci: "a7a8b", san: "a8=B" }, { uci: "a7a8n", san: "a8=N" }, { uci: "a1b1", san: "Kb1" }] };
  await P.api("/api/state");
  const before = calls.length;
  P.clickSquare("a7"); P.clickSquare("a8"); await tick();
  assert(calls.length === before && !byId.promo.hidden, "promotion chooser shown, nothing sent");
  assert(byId.promo.children.map(b => b.dataset.uci).join() === "a7a8q,a7a8n,a7a8r,a7a8b", "Q N R B order");
  await byId.promo.children[1].click(); await tick(); await tick();
  assert(calls.find((c, i) => i >= before && c[0] === "/api/move")[1].uci === "a7a8n", "knight promotion sent");

  // typed moves: SAN, UCI, castling with zeros, prefixes
  server = { ...base, legal: [{ uci: "g1f3", san: "Nf3" }, { uci: "b1c3", san: "Nc3" }, { uci: "e1g1", san: "O-O" }, { uci: "e2e4", san: "e4" }] };
  await P.api("/api/state");
  assert(P.resolveTyped("Nf").match.uci === "g1f3", "unique prefix");
  assert(P.resolveTyped("N").match === null && P.resolveTyped("N").candidates.length === 2, "ambiguous prefix");
  assert(P.resolveTyped("e2e4").match.san === "e4", "uci");
  assert(P.resolveTyped("0-0").match.uci === "e1g1", "castling with zeros");
  assert(P.paceFor({ probability: 1 }) === 250 && P.paceFor({ probability: 0 }) === 1000, "pacing bounds");

  // two-step resign, then the reflection
  const r0 = calls.length;
  await byId.resign.click();
  assert(calls.length === r0 && byId.resign.textContent === "Confirm resign", "first click only arms");
  await byId.resign.click(); await tick(); await tick(); await tick();
  assert(calls.some((c, i) => i >= r0 && c[0] === "/api/resign"), "second click resigns");
  assert(P.ui.route === "reflection", "game over leads to the reflection, route " + P.ui.route);
  await tick(); await tick();
  assert(byId["ref-title"].textContent === "YOU WON." && byId["ref-sub"].textContent === "Barely.", "won, barely");
  assert(byId["ref-match"].textContent === "That was very you.", "very you");
  assert(byId["ref-match-machine"].textContent === "MATCH 0.90 · YOU USUALLY 0.82 · 20 DECISIONS", "match machine line");
  assert(byId["ref-parted"].textContent === "You and your Twin parted ways 4 times.", "parted");
  assert(byId["ref-actions"].classList.contains("on"), "actions revealed last");

  // see the difference: your move in ochre, the Twin's in cobalt
  P.go("review", "difference"); await tick(); await tick();
  assert(byId["diff-you"].textContent === "You chose 1.e4." && byId["diff-twin"].textContent === "Your Twin would have chosen 1.d4.", "difference lines");
  assert(byId["diff-verdict"].textContent === "Both were plausible.", "verdict");
  assert(byId["diff-history"].textContent === "You have made this kind of move 73 times before.", "history");
  const here = P.differenceCopy({ ...REFLECTION.divergences[0], here_twin: 412, here_human: 2, here_total: 420 });
  assert(here.history === "In this position, you have played d4 412 times, and e4 twice.", "grounded history: " + here.history);
  assert(here.machine.startsWith("POSITION 008341 · SEEN 420×"), "seen here");
  assert(byId["diff-machine"].textContent.startsWith("PAWN · QUIET · OPENING · D4 · N 73"), "kind machine line");
  assert(byId["review-board"].children.find(g => g.attrs.class === "arrows").children.map(a => a.attrs.class).join() === "arrow twin,arrow you", "two arrows");
  P.go("review", "replay"); await tick(); await tick();
  assert(byId["replay-count"].textContent === "1 / 1" && byId["replay-status"].textContent === "The end.", "replay at the end");

  // words: outcomes and the copy never call the Twin an AI, bot, engine or computer
  const C = P.reflectionCopy;
  assert(C({ ...REFLECTION, outcome: "lost", highest_eval: 200 }).sub === "You were better, once.", "lost after being better");
  assert(C({ ...REFLECTION, outcome: "lost", highest_eval: 20 }).sub === "It knew you well.", "lost");
  assert(C({ ...REFLECTION, outcome: "resigned", highest_eval: 20, match: 0.4 }).sub === "You weren't quite yourself.", "unlike you");
  assert(C({ ...REFLECTION, outcome: "won", lowest_eval: 10 }).sub === "Cleanly.", "clean win");
  assert(C({ ...REFLECTION, match: 0.5 }).match === "That wasn't quite you.", "not quite you");
  assert(C({ ...REFLECTION, differed: 1 }).parted === "You and your Twin parted ways once.", "once");
  const copies = [P.statusText(), P.homeSummary(HOME), ...Object.values(C(REFLECTION)),
    ...Object.values(P.differenceCopy(REFLECTION.divergences[0])),
    ...["new", "unusual", "familiar"].flatMap(k => Object.values(P.whisperCopy({ kind: k, position: "1", likeness: 0.1, seen: 3 }))),
    byId["glimpse-you"].textContent, byId["glimpse-twin"].textContent, byId["home-question"].textContent,
    P.timeLeft(100), P.estimateLine({ seconds: 10, first: true }), P.estimateLine({ seconds: 10, rereads: true }), ...facts];
  for (const text of copies) assert(!FORBIDDEN.test(String(text || "")), "forbidden word in: " + text);

  server = { ...base, over: { result: "1-0", reason: "checkmate" } }; await P.api("/api/state");
  assert(P.statusText() === "Checkmate. You won.", "win phrasing");
  server = { ...base, human_color: "black", over: { result: "1-0", reason: "checkmate" } }; await P.api("/api/state");
  assert(P.statusText() === "Checkmate. Your Twin won.", "loss phrasing");

  // time, in words that never look more exact than they are
  assert(P.timeLeft(20) === "Less than a minute left." && P.timeLeft(70) === "About a minute left.", "short times");
  assert(P.timeLeft(260) === "About 4 minutes left." && P.timeLeft(1560) === "About 25 minutes left.", "minutes, coarser when long");
  assert(P.timeLeft(4900) === "About 1 hour 20 minutes left." && P.timeLeft(null) === "", "hours, and no number without a basis");
  assert(P.estimateLine({ seconds: 1840, first: true, basis: "measured" }) === "ROUGHLY 30 MIN · THE FIRST TIME", "first estimate");
  assert(P.estimateLine({ seconds: 3900, rereads: true }) === "ABOUT 1 H 05 MIN · EVERY GAME IS READ AGAIN AT THE NEW DEPTH", "depth change");
  assert(P.estimateLine({ seconds: 100, new_decisions: 1200 }) === "ABOUT 2 MIN · 1,200 NEW DECISIONS TO READ", "rebuild");

  // work in progress is joined, never refused, and follows you around the app
  P.go("settings"); await tick(); await tick();
  await P.pollJob();
  P.ui.jobAcknowledged = false;
  const b0 = calls.length;
  await byId["set-rebuild"].click(); await tick(); await tick();
  assert(calls.some((c, i) => i >= b0 && c[0] === "/api/build") && P.ui.route === "build", "rebuild joins and shows the work");
  assert(byId["build-head"].textContent === "Building your Twin." && byId["build-eta"].textContent === "About 25 minutes left.", "build head + eta");
  assert(byId["build-eta-machine"].textContent === "ELAPSED 3:12 · FROM THE PACE SO FAR", "basis is stated");
  assert(byId["build-first"].textContent === "The first time is the slowest. After this, only new games are read." && !byId["build-first"].hidden, "first-time note");
  assert(byId.stages.children.map(li => li.className).join() === "now,todo,todo,todo,todo", "how far along");
  assert(byId.stages.children[0].children[1].textContent === "DECISION 26,404 OF 66,011 · 52/S", "current stage detail");
  assert(!byId["build-leave"].hidden, "you may leave");
  P.go(""); await tick(); await tick();
  assert(!byId["job-pill"].hidden && byId["job-pill-text"].innerHTML.includes("ABOUT 25 MIN"), "pill elsewhere: " + byId["job-pill-text"].innerHTML);
  P.renderJob({ ...RUNNING, kind: "import", stage: "COMPARING DECISIONS", stages: ["FINDING YOUR GAMES", "REMEMBERING", "FINDING PATTERNS", "COMPARING DECISIONS"],
    durations: { "FINDING YOUR GAMES": 75, REMEMBERING: 1490, "FINDING PATTERNS": 300 }, result: { found: 4862 }, meta: {} });
  assert(byId["build-head"].textContent === "4,862 games found." && byId["build-sub"].textContent === "We're going through them.", "import then build words");
  assert(byId.stages.children.map(li => li.className).join() === "done,done,done,now,todo,todo", "stages done and to come");
  assert(byId.stages.children[1].children[1].textContent === "24:50", "finished stages keep their time");
  job = null;

  console.log("ui checks passed:", calls.length, "requests");
  process.exit(0);
})().catch(e => { console.error("FAIL", e.message); process.exit(1); });
