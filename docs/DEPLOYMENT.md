# Self-hosted deployment: chess.madebyshaad.com

The page is public. The engine, your games and your Twin stay on your computer.

```
 visitor's browser
   │  loads page + fonts (static files only)         ┌──────────────────────────┐
   ├────────────────────────────────────────────────▶│ chess.madebyshaad.com    │
   │                                                 │ Cloudflare Pages, no code│
   │  API calls: GET/POST http://127.0.0.1:8765      └──────────────────────────┘
   ▼
 pco play  (your machine, 127.0.0.1 only)
   ├─ Maia-3, Stockfish, torch (CPU)
   └─ $PCO_HOME  (all data, below)
```

The browser itself makes the API calls, so "the backend" is whatever listens on
`127.0.0.1:8765` of the machine running that browser. Only the person who runs
`pco play` has a working app. For anyone else the page shows "Lost the connection."

`pco play` still serves the same page itself at `http://127.0.0.1:8765/`. That
needs no domain and no CORS, and it is the fallback when a browser blocks the
hosted page (see Browser support).

## 1. Domain and hosting requirements

| Item | Requirement |
|---|---|
| Domain | `madebyshaad.com`, registered at and using Cloudflare DNS. Subdomain: `chess` |
| Static host | Cloudflare Pages (free), a "Direct Upload" project. Any static host works if it serves `dist/` over HTTPS. The `_headers` file is Cloudflare/Netlify format |
| HTTPS | Automatic certificate from Cloudflare. The page must be HTTPS: a browser lets an HTTPS page call `http://127.0.0.1` but not other plain-HTTP addresses |
| Backend | Not hosted. No tunnel, no port forwarding, no proxy record |
| Accounts | Cloudflare only. No AWS, GCP or other cloud |

What leaves your machine: the static files of `dist/`, once per deploy. No games,
profiles, settings or logs are ever in `dist/` or on the host.

## 2. Build the frontend

```sh
uv run python scripts/build_frontend.py                     # writes dist/
uv run python scripts/build_frontend.py --api-base http://127.0.0.1:9000   # other port
```

`dist/` (git-ignored) contains:

| File | Purpose |
|---|---|
| `index.html` | the app page, with `<meta name="pco-api">` set to the backend origin |
| `static/fonts/*` | the bundled fonts and their OFL licenses |
| `_headers` | CSP limiting the page to its own files plus `connect-src <backend>`, `nosniff`, no referrer, `frame-ancestors 'none'`, no-cache for the page and long cache for fonts |

The build overwrites files in the output directory and deletes nothing. The page
itself is unchanged by the build except for that one `<meta>` value; with it empty
(the default, as `pco play` serves it) the page calls its own origin.

## 3. Deploy to chess.madebyshaad.com (one time)

1. Cloudflare dashboard → Workers & Pages → Create → Pages → **Upload assets**.
   Name the project, upload the `dist/` folder, deploy.
   (CLI alternative, needs Node: `npx wrangler pages deploy dist --project-name <name>`.)
2. Project → Custom domains → add `chess.madebyshaad.com`. Because the zone is on
   Cloudflare, it creates the DNS record for you. Wait for the certificate to turn active.
3. Turn on two-step login for the Cloudflare account (see Security).

Redeploy after any change by rebuilding and uploading `dist/` again. To roll back,
use the project's Deployments list.

## 4. Run the backend

```sh
uv run pco play --no-browser
```

`https://chess.madebyshaad.com` is allowed by default, so users of the hosted page need
no flags. Open the site; the startup line confirms it:
`API also open to pages at: https://chess.madebyshaad.com`.
Use `--no-hosted` to refuse the hosted page.

- The server still binds `127.0.0.1` only and still rejects foreign `Host` headers.
- `--allow-origin` adds more origins (a second domain, a test host). It takes exact origins (`scheme://host[:port]`), repeatable, comma-separated in
  `PCO_ALLOW_ORIGIN`. Wildcards, paths and `null` are refused at startup.
- Any request whose `Origin` header is not the server's own page or a listed origin gets
  403, including simple GETs. Without the flag, no other website can talk to the server.
- Replies to a listed origin carry `Access-Control-Allow-Origin` and, for Chromium's
  Private Network Access preflight, `Access-Control-Allow-Private-Network: true`.
- Port 8765 is the default. If you change `--port`, rebuild with the same `--api-base`.

Check it by hand:

```sh
curl -si -X OPTIONS http://127.0.0.1:8765/api/home \
  -H 'Origin: https://chess.madebyshaad.com' \
  -H 'Access-Control-Request-Method: GET' \
  -H 'Access-Control-Request-Private-Network: true'     # expect 204 and both Allow headers
curl -si http://127.0.0.1:8765/api/home -H 'Origin: https://evil.example'   # expect 403
```

### Page and app versions

The page and the app are released separately, so they check each other. The app answers
`GET /api/version` with `{"api": N, "app": "x.y.z"}`; `API_VERSION` in
`src/pco/play/server.py` and `PAGE_API` in the page must match (a test enforces it).
Bump both on any API change an older page or app could not cope with, then rebuild and
redeploy `dist/`. On a hosted page the user sees one of three screens instead of the app:

| Situation | Screen |
|---|---|
| no app answering on the port | "Start the app on this machine." with `pco play --no-browser` and *Try again* |
| app older than the page (or from before this check) | "Your app is out of date." |
| app newer than the page | "This page is out of date." with *Reload* |

The "How to get it" link on that screen points at the README's Start section on GitHub
(`https://github.com/shaadart/you-vs.-you#start`); change it in `index.html` if the
repository moves.

### Browser support

Automated tests cover the server headers, the origin rules, the build output and the
page script against a fake DOM. The owner confirmed the hosted page works against a
local backend (2026-11-03; browser not recorded). Other browsers are untested. What to expect:

- **Chrome and other Chromium browsers:** an HTTPS page may call `http://127.0.0.1`.
  Recent versions may ask once for permission to reach local network devices; allow it.
- **Firefox and Safari:** may block an HTTPS page from reaching `http://127.0.0.1`
  (mixed content or local-network rules). If so, use `http://127.0.0.1:8765/`.
- The "Last game as PGN" link is a plain link to the backend; a browser that treats the
  download as insecure may block it. The local address always works.

## 5. Where data lives

Everything is under one directory, `$PCO_HOME`, or `--home DIR` on any command.
Default on Linux: `$XDG_DATA_HOME/pco`, else `~/.local/share/pco`
(macOS `~/Library/Application Support/pco`, Windows `%LOCALAPPDATA%\pco`).

| Path | Holds | If lost |
|---|---|---|
| `pco.sqlite3` | imported games and decisions | **Irreplaceable** except by re-importing from Chess.com/Lichess; back it up |
| `preferences.json` | speed, workers, temperature | defaults return |
| `profiles/` | trained Twins | rebuild from games (`pco train`) |
| `played/` | PGNs of games you played against the Twin | not recoverable; never imported as training data |
| `cache/analysis.sqlite3` | Maia/Stockfish analysis cache | safe to delete; rebuilt on demand |
| `reports/` | evaluation reports (JSON) | rerun `pco eval` |
| `assets/` | verified model weights and Stockfish | `pco setup` downloads again |

To move data off the default location: `export PCO_HOME=/path`, or put it inside the
repo at `.pco/` (git-ignored). Back up by copying the directory while `pco play` is stopped.

Theme and board style are kept by the browser (`localStorage`), per site: the hosted page and
`http://127.0.0.1:8765/` each remember their own.

The only network traffic of the backend is the existing behavior: reading your public
games from Chess.com/Lichess when you ask, and the one-time model/Stockfish download.
This setup adds none.

## 6. Security notes

Anything that can run JavaScript on `https://chess.madebyshaad.com` can read, change and
forget your local data (including "forget everything", which needs only the typed word
`FORGET`). So the origin is a trust boundary:

- Turn on two-step login for the Cloudflare account that owns the domain and the Pages project.
- Keep the project static. Do not add Workers, Pages Functions, analytics, third-party scripts
  or other pages under `chess.madebyshaad.com`, and do not host other apps on sibling paths.
- Do not allow wildcard or neighbor origins.
- Stop `pco play` when you do not need it. The API is only reachable while it runs.
- To drop remote access, run `pco play --no-hosted`.

## 7. Later: security scanning (not done)

Not implemented yet. Natural first steps, each to be added only with a measured result:
dependency audit of `uv.lock`, scanning `dist/` and the repo for secrets, a header check
of the deployed site against `_headers`, and a CSP/CORS regression test in CI.
