// Exercises the play page script (click-to-move, opponent request, promotion picker)
// against minimal DOM/fetch stubs. Run by tests/test_ui_js.py when node is available.
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8").match(/<script>([\s\S]*)<\/script>/)[1];
function node() { return { attrs: {}, children: [], listeners: {}, style: {}, hidden: false, textContent: "", innerHTML: "", value: "",
  setAttribute(k, v) { this.attrs[k] = v; }, addEventListener(t, f) { this.listeners[t] = f; },
  append(...c) { this.children.push(...c); }, replaceChildren() { this.children = []; } }; }
const els = {};
global.document = { getElementById: id => (els[id] ||= node()), createElementNS: () => node(), createElement: () => node() };
global.alert = m => { alerts.push(m); };
const alerts = [], calls = [];
let state = { fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", turn: "white", human_color: "white",
  human_to_move: true, opponent_to_move: false, legal_moves: ["e2e4", "e2e3", "g1f3"], san_moves: [], last_move: null,
  check: false, over: null, opponent: { label: "L", elo: 1500, temperature: 1 }, last_choice: null, error: null, saved_pgn: null };
global.fetch = async (path, opts) => { calls.push([path, opts && opts.body]);
  if (path === "/api/move") state = { ...state, human_to_move: false, opponent_to_move: true, last_move: JSON.parse(opts.body).uci };
  if (path === "/api/opponent") state = { ...state, human_to_move: true, opponent_to_move: false,
    last_choice: { uci: "e7e5", san: "e5", probability: .5, top: [{ uci: "e7e5", san: "e5", probability: .5 }], latency_ms: 10, source: "m" } };
  return { ok: true, json: async () => state }; };
eval(src + "\n;global.__t = { clickSquare, showPromotion, api };");
const tick = () => new Promise(r => setTimeout(r, 20));
(async () => {
  await tick();
  const svgRects = () => els.board.children.filter(c => c.attrs.width === 10 && c.listeners.click).length;
  if (svgRects() !== 64) throw new Error("expected 64 clickable squares, got " + svgRects());
  __t.clickSquare("e2"); await tick();
  const dots = els.board.children.filter(c => c.attrs.r !== undefined).length;
  if (dots !== 2) throw new Error("expected 2 target markers for e2, got " + dots);
  __t.clickSquare("e4"); await tick(); await tick();
  const paths = calls.map(c => c[0]);
  if (!paths.includes("/api/move") || JSON.parse(calls.find(c => c[0] === "/api/move")[1]).uci !== "e2e4") throw new Error("move not sent: " + JSON.stringify(calls));
  if (!paths.includes("/api/opponent")) throw new Error("opponent not requested after human move");
  if (!els.choice.innerHTML.includes("Played <b>e5</b>")) throw new Error("choice panel not rendered");
  // Promotion: two legal moves share from/to -> picker instead of a move
  state = { ...state, fen: "8/P6k/8/8/8/8/7r/K7 w - - 0 1", legal_moves: ["a7a8q", "a7a8r", "a7a8b", "a7a8n", "a1b1"], human_to_move: true };
  await __t.api("/api/state");
  const before = calls.length;
  __t.clickSquare("a7"); __t.clickSquare("a8"); await tick();
  if (calls.length !== before) throw new Error("promotion should not send a move before choosing a piece");
  if (els.promo.hidden || els.promo.children.length !== 4) throw new Error("promotion picker not shown");
  els.promo.children[3].listeners.click(); await tick();
  if (JSON.parse(calls[before][1]).uci !== "a7a8n") throw new Error("wrong promotion move " + calls[before][1]);
  if (alerts.length) throw new Error("unexpected alerts " + alerts);
  console.log("ui checks passed:", calls.map(c => c[0]).join(" "));
})().catch(e => { console.error("FAIL", e.message); process.exit(1); });
