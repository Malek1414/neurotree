# Neurotree

A 3D attention tree of everything on this Mac. You sit at the core; your life areas are branches; projects, files and functions grow outward. What you and Claude work on most sits closest to the core and fires hardest: every edit, read and commit sends a signal along its branch into the core.

Stage 1 of 5: indexer + desktop app. Next: connect to Claude, per-hackathon-project mini graphs, the "neurons firing" embed for project sites. Design: [docs/superpowers/specs/2026-10-31-neurotree-design.md](docs/superpowers/specs/2026-10-31-neurotree-design.md).

## What it reads (read-only, nothing leaves the Mac)
- Git repos under `~/Desktop` and `~/Documents` (files, imports, functions, a year of commits; second clones count for the original)
- Your GitHub repos via `gh` (only for repos without a local clone)
- Documents and Desktop files (names, edit times; `.md`/`.txt` are scanned for the repos they mention)
- Claude Code sessions in `~/.claude/projects` (only which files were read or edited, and when, never message text)
- claude-mem memory (`~/.claude-mem/claude-mem.db`, opened read-only: titles, file lists, times)

Never read: `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.kube`, `~/.docker`, `~/Library`, and any file or folder named like a secret (`.env*`, `*.pem`, `*.key`, `id_rsa*`, `*credential*`, `*secret*`, ...). Mail, Messages, Calendar and Photos are not read; they would need your explicit opt-in later.

Data lives in `~/Library/Application Support/Neurotree/` (`graph.db`, `graph.json`, `status.json`, `areas.json`, `settings.json`).

## How importance works
Attention 45 % (Claude touches, half-life 30 days), history 25 % (commits and edits, half-life 90 days), structure 20 % (how much the rest of the code depends on a module), runtime 10 % (share of a recorded run). Each signal is a percentile within its level; lockfiles, generated and vendored files are dimmed. Every node shows its breakdown and evidence.

## Run it
```bash
uv venv .venv --python 3.13 && uv pip install --python .venv/bin/python networkx pytest
.venv/bin/python -m neurotree all          # first index, ~1-2 min

cd viewer && npm install && npm run dev    # browser: http://127.0.0.1:5173
cd app && npm install && npx tauri dev     # desktop app (uses the dev server above)
cd app && npm run build                    # -> app/src-tauri/target/release/bundle/macos/Neurotree.app
```
In the app: drag to orbit, scroll to zoom, arrow keys pan (Shift faster), `/` search, `Esc` reset, `F` frame rate. Live fires on real file changes and on Claude's edits as they happen; Replay plays the last 120 days. Areas are a proposal from your attention until you edit them (Settings > Areas, or a project's panel); after that every reindex keeps your placement.

## Tests
```bash
.venv/bin/python -m pytest -q              # indexer (29)
cd viewer && npm test                      # viewer (33)
cd app/src-tauri && cargo test             # shell (39)
```
