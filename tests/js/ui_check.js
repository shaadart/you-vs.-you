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
  assert(byId["home-summary"].textContent === "10,734 games · 6 years · built 24.10.26", "summary " + byId["home-summary"].textContent);
  assert(byId["home-play"].textContent === "Play yourself", "primary action");

  // no games yet: the home gives way to "SHOW ME YOUR CHESS."
  home = { ...HOME, games: 0, has_twin: false, player: null, built: null };
  P.go(""); await tick(); await tick(); await tick();
  assert(P.ui.route === "import" && !byId["s-import"].hidden, "onboarding redirect, route " + P.ui.route);
  home = { ...HOME };

  // the ritual: YOU -> YOU -> PLAY, with versions framed as yourself
  P.go("meet"); await tick(); await tick();
  const names = byId.versions.children.map(b => b.textContent);
  assert(JSON.stringify(names) === JSON.stringify(["Current You", "Older You", "Blitz You", "Archive →"]), "versions " + names);
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
    ...["new", "unusual", "familiar"].flatMap(k => Object.values(P.whisperCopy({ kind: k, position: "1", likeness: 0.1, seen: 3 })))];
  for (const text of copies) assert(!FORBIDDEN.test(String(text || "")), "forbidden word in: " + text);

  server = { ...base, over: { result: "1-0", reason: "checkmate" } }; await P.api("/api/state");
  assert(P.statusText() === "Checkmate. You won.", "win phrasing");
  server = { ...base, human_color: "black", over: { result: "1-0", reason: "checkmate" } }; await P.api("/api/state");
  assert(P.statusText() === "Checkmate. Your Twin won.", "loss phrasing");

  // building: stages appear as they are reached, never a progress bar
  P.renderJob({ stage: "COMPARING DECISIONS", stages: ["FINDING YOUR GAMES", "REMEMBERING", "FINDING PATTERNS", "COMPARING DECISIONS"],
    fragments: ["DECISION 1 OF 2 · 60/S"], status: "running", result: { found: 4862 } });
  assert(byId["build-head"].textContent === "4,862 games found." && byId["build-sub"].textContent === "We're going through them.", "build words");
  assert(byId.stages.children.map(li => li.textContent).join() === "REMEMBERING,FINDING PATTERNS,COMPARING DECISIONS", "stages so far");
  assert(byId.stages.children[2].className.includes("now"), "current stage marked");

  console.log("ui checks passed:", calls.length, "requests");
  process.exit(0);
})().catch(e => { console.error("FAIL", e.message); process.exit(1); });
