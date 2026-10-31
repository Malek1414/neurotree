# Neurotree: a 3D attention tree of Malek's Mac

**Status:** design approved section by section on 31 Oct 2026 (parts 1–2 explicitly; parts 3–5 delegated: "keep engineering all the parts and then build it").
**Scope of this spec:** stage 1 of 5: the indexer (Mac data -> one knowledge graph) and the 3D desktop viewer. Later stages, each with its own spec: (3) connect to Claude, (4) per-hackathon-project mini graphs from GitHub, (5) "neurons firing" embed on every project site.
**Reference:** Obsidian's graph view, studied for behavior only (REA runs in `~/rea-runs/`, notes in the session scratchpad). No Obsidian code, text or assets are used.

## Decisions (from the brainstorm)
| Question | Decision |
|---|---|
| Data in v1 | Work data + files: git repos (50 local, 60 on GitHub), 963 Claude Code sessions, claude-mem memory (27,868 observations), Documents/Desktop files. Mail, Messages, Calendar, Photos only later, each after Malek's explicit permission. |
| What fires harder | A blend, **attention first**: attention 45 %, history 25 %, structure 20 %, runtime 10 %, times a noise factor. Every node keeps its breakdown. |
| Shape | **Attention core**: Malek at the center, areas are branches, distance from the center follows attention (this week's focus close and bright, untouched things drift out and dim). |
| Areas | Proposed from co-attention clusters, reviewed by Malek (rename, merge, move), saved in `areas.json`; unsure items go to "Unsorted". |
| Build | **Desktop app**: Tauri 2 shell, Python indexer sidecar, SQLite store, web viewer (three.js + 3d-force-graph). Electron is the fallback if the WebKit performance gate fails. |

## 1. Architecture
- **Shell (Rust, Tauri 2):** main window with the tree, menubar icon (index status, Reindex, Open). Starts and supervises the indexer; watches source folders (`notify`, 2 s debounce): a change fires its node in the viewer immediately, and a full reindex runs at most every 10 minutes to place new nodes; exposes commands to the viewer: `open_path`, `reveal_in_finder`, `reindex`, `status`.
- **Indexer (Python 3.12+, package `neurotree`):** source adapters -> store -> scoring -> areas -> export. CLI: `python -m neurotree all`. Read-only towards every source. v1 runs from the repo's own `.venv` (no bundled Python); the shell finds it via `NEUROTREE_PYTHON` or `<repo>/.venv/bin/python`.
- **Store:** `~/Library/Application Support/Neurotree/graph.db` (SQLite, WAL). Tables: `nodes`, `edges`, `events`, `scores`, `meta`. Nothing leaves the Mac.
- **Viewer (TypeScript, Vite):** loads `graph.json` (exported view of the store), renders the 3D tree. Same package later builds the site embed (stage 5).

## 2. Data model and scoring
**Levels (center outward):** 0 `self` (Malek) -> 1 `area` -> 2 `project` -> 3 `file` -> 4 `detail` (functions; memory facts), level 4 loaded on demand.
- Project = one repo (local clone and GitHub copy merged by normalized remote URL) or one meaningful Documents/Desktop folder. GitHub-only repos are project nodes without files.
- File = code module (incl. json/yaml/toml config) or document (md, txt, pdf, docx, pptx, pages, xlsx, csv, rtf). `.key` is treated as a possible private key, so Keynote decks are skipped. Skipped: `.git`, `node_modules`, `.venv`, build/dist, caches, binaries, media over 50 MB.
**Edges:** `tree` (parent-child); arcs: `import` (module -> module), `co_attention` (touched in the same Claude session; weight = shared sessions), `mention` (md/txt file names a repo or file), `shared_code` (same file content hash in two projects).
**Events** (`item, ts, kind, weight, source`): Claude session file `read` 1, `edit`/`write` 3, path `mention` in a Bash command 0.5; claude-mem `files_read` 1, `files_modified` 3; git `commit` (weight log2(1+lines changed)); file `modified` (mtime) 0.5.
**Signals per node:**
- attention = sum(event weight * 0.5^(age/30 d)) over Claude + memory events.
- history = sum(commit weight * 0.5^(age/90 d)) + file mtime events.
- structure = PageRank inside the project's import graph (Python via AST, JS/TS via import/require parsing); other languages: no signal.
- runtime = share of time from a repograph `trace.json` if the project has one.
**Score:** each signal -> percentile rank among nodes of the same level; missing signals drop out and the remaining weights are renormalized; importance = weighted sum * noise factor (lockfiles, generated, vendored, fixtures, minified: 0.1; tests: 0.5); structure keeps a floor of 0.15 of its weight so essential but untouched code never goes fully dark. Parents roll up their children's raw attention and history, then are ranked within their own level.
**Breakdown:** each node stores `{attention, history, structure, runtime, noise, importance}` plus top evidence lines (e.g. "38 Claude touches in 30 d, 12 commits, imported by 9 modules").
**Areas:** project co-attention graph (shared sessions + shared memory projects) -> Louvain communities (networkx) -> names from the most frequent folder/README keywords -> `areas.json` (`{reviewed, areas: {slug: {name, color, projects[]}}}`); it stays a fresh proposal on every run until Malek reviews it, then his placement wins and new projects join the area holding most of their attention. Membership confidence < 0.4 -> "Unsorted".
**Labels:** first line of README / module docstring / document title. Claude-written labels come in stage 3.

## 3. Viewer and interactions
- **Look:** near-black background, bloom glow, instanced spheres; color = area; size and emissive strength = importance; tree links thin and dim, arcs curved and colored by kind.
- **Layout (custom forces on d3-force-3d):** each area gets a direction (Fibonacci points on a sphere); `branch` force pulls children toward their parent's direction; `radial` force pulls each node to r = levelBase(level) * (1 + 0.8 * (1 - attentionRank)); `self` pinned at the origin; collision keeps nodes apart. Hot nodes sit closer to the core within their level.
- **Firing:** particles travel along edges toward the core when an event happens; particle count, width and brightness scale with importance. Modes: **Live** (watcher events fire immediately), **Replay** (time-lapse over the last N days, speed slider, events in order), idle **breathing** (pulse rate follows 7-day activity).
- **Level of detail:** start with levels 0–2 plus the top files by importance (cap 6,000 visible nodes); clicking a project expands all its files; level 4 on demand.
- **Interactions** (parity with the reference behavior): hover highlights neighbors and dims the rest; click selects and opens a side panel (path, area, score breakdown with evidence, last activity, related memory facts, Open / Reveal in Finder); double-click flies the camera to the node; search box; **local mode** (selected node + depth 1–3); orbit with mouse, arrow keys pan (Shift faster), `/` search, `Esc` clears.
- **Settings panel:** Filters (search, code / documents / facts / orphans / noise on-off), Groups (area colors, custom query groups), Display (arrows, label fade, node size, link thickness, bloom), Forces (core pull, repel, link strength, link distance, branch cohesion). Saved in `settings.json`.

## 4. Privacy, permissions, errors
- Everything stays local. The only network call is `gh repo list` (Malek's own auth) for GitHub-only repos; it can be switched off.
- Stored content is minimal: paths, names, timestamps, counts, first lines/titles. From Claude sessions only file paths and times are kept (never message text); from claude-mem only project, title, file lists and time.
- Hard exclusions (never read): `~/.ssh`, `~/.gnupg`, Keychains, browser profiles, `~/Library` (except `~/.claude/projects` and `~/.claude-mem`), files named `.env*`, `*.pem`, `*.key`, `id_*`, `*credentials*`, `*secret*`. Plus user exclusions in `settings.json`.
- Sources toggle individually in `settings.json`; the four personal sources (Mail, Messages, Calendar, Photos) are not implemented in v1 and need an explicit opt-in plus the macOS permission prompt when added.
- Each adapter runs isolated: failures are logged with counts and shown in the menubar status ("Claude sessions: 961 ok, 2 unreadable"); the graph still renders from the rest. claude-mem is opened read-only and tolerates missing columns. The shell restarts a crashed indexer with backoff (1, 5, 30 s). Store writes are transactional; the viewer shows an empty-state message when there is no data.

## 5. Testing
- **Indexer (pytest):** fixtures with a tiny git repo with history, sample session JSONL, a small SQLite with the claude-mem schema, a documents tree; tests for each adapter, decay, percentile ranking, weight renormalization, noise factor, parent aggregation, area clustering and the exclusion list (a `.env` and an `id_rsa` in the fixture tree must never be read).
- **Viewer (vitest):** radius and direction functions, LOD selection, settings persistence; Playwright smoke test on the Vite page with a fixture graph (canvas renders, hover highlight, side panel opens, replay advances).
- **Shell (cargo test):** watcher debounce, command handlers; manual: app launches, menubar status, reindex.
- **Performance gate:** 20,000 nodes at >= 30 fps in the Tauri window on this Mac; otherwise switch the shell to Electron.

## Milestones
1. Indexer: store, four adapters, scoring, areas, export (CLI + tests).
2. Viewer: layout, firing, interactions, settings (with fixture and real export).
3. Shell: Tauri window, menubar, sidecar, watcher, commands; performance gate.
4. Area review in the app (rename, merge, move) writing `areas.json`.
