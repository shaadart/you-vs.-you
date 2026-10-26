// Exercises the play page script (docs/DESIGN.md "Mirror") against a small fake DOM and a
// fake server: selection and dots, click-to-move, the opponent reply, ghost arrows from the
// spectrum, the always-shown played move, promotion order, typed moves, pacing, two-step
// resign, status phrasing, and that alert() is never used. Run by tests/test_ui_js.py.
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8").match(/<script>([\s\S]*)<\/script>/)[1];

class FakeEl {
  constructor(tag) {
    Object.assign(this, { tagName: String(tag).toUpperCase(), children: [], attrs: {}, style: {}, dataset: {},
      listeners: {}, hidden: false, textContent: "", innerHTML: "", className: "", value: "", disabled: false,
      open: false, returnValue: "", scrollTop: 0, scrollHeight: 0 });
    const set = new Set();
    this.classList = { add: (...c) => c.forEach(x => set.add(x)), remove: (...c) => c.forEach(x => set.delete(x)),
      toggle: (c, on) => { const v = on === undefined ? !set.has(c) : !!on; v ? set.add(c) : set.delete(c); return v; },
      contains: c => set.has(c) };
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
  dispatch(t, ev = {}) { return Promise.all((this.listeners[t] || []).map(f => f({ target: this, preventDefault() {}, ...ev }))); }
  appendChild(c) { this.children.push(c); return c; }
  append(...c) { this.children.push(...c); }
  replaceChildren(...c) { this.children = c; }
  focus() {} blur() {} setPointerCapture() {} showModal() { this.open = true; } close() { this.open = false; }
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
global.localStorage = { getItem: () => null, setItem() {} };
global.requestAnimationFrame = cb => setTimeout(cb, 0);
global.alert = m => { throw new Error("alert() must not be used: " + m); };

const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const base = {
  fen: START, turn: "white", human_color: "white", human_to_move: true, opponent_to_move: false,
  legal_moves: ["e2e4", "e2e3", "g1f3"],
  legal: [{ uci: "e2e4", san: "e4" }, { uci: "e2e3", san: "e3" }, { uci: "g1f3", san: "Nf3" }],
  san_moves: [], last_move: null, check: false, over: null, error: null, saved_pgn: null, last_choice: null,
  opponent: { label: "L", personal: true, name: "me", model: "2026-10-24T00:00:00Z", elo: 1500, temperature: 0.9, default_temperature: 0.9 },
};
let server = { ...base };
const calls = [];
const reply = s => ({ ok: true, json: async () => s });
global.fetch = async (path, opts) => {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  calls.push([path, body]);
  if (path === "/api/move") server = { ...server, human_to_move: false, opponent_to_move: true, last_move: body.uci,
    fen: "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1", turn: "black", san_moves: ["e4"] };
  if (path === "/api/opponent") server = { ...server, human_to_move: true, opponent_to_move: false, last_move: "e7e5", turn: "white",
    fen: "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2", san_moves: ["e4", "e5"],
    last_choice: { uci: "e7e5", san: "e5", probability: 0.5, likeness: 1, latency_ms: 12,
      top: [{ uci: "e7e5", san: "e5", probability: 0.5 }, { uci: "c7c5", san: "c5", probability: 0.3 }] } };
  if (path === "/api/resign") server = { ...server, over: { result: "0-1", reason: "resignation" }, human_to_move: false };
  return reply(server);
};

eval(src);
const P = window.__pco;
const tick = () => new Promise(r => setTimeout(r, 15));
const assert = (cond, msg) => { if (!cond) throw new Error(msg); };

(async () => {
  await tick(); await tick();
  assert(P.layers.squares.children.length === 64, "64 squares");
  assert(P.layers.pieces.children.length === 32, "32 pieces");
  assert(byId.status.textContent === "Your move.", "status " + byId.status.textContent);

  P.ui.noPace = true;
  P.clickSquare("e2");
  assert(P.ui.selected === "e2" && P.layers.dots.children.length === 2, "e2 selected with 2 targets");
  P.clickSquare("e4"); await tick(); await tick();
  const paths = calls.map(c => c[0]);
  assert(JSON.stringify(calls.find(c => c[0] === "/api/move")[1]) === '{"uci":"e2e4"}', "move sent");
  assert(paths.includes("/api/opponent"), "opponent requested after the human move");
  assert(byId.decision.innerHTML.includes("e5") && byId.spectrum.children.length === 2, "decision + spectrum rendered");

  await byId.spectrum.children[1].dispatch("mouseenter");
  assert(P.ui.arrow === "c7c5" && P.layers.arrows.children.length === 1, "hover draws the candidate's arrow");
  await byId.spectrum.children[1].dispatch("mouseleave");
  assert(P.layers.arrows.children.length === 0, "arrow cleared");

  // the played move is always listed, even outside the top five
  server = { ...server, last_choice: { ...server.last_choice, uci: "a7a6", san: "a6", probability: 0.001 } };
  await P.api("/api/state");
  const rows = byId.spectrum.children;
  assert(rows.length === 3 && rows[2].className.includes("outlier") && rows[2].dataset.uci === "a7a6", "outlier row");
  assert(byId.decision.innerHTML.includes("p &lt;0.01") || byId.decision.innerHTML.includes("p <0.01"), "tiny p formatted");

  // promotion: no move until a piece is chosen; order Q, N, R, B
  server = { ...base, fen: "8/P6k/8/8/8/8/7r/K7 w - - 0 1", legal_moves: ["a7a8q", "a7a8r", "a7a8b", "a7a8n", "a1b1"],
    legal: [{ uci: "a7a8q", san: "a8=Q" }, { uci: "a7a8r", san: "a8=R" }, { uci: "a7a8b", san: "a8=B" }, { uci: "a7a8n", san: "a8=N" }, { uci: "a1b1", san: "Kb1" }] };
  await P.api("/api/state");
  const before = calls.length;
  P.clickSquare("a7"); P.clickSquare("a8"); await tick();
  assert(calls.length === before && !byId.promo.hidden, "promotion chooser shown, nothing sent");
  assert(byId.promo.children.map(b => b.dataset.uci).join() === "a7a8q,a7a8n,a7a8r,a7a8b", "Q N R B order");
  await byId.promo.children[1].dispatch("click"); await tick();
  assert(calls.find((c, i) => i >= before && c[0] === "/api/move")[1].uci === "a7a8n", "knight promotion sent");

  // typed moves: SAN, UCI, castling with zeros, prefixes
  server = { ...base, legal: [{ uci: "g1f3", san: "Nf3" }, { uci: "b1c3", san: "Nc3" }, { uci: "e1g1", san: "O-O" }, { uci: "e2e4", san: "e4" }] };
  await P.api("/api/state");
  assert(P.resolveTyped("Nf").match.uci === "g1f3", "unique prefix");
  assert(P.resolveTyped("N").match === null && P.resolveTyped("N").candidates.length === 2, "ambiguous prefix");
  assert(P.resolveTyped("e2e4").match.san === "e4", "uci");
  assert(P.resolveTyped("0-0").match.uci === "e1g1", "castling with zeros");
  assert(P.resolveTyped("Qh5").candidates.length === 0, "no such move");

  assert(P.paceFor({ probability: 1 }) === 250 && P.paceFor({ probability: 0 }) === 1000, "pacing bounds");

  // two-step resign
  const r0 = calls.length;
  await byId.resign.dispatch("click");
  assert(calls.length === r0 && byId.resign.textContent === "Confirm resign", "first click only arms");
  await byId.resign.dispatch("click"); await tick();
  assert(calls[calls.length - 1][0] === "/api/resign", "second click resigns");
  assert(byId.status.textContent === "You resigned.", "resign status");

  server = { ...base, over: { result: "1-0", reason: "checkmate" } }; await P.api("/api/state");
  assert(P.statusText() === "Checkmate. You won.", "win phrasing");
  server = { ...base, human_color: "black", over: { result: "1-0", reason: "checkmate" } }; await P.api("/api/state");
  assert(P.statusText() === "Checkmate. It won.", "loss phrasing");

  console.log("ui checks passed:", calls.length, "requests");
  process.exit(0);
})().catch(e => { console.error("FAIL", e.message); process.exit(1); });
