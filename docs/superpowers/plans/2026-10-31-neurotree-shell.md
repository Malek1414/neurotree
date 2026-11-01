# Neurotree Shell Implementation Plan (plan 3 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Tauri 2 desktop app that shows the Neurotree viewer in a window, lives in the macOS menubar, supervises the Python indexer, and fires live file changes (from the file system and from Claude Code sessions) into the viewer.

**Architecture:** One Rust crate in `app/src-tauri`, split by responsibility: `paths` (where things live, what is private), `events` (payloads and the 2 s batcher), `claude_tail` (new JSONL bytes to file paths), `watcher` (FSEvents routing), `indexer` (runner, retries, reindex policy), `commands` (what the viewer invokes), `tray` (menubar). `lib.rs` only wires threads and Tauri together. Every decision lives in a pure function or struct with `cargo test` coverage; the Tauri glue stays thin.

**Tech Stack:** Rust 1.97, Tauri 2 (`tray-icon`, `image-png`), `notify` 8 (FSEvents), `serde`/`serde_json`, `libc` (`localtime_r` for the menubar clock), `tempfile` (tests); `@tauri-apps/cli` 2 from npm (no `cargo install`); Pillow for the icon art.

## Global Constraints
- **Viewer contract (exact):** `tauri.conf.json` has `build.frontendDist = "../../viewer/dist"`, `build.devUrl = "http://localhost:5173"`, window label `main`, title "Neurotree", 1440x900, background `#05060a`.
- **Commands** (JS: `invoke` from `@tauri-apps/api/core`): `load_graph() -> String` (raw `<data dir>/graph.json`, error string if missing); `status() -> Option<serde_json::Value>` (parsed `status.json` or null); `open_path(path: String)` = `open <path>`; `reveal_path(path: String)` = `open -R <path>` (reject paths that do not exist, run nothing else); `reindex() -> bool` (false if a run is in progress).
- **Events** (JS: `listen` from `@tauri-apps/api/event`): `"fs-change"` `{ "paths": [String], "source": "fs" | "claude" }`, coalesced, at most one emit per 2 s window; `"index-status"` `{ "running": bool, "finished_at": f64 | null, "error": String | null }` when a run starts and ends.
- **Data dir:** env `NEUROTREE_HOME`, else `~/Library/Application Support/Neurotree`.
- **Indexer:** python = `NEUROTREE_PYTHON`, else `<repo>/.venv/bin/python`; repo = `NEUROTREE_REPO`, else `CARGO_MANIFEST_DIR/../..` at build time; cwd = repo; command `python -m neurotree all`; on failure retry after 1 s, 5 s, 30 s; full index at startup when `graph.json` is missing or older than 6 h; after watcher changes, a full reindex at most every 10 minutes.
- **Watcher:** recursive on `~/Desktop`, `~/Documents`, `~/.claude/projects`. Ignore components `.git node_modules .venv venv __pycache__ dist build .next .cache target`, everything under `~/Library ~/.ssh ~/.gnupg ~/.aws`, names `.env* *.pem *.key id_rsa* id_ed25519* *credential* *secret*`. Never read file contents, except the Claude tail.
- **Claude tail:** for a growing `*.jsonl` under `~/.claude/projects` read only appended bytes (per-file offset; a file seen for the first time starts at its end); from `tool_use` blocks of Edit, Write, MultiEdit, NotebookEdit, Read, Glob, Grep keep `input.file_path` / `input.notebook_path` / `input.path`; emit as `fs-change` with source `"claude"`. Never keep message text.
- **Menubar:** disabled status line ("Indexed 21:56 · 3,412 nodes" / "Indexing…" / "Index failed"), "Reindex now", "Show Neurotree", "Quit". Closing the window hides it.
- **Ownership:** this plan owns `app/` only. Never create or edit `viewer/` or `neurotree/` in git. A throwaway `viewer/dist/index.html` is allowed for a local release build (`dist/` is gitignored).
- **Commits:** every commit message ends with
  `Co-Authored-By: WOZCODE <contact@withwoz.com>` and `Claude-Session: https://claude.ai/code/session_01T5GAsiSvUNCk2US4QqTgrk`.

## Decisions made while planning
- **Retries:** one run plus up to three retries, waiting 1 s, 5 s, 30 s before them (4 attempts at most). The error of the last attempt is reported.
- **`index-status` values:** start `{running: true, finished_at: null, error: null}`; success `{running: false, finished_at: <epoch s>, error: null}`; failure `{running: false, finished_at: null, error: "<stderr tail>"}`. The viewer reloads the graph when `running` is false and `error` is null.
- **`fs-change` throttle** is shared by both sources: one emit per 2 s overall, the older batch first. A batch settles 300 ms after its first path so one save (several FSEvents) is one emit. Duplicates are dropped; a batch holds at most 1,000 paths (the next full reindex catches the rest).
- **Privacy list** mirrors `neurotree/privacy.py`, which is a superset of the watcher list above (adds `~/.kube`, `~/.docker`, `.p12 .pfx .kdbx .keychain* id_ecdsa* id_dsa* .netrc .npmrc .pypirc`, and skip dirs `.tox .mypy_cache .pytest_cache Pods DerivedData .gradle .idea .turbo`), so the watcher never fires a path the indexer refuses to read. Secret patterns apply to the file name, as in the indexer.
- **Claude tail priming:** at startup every existing `*.jsonl` is recorded at its current size, so the first append after launch is read; files that appear later start at their end (as specified). Paths that are private by the list above are dropped before emitting.
- **`open_path`** also refuses launchable types (`.app .command .tool .terminal .workflow .action .pkg .mpkg`), because `open` would run them; `reveal_path` accepts them. Both require an absolute, existing path and only ever run `/usr/bin/open`.
- **Indexer PATH:** an app started from Finder gets a bare `PATH`; the child gets `/opt/homebrew/bin` and `/usr/local/bin` prepended so `git` and `gh` resolve.
- **Spec naming:** the spec's `reveal_in_finder` is `reveal_path` (viewer contract wins).
- **Dev vs release assets:** a plain `cargo build`/`cargo test` (no `custom-protocol` feature) loads `devUrl` and does not need `viewer/dist`; `tauri build` embeds `../../viewer/dist`, so the viewer must be built first.

## File Structure
```
app/package.json                     @tauri-apps/cli; scripts dev / build / icon
app/icon-source.png                  1024 px original icon art (generated)
app/scripts/make_icon.py             draws icon-source.png and the menubar template icon
app/src-tauri/Cargo.toml             crate neurotree, lib neurotree_lib
app/src-tauri/build.rs               tauri_build::build()
app/src-tauri/tauri.conf.json        window + build contract with the viewer
app/src-tauri/capabilities/default.json   core:default for the main window
app/src-tauri/icons/                 bundle icons from `tauri icon` + tray.png
app/src-tauri/.gitignore             /target/, /gen/schemas
app/src-tauri/src/main.rs            calls neurotree_lib::run()
app/src-tauri/src/lib.rs             wiring: state, tray, live pipeline, scheduler, window events
app/src-tauri/src/paths.rs           data dir, watch roots, is_ignored()
app/src-tauri/src/events.rs          FsChange / IndexStatus payloads, Batcher, spawn_batcher()
app/src-tauri/src/claude_tail.rs     extract_paths(), Tailer, spawn()
app/src-tauri/src/watcher.rs         route(), start()
app/src-tauri/src/indexer.rs         resolve_command(), run_with_retries(), ReindexPolicy, Indexer
app/src-tauri/src/commands.rs        AppState and the five commands
app/src-tauri/src/tray.rs            status_line(), create(), refresh()
```

Data flow:
```
FSEvents ──watcher::route──┬─ Fs ─────────────────────────────┐
                           └─ ClaudeLog ─ claude_tail (paths) ─┤─> Batcher (2 s) ─> emit "fs-change"
                           └─ both: Indexer::mark_dirty ─> scheduler (30 s tick, 10 min policy) ─> Indexer::start
Indexer::start ─> python -m neurotree all (retries 1/5/30 s) ─> emit "index-status" + tray::refresh
```

---

### Task 1: Tauri skeleton, window contract, icons

**Files:**
- Create: `app/package.json`, `app/scripts/make_icon.py`, `app/src-tauri/{Cargo.toml,build.rs,tauri.conf.json,.gitignore}`, `app/src-tauri/capabilities/default.json`, `app/src-tauri/src/{main.rs,lib.rs}`
- Generate: `app/icon-source.png`, `app/src-tauri/icons/*`
- Test: `config_contract` module in `app/src-tauri/src/lib.rs`

**Interfaces:**
- Produces: crate `neurotree` with lib `neurotree_lib` and `pub fn run()`; window label `"main"`; `icons/tray.png` (44x44 template icon) used by Task 8.

- [ ] **Step 1: npm package with the Tauri CLI**

**File:** `app/package.json`
```json
{
  "name": "neurotree-app",
  "private": true,
  "version": "0.1.0",
  "description": "Neurotree desktop shell (Tauri 2)",
  "scripts": {
    "tauri": "tauri",
    "dev": "tauri dev",
    "build": "npm --prefix ../viewer run build && tauri build --bundles app",
    "icon": "python3 scripts/make_icon.py && tauri icon icon-source.png"
  },
  "devDependencies": {
    "@tauri-apps/cli": "^2"
  }
}
```

Run: `cd app && npm install && npx tauri --version`
Expected: `tauri-cli 2.x`

- [ ] **Step 2: Crate files**

**File:** `app/src-tauri/Cargo.toml`
```toml
[package]
name = "neurotree"
version = "0.1.0"
description = "Neurotree desktop shell: window, menubar, indexer supervisor, live file watcher"
authors = ["Malek Hassan"]
edition = "2021"
rust-version = "1.80"

[lib]
name = "neurotree_lib"
crate-type = ["staticlib", "cdylib", "rlib"]

[build-dependencies]
tauri-build = { version = "2", features = [] }

[dependencies]
tauri = { version = "2", features = ["tray-icon", "image-png"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
notify = "8"
libc = "0.2"

[dev-dependencies]
tempfile = "3"
```

**File:** `app/src-tauri/build.rs`
```rust
fn main() {
    tauri_build::build()
}
```

**File:** `app/src-tauri/src/main.rs`
```rust
// Prevents an extra console window on Windows in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    neurotree_lib::run()
}
```

**File:** `app/src-tauri/capabilities/default.json`
```json
{
  "$schema": "../gen/schemas/desktop-schema.json",
  "identifier": "default",
  "description": "Main window: core events (listen) plus the app's own commands (load_graph, status, open_path, reveal_path, reindex)",
  "windows": ["main"],
  "permissions": ["core:default"]
}
```

**File:** `app/src-tauri/.gitignore`
```gitignore
/target/
/gen/schemas
```

- [ ] **Step 3: Write the failing contract test** (lib.rs with a minimal `run()`: close hides the window, dock click shows it)

**File:** `app/src-tauri/src/lib.rs`
```rust
//! Neurotree desktop shell: one window with the viewer, closing it only hides it.

use tauri::{Manager, WindowEvent};

pub fn run() {
    tauri::Builder::default()
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building Neurotree")
        .run(|app, event| {
            #[cfg(target_os = "macos")]
            if let tauri::RunEvent::Reopen { .. } = event {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.show();
                    let _ = w.set_focus();
                }
            }
        });
}

#[cfg(test)]
mod config_contract {
    use serde_json::Value;

    fn conf() -> Value {
        serde_json::from_str(include_str!("../tauri.conf.json")).unwrap()
    }

    #[test]
    fn build_points_at_the_viewer() {
        let c = conf();
        assert_eq!(c["build"]["frontendDist"], "../../viewer/dist");
        assert_eq!(c["build"]["devUrl"], "http://localhost:5173");
    }

    #[test]
    fn main_window_matches_the_viewer_contract() {
        let c = conf();
        let w = &c["app"]["windows"][0];
        assert_eq!(w["label"], "main");
        assert_eq!(w["title"], "Neurotree");
        assert_eq!(w["width"], 1440);
        assert_eq!(w["height"], 900);
        assert_eq!(w["backgroundColor"], "#05060a");
    }
}
```

Run: `cd app/src-tauri && cargo test`
Expected: FAIL: the `tauri-build` build script (and `include_str!` in the test) cannot find `tauri.conf.json`, which does not exist yet.

- [ ] **Step 4: The config**

**File:** `app/src-tauri/tauri.conf.json`
```json
{
  "$schema": "../node_modules/@tauri-apps/cli/config.schema.json",
  "productName": "Neurotree",
  "version": "0.1.0",
  "identifier": "com.malekhassan.neurotree",
  "build": {
    "frontendDist": "../../viewer/dist",
    "devUrl": "http://localhost:5173"
  },
  "app": {
    "windows": [
      {
        "label": "main",
        "title": "Neurotree",
        "width": 1440,
        "height": 900,
        "minWidth": 800,
        "minHeight": 500,
        "center": true,
        "backgroundColor": "#05060a",
        "theme": "Dark"
      }
    ],
    "security": {
      "csp": null
    }
  },
  "bundle": {
    "active": true,
    "targets": ["app"],
    "category": "Productivity",
    "shortDescription": "A 3D attention tree of everything on this Mac",
    "icon": [
      "icons/32x32.png",
      "icons/128x128.png",
      "icons/128x128@2x.png",
      "icons/icon.icns",
      "icons/icon.png"
    ],
    "macOS": {
      "minimumSystemVersion": "11.0"
    }
  }
}
```

- [ ] **Step 5: Icons**

**File:** `app/scripts/make_icon.py`
```python
"""Draw Neurotree's original icons with Pillow (python3 -m pip install pillow):
  icon-source.png              1024x1024 app icon: a glowing core with six faint branches on near-black,
                               fed to `npx tauri icon icon-source.png` for the bundle sizes.
  src-tauri/icons/tray.png     44x44 menubar template icon (black on transparent; macOS tints it).
Run from app/: python3 scripts/make_icon.py"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

APP = Path(__file__).resolve().parent.parent
BG = (5, 6, 10, 255)  # #05060a, the window background
CORE = (120, 220, 255)
BRANCH = (150, 120, 255)


def app_icon(size: int = 1024) -> Image.Image:
    c = size / 2
    m = int(size * 0.1)  # macOS icon grid: content inset ~10 %
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([m, m, size - m, size - m], radius=int(size * 0.18), fill=255)
    canvas.paste(Image.new("RGBA", (size, size), BG), (0, 0), mask)

    halo = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    h = ImageDraw.Draw(halo)
    for r, alpha in ((0.26, 50), (0.18, 90), (0.12, 160)):
        rr = size * r
        h.ellipse([c - rr, c - rr, c + rr, c + rr], fill=CORE + (alpha,))
    halo = halo.filter(ImageFilter.GaussianBlur(size * 0.04))

    tree = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    t = ImageDraw.Draw(tree)
    for i in range(6):
        a = math.radians(90 + i * 60)
        tip = (c + math.cos(a) * size * 0.27, c - math.sin(a) * size * 0.27)
        t.line([(c, c), tip], fill=BRANCH + (170,), width=int(size * 0.014))
        r = size * 0.03
        t.ellipse([tip[0] - r, tip[1] - r, tip[0] + r, tip[1] + r], fill=BRANCH + (255,))
    tree = Image.alpha_composite(tree.filter(ImageFilter.GaussianBlur(size * 0.012)), tree)
    rr = size * 0.07
    ImageDraw.Draw(tree).ellipse([c - rr, c - rr, c + rr, c + rr], fill=(235, 250, 255, 255))

    art = Image.alpha_composite(halo, tree)
    art.putalpha(ImageChops.multiply(art.getchannel("A"), mask))
    return Image.alpha_composite(canvas, art)


def tray_icon(size: int = 44) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = size / 2
    for i in range(6):
        a = math.radians(90 + i * 60)
        tip = (c + math.cos(a) * size * 0.36, c - math.sin(a) * size * 0.36)
        d.line([(c, c), tip], fill=(0, 0, 0, 255), width=2)
        d.ellipse([tip[0] - 3, tip[1] - 3, tip[0] + 3, tip[1] + 3], fill=(0, 0, 0, 255))
    r = size * 0.16
    d.ellipse([c - r, c - r, c + r, c + r], fill=(0, 0, 0, 255))
    return img


if __name__ == "__main__":
    app_icon().save(APP / "icon-source.png")
    (APP / "src-tauri" / "icons").mkdir(parents=True, exist_ok=True)
    tray_icon().save(APP / "src-tauri" / "icons" / "tray.png")
    print("wrote icon-source.png and src-tauri/icons/tray.png")
```

Run: `cd app && python3 scripts/make_icon.py && npx tauri icon icon-source.png && rm -rf src-tauri/icons/{android,ios} src-tauri/icons/Square*Logo.png src-tauri/icons/StoreLogo.png src-tauri/icons/64x64.png`
Expected: `src-tauri/icons/` holds `32x32.png 128x128.png 128x128@2x.png icon.icns icon.ico icon.png tray.png`.

- [ ] **Step 6: Run the tests**

Run: `cd app/src-tauri && cargo test`
Expected: `test result: ok. 2 passed` (first build of Tauri takes a few minutes). No `viewer/dist` is needed: without the `custom-protocol` feature the binary loads `devUrl`.

- [ ] **Step 7: Commit**

```bash
git add app
git commit -m "Tauri 2 shell skeleton: window contract, close-to-hide, icons"
```

---

### Task 2: Paths and privacy exclusions

**Files:**
- Create: `app/src-tauri/src/paths.rs`
- Modify: `app/src-tauri/src/lib.rs` (add `mod paths;` above the `use` lines)

**Interfaces:**
- Produces: `home_dir() -> PathBuf`; `data_dir_from(env_home: Option<String>, home: &Path) -> PathBuf`; `data_dir() -> PathBuf`; `claude_projects(home: &Path) -> PathBuf`; `watch_roots(home: &Path) -> Vec<PathBuf>`; `is_secret_name(name: &str) -> bool`; `is_ignored(path: &Path, home: &Path) -> bool`; consts `SKIP_DIRS`, `PRIVATE_DIRS`.

- [ ] **Step 1: Write the failing tests** (the file starts as only its test module)

**File:** `app/src-tauri/src/paths.rs`
```rust
#[cfg(test)]
mod tests {
    use super::*;

    const HOME: &str = "/Users/m";

    fn ignored(p: &str) -> bool {
        is_ignored(Path::new(p), Path::new(HOME))
    }

    #[test]
    fn data_dir_prefers_env() {
        assert_eq!(data_dir_from(Some("/tmp/nt".into()), Path::new(HOME)), PathBuf::from("/tmp/nt"));
        assert_eq!(data_dir_from(Some("".into()), Path::new(HOME)),
                   PathBuf::from("/Users/m/Library/Application Support/Neurotree"));
        assert_eq!(data_dir_from(None, Path::new(HOME)),
                   PathBuf::from("/Users/m/Library/Application Support/Neurotree"));
    }

    #[test]
    fn watch_roots_are_desktop_documents_and_claude_projects() {
        assert_eq!(watch_roots(Path::new(HOME)), vec![
            PathBuf::from("/Users/m/Desktop"),
            PathBuf::from("/Users/m/Documents"),
            PathBuf::from("/Users/m/.claude/projects"),
        ]);
        assert_eq!(claude_projects(Path::new(HOME)), PathBuf::from("/Users/m/.claude/projects"));
    }

    #[test]
    fn skip_dirs_anywhere_in_the_path() {
        for p in ["/Users/m/Desktop/app/.git/index", "/Users/m/Desktop/app/node_modules/x/i.js",
                  "/Users/m/Desktop/app/.venv/lib/site.py", "/Users/m/Desktop/app/venv/bin/python",
                  "/Users/m/Desktop/app/pkg/__pycache__/a.pyc", "/Users/m/Desktop/app/dist/index.html",
                  "/Users/m/Desktop/app/build/out.o", "/Users/m/Desktop/web/.next/cache/x",
                  "/Users/m/Desktop/app/.cache/y", "/Users/m/Desktop/app/target/debug/app"] {
            assert!(ignored(p), "{p}");
        }
    }

    #[test]
    fn private_folders_under_home() {
        for p in ["/Users/m/Library/Mail/x.emlx", "/Users/m/.ssh/config", "/Users/m/.gnupg/pubring.kbx",
                  "/Users/m/.aws/config", "/Users/m/Library"] {
            assert!(ignored(p), "{p}");
        }
        assert!(!ignored("/Users/m/Desktop/Library/notes.md"), "only ~/Library, not any Library");
    }

    #[test]
    fn secret_file_names() {
        for p in ["/Users/m/Desktop/app/.env", "/Users/m/Desktop/app/.env.local", "/Users/m/Desktop/app/.envrc",
                  "/Users/m/Desktop/server.pem", "/Users/m/Desktop/talk.key", "/Users/m/Desktop/id_rsa",
                  "/Users/m/Desktop/id_rsa.pub", "/Users/m/Desktop/id_ed25519", "/Users/m/Desktop/aws-Credentials.json",
                  "/Users/m/Desktop/my_secret_plan.md", "/Users/m/Desktop/SECRETS.txt"] {
            assert!(ignored(p), "{p}");
        }
    }

    #[test]
    fn normal_work_files_pass() {
        for p in ["/Users/m/Desktop/app/main.py", "/Users/m/Documents/notes/plan.md",
                  "/Users/m/Desktop/app/id_mapping.py", "/Users/m/Desktop/app/keynote.pdf",
                  "/Users/m/Desktop/builder/app.ts", "/Users/m/.claude/projects/-Users-m-Desktop-app/s.jsonl"] {
            assert!(!ignored(p), "{p}");
        }
    }
}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd app/src-tauri && cargo test paths`
Expected: FAIL: `cannot find function 'is_ignored' in this scope` (and the other names).

- [ ] **Step 3: Implement**

Put this **above** the test module in `app/src-tauri/src/paths.rs`:
```rust
//! Where things live and which paths the shell must never touch. Mirrors `neurotree/privacy.py`, so the
//! watcher never fires a path the indexer would refuse to read.

use std::path::{Component, Path, PathBuf};

/// Folder names that are never interesting, anywhere in a path.
pub const SKIP_DIRS: &[&str] = &[
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", ".cache", "target",
    ".tox", ".mypy_cache", ".pytest_cache", "Pods", "DerivedData", ".gradle", ".idea", ".turbo",
];
/// Folders directly under the home folder that are private as a whole.
pub const PRIVATE_DIRS: &[&str] = &["Library", ".ssh", ".gnupg", ".aws", ".kube", ".docker"];

const SECRET_PREFIXES: &[&str] = &[".env", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa"];
const SECRET_SUFFIXES: &[&str] = &[".pem", ".key", ".p12", ".pfx", ".kdbx", ".keychain", ".keychain-db"];
const SECRET_PARTS: &[&str] = &["credential", "secret"];
const SECRET_NAMES: &[&str] = &[".netrc", ".npmrc", ".pypirc"];

pub fn home_dir() -> PathBuf {
    std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/"))
}

/// `NEUROTREE_HOME` if set and non-empty, else `~/Library/Application Support/Neurotree`.
pub fn data_dir_from(env_home: Option<String>, home: &Path) -> PathBuf {
    match env_home {
        Some(dir) if !dir.is_empty() => PathBuf::from(dir),
        _ => home.join("Library").join("Application Support").join("Neurotree"),
    }
}

pub fn data_dir() -> PathBuf {
    data_dir_from(std::env::var("NEUROTREE_HOME").ok(), &home_dir())
}

pub fn claude_projects(home: &Path) -> PathBuf {
    home.join(".claude").join("projects")
}

/// Folders the live watcher follows (recursively).
pub fn watch_roots(home: &Path) -> Vec<PathBuf> {
    vec![home.join("Desktop"), home.join("Documents"), claude_projects(home)]
}

/// File names that may hold keys or credentials (case-insensitive).
pub fn is_secret_name(name: &str) -> bool {
    let n = name.to_ascii_lowercase();
    SECRET_NAMES.contains(&n.as_str())
        || SECRET_PREFIXES.iter().any(|p| n.starts_with(p))
        || SECRET_SUFFIXES.iter().any(|s| n.ends_with(s))
        || SECRET_PARTS.iter().any(|s| n.contains(s))
}

/// True for paths the shell must ignore: private home folders, skip dirs anywhere, secret file names.
pub fn is_ignored(path: &Path, home: &Path) -> bool {
    if PRIVATE_DIRS.iter().any(|d| path.starts_with(home.join(d))) {
        return true;
    }
    let skip = path.components().any(|c| match c {
        Component::Normal(part) => SKIP_DIRS.contains(&part.to_string_lossy().as_ref()),
        _ => false,
    });
    skip || path.file_name().is_some_and(|n| is_secret_name(&n.to_string_lossy()))
}
```

- [ ] **Step 4: Run the tests**

Run: `cd app/src-tauri && cargo test paths`
Expected: `test result: ok. 6 passed`

- [ ] **Step 5: Commit**

```bash
git add app/src-tauri/src/paths.rs app/src-tauri/src/lib.rs
git commit -m "Shell paths: data dir, watch roots, privacy exclusions"
```

---

### Task 3: Event payloads and the 2 s batcher

**Files:**
- Create: `app/src-tauri/src/events.rs`
- Modify: `app/src-tauri/src/lib.rs` (add `mod events;`)

**Interfaces:**
- Produces: consts `FS_CHANGE = "fs-change"`, `INDEX_STATUS = "index-status"`, `WINDOW = 2 s`, `SETTLE = 300 ms`, `MAX_BATCH = 1000`; `enum Source { Fs, Claude }` (serializes `"fs"`/`"claude"`); `struct FsChange { paths: Vec<String>, source: Source }`; `struct IndexStatus { running: bool, finished_at: Option<f64>, error: Option<String> }`; `struct Change { source: Source, path: String }`; `Batcher::{new(window, settle), push(source, path, now), next_deadline() -> Option<Instant>, take_ready(now) -> Option<FsChange>}`; `spawn_batcher(window, settle, emit: impl Fn(FsChange) + Send + 'static) -> Sender<Change>`.

- [ ] **Step 1: Write the failing tests**

**File:** `app/src-tauri/src/events.rs`
```rust
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::sync::mpsc;
    use std::time::{Duration, Instant};

    const W: Duration = Duration::from_secs(2);
    const S: Duration = Duration::from_millis(300);

    fn ms(n: u64) -> Duration {
        Duration::from_millis(n)
    }

    #[test]
    fn payloads_match_the_viewer_contract() {
        let fs = FsChange { paths: vec!["/a".into()], source: Source::Fs };
        assert_eq!(serde_json::to_value(&fs).unwrap(), json!({"paths": ["/a"], "source": "fs"}));
        let cl = FsChange { paths: vec![], source: Source::Claude };
        assert_eq!(serde_json::to_value(&cl).unwrap()["source"], "claude");
        let st = IndexStatus { running: false, finished_at: Some(1.5), error: None };
        assert_eq!(serde_json::to_value(&st).unwrap(), json!({"running": false, "finished_at": 1.5, "error": null}));
    }

    #[test]
    fn a_burst_settles_into_one_deduplicated_batch() {
        let t0 = Instant::now();
        let mut b = Batcher::new(W, S);
        b.push(Source::Fs, "/a".into(), t0);
        b.push(Source::Fs, "/b".into(), t0 + ms(50));
        b.push(Source::Fs, "/a".into(), t0 + ms(100));
        assert_eq!(b.take_ready(t0 + ms(200)), None, "still settling");
        assert_eq!(b.next_deadline(), Some(t0 + S));
        let batch = b.take_ready(t0 + S).unwrap();
        assert_eq!(batch, FsChange { paths: vec!["/a".into(), "/b".into()], source: Source::Fs });
        assert_eq!(b.take_ready(t0 + ms(5000)), None, "nothing left");
        assert_eq!(b.next_deadline(), None);
    }

    #[test]
    fn at_most_one_emit_per_window_across_sources() {
        let t0 = Instant::now();
        let mut b = Batcher::new(W, S);
        b.push(Source::Fs, "/a".into(), t0);
        b.push(Source::Claude, "/c".into(), t0 + ms(10));
        let first = b.take_ready(t0 + S).unwrap();
        assert_eq!(first.source, Source::Fs, "older batch goes first");
        assert_eq!(b.take_ready(t0 + ms(1500)), None, "window not over");
        assert_eq!(b.next_deadline(), Some(t0 + S + W));
        let second = b.take_ready(t0 + S + W).unwrap();
        assert_eq!(second, FsChange { paths: vec!["/c".into()], source: Source::Claude });
    }

    #[test]
    fn batches_are_capped() {
        let t0 = Instant::now();
        let mut b = Batcher::new(W, S);
        for i in 0..(MAX_BATCH + 50) {
            b.push(Source::Fs, format!("/f{i}"), t0);
        }
        assert_eq!(b.take_ready(t0 + S).unwrap().paths.len(), MAX_BATCH);
    }

    #[test]
    fn spawned_batcher_emits_through_the_callback() {
        let (out_tx, out_rx) = mpsc::channel();
        let tx = spawn_batcher(ms(200), ms(20), move |c| out_tx.send(c).unwrap());
        tx.send(Change { source: Source::Claude, path: "/x".into() }).unwrap();
        tx.send(Change { source: Source::Claude, path: "/y".into() }).unwrap();
        let got = out_rx.recv_timeout(Duration::from_secs(2)).unwrap();
        assert_eq!(got, FsChange { paths: vec!["/x".into(), "/y".into()], source: Source::Claude });
    }
}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd app/src-tauri && cargo test events`
Expected: FAIL: `cannot find struct, variant or union type 'FsChange'`.

- [ ] **Step 3: Implement**

Put this **above** the test module in `app/src-tauri/src/events.rs`:
```rust
//! Event payloads sent to the webview and the batcher that coalesces file changes into at most one
//! `fs-change` emit per window (2 s in the app).

use serde::Serialize;
use std::collections::HashSet;
use std::sync::mpsc::{self, RecvTimeoutError, Sender};
use std::thread;
use std::time::{Duration, Instant};

pub const FS_CHANGE: &str = "fs-change";
pub const INDEX_STATUS: &str = "index-status";
/// Emits are throttled to one per window across both sources.
pub const WINDOW: Duration = Duration::from_secs(2);
/// A batch waits this long after its first path so one save (several fs events) lands in one emit.
pub const SETTLE: Duration = Duration::from_millis(300);
/// Paths beyond this per batch are dropped; the next full reindex picks them up.
pub const MAX_BATCH: usize = 1000;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Source {
    Fs,
    Claude,
}

/// Payload of `fs-change`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct FsChange {
    pub paths: Vec<String>,
    pub source: Source,
}

/// Payload of `index-status`; `finished_at` (epoch seconds) is set only after a successful run.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct IndexStatus {
    pub running: bool,
    pub finished_at: Option<f64>,
    pub error: Option<String>,
}

/// One changed path on its way to the batcher.
#[derive(Debug, Clone)]
pub struct Change {
    pub source: Source,
    pub path: String,
}

#[derive(Default)]
struct Pending {
    paths: Vec<String>,
    seen: HashSet<String>,
    opened: Option<Instant>,
}

pub struct Batcher {
    window: Duration,
    settle: Duration,
    fs: Pending,
    claude: Pending,
    last_emit: Option<Instant>,
}

impl Batcher {
    pub fn new(window: Duration, settle: Duration) -> Self {
        Batcher { window, settle, fs: Pending::default(), claude: Pending::default(), last_emit: None }
    }

    fn pending_mut(&mut self, source: Source) -> &mut Pending {
        match source {
            Source::Fs => &mut self.fs,
            Source::Claude => &mut self.claude,
        }
    }

    pub fn push(&mut self, source: Source, path: String, now: Instant) {
        let p = self.pending_mut(source);
        if p.opened.is_none() {
            p.opened = Some(now);
        }
        if p.paths.len() < MAX_BATCH && p.seen.insert(path.clone()) {
            p.paths.push(path);
        }
    }

    fn ready_at(&self, p: &Pending) -> Option<Instant> {
        let settled = p.opened? + self.settle;
        Some(match self.last_emit {
            Some(last) => settled.max(last + self.window),
            None => settled,
        })
    }

    /// When the next batch becomes ready, if anything is pending.
    pub fn next_deadline(&self) -> Option<Instant> {
        [self.ready_at(&self.fs), self.ready_at(&self.claude)].into_iter().flatten().min()
    }

    /// The oldest ready batch, if any; marks the window as used.
    pub fn take_ready(&mut self, now: Instant) -> Option<FsChange> {
        let ready = |p: &Pending| self.ready_at(p).is_some_and(|t| now >= t);
        let source = match (ready(&self.fs), ready(&self.claude)) {
            (true, true) if self.claude.opened < self.fs.opened => Source::Claude,
            (true, _) => Source::Fs,
            (false, true) => Source::Claude,
            (false, false) => return None,
        };
        let taken = std::mem::take(self.pending_mut(source));
        self.last_emit = Some(now);
        Some(FsChange { paths: taken.paths, source })
    }
}

/// Runs a `Batcher` (2 s window, 300 ms settle) on its own thread and calls `emit` with every batch.
/// Returns the sender that the watcher and the Claude tail push into; the thread ends when all senders drop.
pub fn spawn_batcher<F>(window: Duration, settle: Duration, emit: F) -> Sender<Change>
where
    F: Fn(FsChange) + Send + 'static,
{
    let (tx, rx) = mpsc::channel::<Change>();
    thread::spawn(move || {
        let mut b = Batcher::new(window, settle);
        loop {
            while let Some(batch) = b.take_ready(Instant::now()) {
                emit(batch);
            }
            let msg = match b.next_deadline() {
                Some(t) => rx.recv_timeout(t.saturating_duration_since(Instant::now())),
                None => rx.recv().map_err(|_| RecvTimeoutError::Disconnected),
            };
            match msg {
                Ok(c) => b.push(c.source, c.path, Instant::now()),
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => break,
            }
        }
    });
    tx
}
```

- [ ] **Step 4: Run the tests**

Run: `cd app/src-tauri && cargo test events`
Expected: `test result: ok. 5 passed`

- [ ] **Step 5: Commit**

```bash
git add app/src-tauri/src/events.rs app/src-tauri/src/lib.rs
git commit -m "fs-change/index-status payloads and the 2 s change batcher"
```

---

### Task 4: Claude Code live tail

**Files:**
- Create: `app/src-tauri/src/claude_tail.rs`
- Modify: `app/src-tauri/src/lib.rs` (add `mod claude_tail;`)

**Interfaces:**
- Consumes: `paths::is_ignored(path, home)` (Task 2).
- Produces: `TOOLS`; `extract_paths(line: &str) -> Vec<String>`; `Tailer::{default(), prime(root: &Path), read_new(path: &Path) -> Vec<String>}`; `spawn(root: PathBuf, home: PathBuf, on_paths: impl Fn(Vec<String>) + Send + 'static) -> Sender<PathBuf>` (primes before returning).

- [ ] **Step 1: Write the failing tests**

**File:** `app/src-tauri/src/claude_tail.rs`
```rust
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::fs::{self, OpenOptions};
    use std::io::Write;
    use std::sync::mpsc;
    use std::time::Duration;

    fn tool_line(cwd: &str, uses: serde_json::Value) -> String {
        json!({"type": "assistant", "cwd": cwd, "sessionId": "s1", "timestamp": "2026-10-31T20:00:00Z",
               "message": {"role": "assistant", "content": uses}}).to_string()
    }

    fn append(path: &Path, text: &str) {
        OpenOptions::new().create(true).append(true).open(path).unwrap().write_all(text.as_bytes()).unwrap();
    }

    #[test]
    fn extracts_paths_of_file_tools_only() {
        let line = tool_line("/w", json!([
            {"type": "text", "text": "I will edit main.py"},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "/w/main.py", "old_string": "a"}},
            {"type": "tool_use", "name": "NotebookEdit", "input": {"notebook_path": "/w/n.ipynb"}},
            {"type": "tool_use", "name": "Grep", "input": {"pattern": "x", "path": "src"}},
            {"type": "tool_use", "name": "Glob", "input": {"pattern": "*.rs"}},
            {"type": "tool_use", "name": "Bash", "input": {"command": "cat /w/secret.txt"}},
            {"type": "tool_use", "name": "WebFetch", "input": {"url": "https://x"}}
        ]));
        assert_eq!(extract_paths(&line), vec!["/w/main.py", "/w/n.ipynb", "/w/src"]);
    }

    #[test]
    fn ignores_lines_without_tool_use_or_broken_json() {
        let user = json!({"type": "user", "message": {"role": "user", "content": "please \"tool_use\" stuff"}});
        assert!(extract_paths(&user.to_string()).is_empty());
        assert!(extract_paths("{\"tool_use\": oops").is_empty());
        assert!(extract_paths("").is_empty());
        let no_cwd = json!({"message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "rel.md"}}]}});
        assert!(extract_paths(&no_cwd.to_string()).is_empty(), "relative path without cwd is dropped");
    }

    #[test]
    fn first_sight_starts_at_the_end_then_reads_only_new_lines() {
        let dir = tempfile::tempdir().unwrap();
        let log = dir.path().join("s.jsonl");
        let edit = |p: &str| tool_line("/w", json!([{"type": "tool_use", "name": "Edit", "input": {"file_path": p}}])) + "\n";
        append(&log, &edit("/w/old.py"));
        let mut t = Tailer::default();
        assert!(t.read_new(&log).is_empty(), "history is skipped");
        append(&log, &edit("/w/new.py"));
        assert_eq!(t.read_new(&log), vec!["/w/new.py"]);
        assert!(t.read_new(&log).is_empty(), "nothing new");
    }

    #[test]
    fn partial_lines_wait_for_their_newline() {
        let dir = tempfile::tempdir().unwrap();
        let log = dir.path().join("s.jsonl");
        append(&log, "");
        let mut t = Tailer::default();
        t.read_new(&log);
        let line = tool_line("/w", json!([{"type": "tool_use", "name": "Write", "input": {"file_path": "/w/a.md"}}]));
        let (head, tail) = line.split_at(40);
        append(&log, head);
        assert!(t.read_new(&log).is_empty());
        append(&log, &format!("{tail}\n"));
        assert_eq!(t.read_new(&log), vec!["/w/a.md"]);
    }

    #[test]
    fn truncation_and_priming() {
        let dir = tempfile::tempdir().unwrap();
        let sub = dir.path().join("-Users-m-app");
        fs::create_dir_all(&sub).unwrap();
        let log = sub.join("s.jsonl");
        append(&log, "{\"a\":1}\n");
        let mut t = Tailer::default();
        t.prime(dir.path());
        append(&log, &(tool_line("/w", json!([{"type": "tool_use", "name": "Read", "input": {"file_path": "/w/r.py"}}])) + "\n"));
        assert_eq!(t.read_new(&log), vec!["/w/r.py"], "primed files are read from their size at startup");
        fs::write(&log, "").unwrap();
        assert!(t.read_new(&log).is_empty(), "truncated file restarts at its new end");
    }

    #[test]
    fn spawned_tail_drops_private_paths() {
        let dir = tempfile::tempdir().unwrap();
        let home = dir.path().to_path_buf();
        let root = home.join(".claude/projects/-x");
        fs::create_dir_all(&root).unwrap();
        let log = root.join("s.jsonl");
        append(&log, "");
        let (out_tx, out_rx) = mpsc::channel();
        let tx = spawn(home.join(".claude/projects"), home.clone(), move |paths| out_tx.send(paths).unwrap());
        let ssh = home.join(".ssh/config").to_string_lossy().into_owned();
        let work = home.join("Desktop/a.py").to_string_lossy().into_owned();
        append(&log, &(tool_line("/w", json!([
            {"type": "tool_use", "name": "Read", "input": {"file_path": ssh}},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": work.clone()}}
        ])) + "\n"));
        tx.send(log.clone()).unwrap();
        assert_eq!(out_rx.recv_timeout(Duration::from_secs(2)).unwrap(), vec![work]);
    }
}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd app/src-tauri && cargo test claude_tail`
Expected: FAIL: `cannot find function 'spawn'` / `cannot find type 'Path'`.

- [ ] **Step 3: Implement**

Put this **above** the test module in `app/src-tauri/src/claude_tail.rs`:
```rust
//! Live tail of Claude Code session logs (`~/.claude/projects/**/*.jsonl`): reads only the bytes appended
//! since the last look and keeps nothing but the file paths of file tools. Message text is parsed in memory
//! to find `tool_use` blocks and dropped right away.

use serde_json::Value;
use std::collections::HashMap;
use std::fs::{self, File};
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::sync::mpsc::{self, Sender};
use std::thread;

use crate::paths::is_ignored;

pub const TOOLS: &[&str] = &["Edit", "Write", "MultiEdit", "NotebookEdit", "Read", "Glob", "Grep"];
const PATH_KEYS: &[&str] = &["file_path", "notebook_path", "path"];
/// Appends larger than this (huge pasted images, a copied log) are skipped instead of read.
const MAX_READ: u64 = 16 * 1024 * 1024;

/// Absolute file paths touched by the file tools in one JSONL line; relative ones resolve against `cwd`.
pub fn extract_paths(line: &str) -> Vec<String> {
    if !line.contains("\"tool_use\"") {
        return Vec::new();
    }
    let Ok(v) = serde_json::from_str::<Value>(line) else { return Vec::new() };
    let cwd = v.get("cwd").and_then(Value::as_str);
    let Some(content) = v.pointer("/message/content").and_then(Value::as_array) else { return Vec::new() };
    content
        .iter()
        .filter(|c| c.get("type").and_then(Value::as_str) == Some("tool_use"))
        .filter(|c| c.get("name").and_then(Value::as_str).is_some_and(|n| TOOLS.contains(&n)))
        .filter_map(|c| {
            let input = c.get("input")?;
            PATH_KEYS.iter().find_map(|k| input.get(*k).and_then(Value::as_str))
        })
        .filter_map(|raw| {
            let p = Path::new(raw);
            if p.is_absolute() {
                Some(raw.to_string())
            } else {
                cwd.map(|c| Path::new(c).join(p).to_string_lossy().into_owned())
            }
        })
        .collect()
}

/// Per-file read offsets. A file seen for the first time starts at its current end.
#[derive(Default)]
pub struct Tailer {
    offsets: HashMap<PathBuf, u64>,
}

impl Tailer {
    /// Records the current size of every `*.jsonl` under `root`, so the first append after startup is read.
    pub fn prime(&mut self, root: &Path) {
        let Ok(entries) = fs::read_dir(root) else { return };
        for entry in entries.flatten() {
            let path = entry.path();
            let Ok(kind) = entry.file_type() else { continue };
            if kind.is_dir() {
                self.prime(&path);
            } else if path.extension().is_some_and(|e| e == "jsonl") {
                if let Ok(meta) = entry.metadata() {
                    self.offsets.insert(path, meta.len());
                }
            }
        }
    }

    /// Paths from the complete lines appended since the last call. A trailing partial line stays unread.
    pub fn read_new(&mut self, path: &Path) -> Vec<String> {
        let Ok(len) = fs::metadata(path).map(|m| m.len()) else {
            self.offsets.remove(path);
            return Vec::new();
        };
        let start = match self.offsets.get(path) {
            Some(&start) if start <= len => start,
            _ => {
                self.offsets.insert(path.to_path_buf(), len);
                return Vec::new();
            }
        };
        if len - start > MAX_READ {
            self.offsets.insert(path.to_path_buf(), len);
            return Vec::new();
        }
        let mut buf = Vec::with_capacity((len - start) as usize);
        let read = File::open(path)
            .and_then(|mut f| f.seek(SeekFrom::Start(start)).map(|_| f))
            .and_then(|f| f.take(len - start).read_to_end(&mut buf));
        if read.is_err() {
            return Vec::new();
        }
        let Some(end) = buf.iter().rposition(|&b| b == b'\n') else { return Vec::new() };
        self.offsets.insert(path.to_path_buf(), start + end as u64 + 1);
        String::from_utf8_lossy(&buf[..end]).lines().flat_map(extract_paths).collect()
    }
}

/// Starts the tail thread. Send it the path of every changed `*.jsonl`; it calls `on_paths` with the new
/// file paths (private ones removed). Existing logs are primed before this returns.
pub fn spawn<F>(root: PathBuf, home: PathBuf, on_paths: F) -> Sender<PathBuf>
where
    F: Fn(Vec<String>) + Send + 'static,
{
    let mut tailer = Tailer::default();
    tailer.prime(&root);
    let (tx, rx) = mpsc::channel::<PathBuf>();
    thread::spawn(move || {
        for log in rx {
            let paths: Vec<String> =
                tailer.read_new(&log).into_iter().filter(|p| !is_ignored(Path::new(p), &home)).collect();
            if !paths.is_empty() {
                on_paths(paths);
            }
        }
    });
    tx
}
```

- [ ] **Step 4: Run the tests**

Run: `cd app/src-tauri && cargo test claude_tail`
Expected: `test result: ok. 6 passed`

- [ ] **Step 5: Commit**

```bash
git add app/src-tauri/src/claude_tail.rs app/src-tauri/src/lib.rs
git commit -m "Claude Code live tail: new JSONL bytes only, file paths only"
```

---

### Task 5: File watcher

**Files:**
- Create: `app/src-tauri/src/watcher.rs`
- Modify: `app/src-tauri/src/lib.rs` (add `mod watcher;`)

**Interfaces:**
- Consumes: `paths::is_ignored` (Task 2).
- Produces: `enum Route { Ignore, Fs, ClaudeLog }`; `route(path, home, claude_root) -> Route`; `start(roots: &[PathBuf], home: PathBuf, claude_root: PathBuf, on_fs: impl Fn(PathBuf) + Send + 'static, on_log: impl Fn(PathBuf) + Send + 'static) -> notify::Result<RecommendedWatcher>` (keep the watcher alive).

- [ ] **Step 1: Write the failing tests** (the second test drives real FSEvents in a temp folder; the folder is canonicalized because FSEvents reports `/private/var/...`)

**File:** `app/src-tauri/src/watcher.rs`
```rust
#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::mpsc;
    use std::time::{Duration, Instant};

    #[test]
    fn routes_changes_by_place_and_privacy() {
        let home = Path::new("/Users/m");
        let claude = Path::new("/Users/m/.claude/projects");
        assert_eq!(route(Path::new("/Users/m/Desktop/app/main.py"), home, claude), Route::Fs);
        assert_eq!(route(Path::new("/Users/m/Desktop/app/.git/HEAD"), home, claude), Route::Ignore);
        assert_eq!(route(Path::new("/Users/m/Documents/.env"), home, claude), Route::Ignore);
        assert_eq!(route(Path::new("/Users/m/.claude/projects/-a/s.jsonl"), home, claude), Route::ClaudeLog);
        assert_eq!(route(Path::new("/Users/m/.claude/projects/-a/notes.md"), home, claude), Route::Ignore);
    }

    #[test]
    fn real_watcher_reports_files_and_session_logs() {
        let dir = tempfile::tempdir().unwrap();
        let home = dir.path().canonicalize().unwrap();
        let desk = home.join("Desktop");
        let claude = home.join(".claude/projects/-p");
        fs::create_dir_all(desk.join("node_modules")).unwrap();
        fs::create_dir_all(&claude).unwrap();
        let (tx, rx) = mpsc::channel::<(Route, PathBuf)>();
        let tx2 = tx.clone();
        let _w = start(
            &[desk.clone(), home.join(".claude/projects")],
            home.clone(),
            home.join(".claude/projects"),
            move |p| tx.send((Route::Fs, p)).unwrap(),
            move |p| tx2.send((Route::ClaudeLog, p)).unwrap(),
        )
        .unwrap();
        std::thread::sleep(Duration::from_millis(300));
        fs::write(desk.join("node_modules/x.js"), "x").unwrap();
        fs::write(desk.join("a.txt"), "a").unwrap();
        fs::write(claude.join("s.jsonl"), "{}\n").unwrap();

        let (mut fs_seen, mut log_seen, mut all) = (false, false, Vec::new());
        let deadline = Instant::now() + Duration::from_secs(10);
        while !(fs_seen && log_seen) && Instant::now() < deadline {
            if let Ok((r, p)) = rx.recv_timeout(Duration::from_millis(200)) {
                fs_seen |= r == Route::Fs && p == desk.join("a.txt");
                log_seen |= r == Route::ClaudeLog && p == claude.join("s.jsonl");
                all.push(p);
            }
        }
        assert!(fs_seen && log_seen, "events seen: {all:?}");
        assert!(all.iter().all(|p| !p.to_string_lossy().contains("node_modules")), "{all:?}");
    }
}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd app/src-tauri && cargo test watcher`
Expected: FAIL: `cannot find type 'Path'` / `cannot find function 'route'`.

- [ ] **Step 3: Implement**

Put this **above** the test module in `app/src-tauri/src/watcher.rs`:
```rust
//! Recursive file watcher (FSEvents via `notify`) on ~/Desktop, ~/Documents and ~/.claude/projects.
//! Only paths and event kinds are looked at here; file contents are never read.

use notify::{Event, EventKind, RecommendedWatcher, RecursiveMode, Watcher};
use std::path::{Path, PathBuf};

use crate::paths::is_ignored;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Route {
    /// A private or noisy path: dropped.
    Ignore,
    /// A work file or folder: fires its node (`fs-change`, source "fs").
    Fs,
    /// A Claude Code session log that grew: handed to the tail.
    ClaudeLog,
}

pub fn route(path: &Path, home: &Path, claude_root: &Path) -> Route {
    if is_ignored(path, home) {
        Route::Ignore
    } else if path.starts_with(claude_root) {
        if path.extension().is_some_and(|e| e == "jsonl") {
            Route::ClaudeLog
        } else {
            Route::Ignore
        }
    } else {
        Route::Fs
    }
}

/// Watches every existing root recursively and routes each changed path. Keep the returned watcher alive:
/// dropping it stops the stream. A root that cannot be watched is reported on stderr and skipped.
pub fn start<F, G>(
    roots: &[PathBuf],
    home: PathBuf,
    claude_root: PathBuf,
    on_fs: F,
    on_log: G,
) -> notify::Result<RecommendedWatcher>
where
    F: Fn(PathBuf) + Send + 'static,
    G: Fn(PathBuf) + Send + 'static,
{
    let mut watcher = notify::recommended_watcher(move |res: notify::Result<Event>| {
        let Ok(event) = res else { return };
        if matches!(event.kind, EventKind::Access(_)) {
            return;
        }
        for path in event.paths {
            match route(&path, &home, &claude_root) {
                Route::Fs => on_fs(path),
                Route::ClaudeLog => on_log(path),
                Route::Ignore => {}
            }
        }
    })?;
    for root in roots.iter().filter(|r| r.is_dir()) {
        if let Err(e) = watcher.watch(root, RecursiveMode::Recursive) {
            eprintln!("[neurotree] cannot watch {}: {e}", root.display());
        }
    }
    Ok(watcher)
}
```

- [ ] **Step 4: Run the tests (5 times, to catch flakiness)**

Run: `cd app/src-tauri && for i in 1 2 3 4 5; do cargo test -q watcher 2>&1 | grep "test result"; done`
Expected: five lines `test result: ok. 2 passed`

- [ ] **Step 5: Commit**

```bash
git add app/src-tauri/src/watcher.rs app/src-tauri/src/lib.rs
git commit -m "Recursive FSEvents watcher with privacy routing"
```

---

### Task 6: Indexer supervisor

**Files:**
- Create: `app/src-tauri/src/indexer.rs`
- Modify: `app/src-tauri/src/lib.rs` (add `mod indexer;`)

**Interfaces:**
- Consumes: `events::IndexStatus` (Task 3).
- Produces: consts `BACKOFF = [1 s, 5 s, 30 s]`, `STARTUP_MAX_AGE = 6 h`, `MIN_INTERVAL = 10 min`; `struct IndexerCommand { program: PathBuf, args: Vec<String>, cwd: PathBuf }`; `resolve_command(python: Option<String>, repo: Option<String>, manifest_dir: &str) -> IndexerCommand`; `command_from_env() -> IndexerCommand`; `child_path(current: Option<String>) -> String`; `stderr_tail(stderr: &[u8], max_lines: usize) -> String`; `needs_startup_index(graph: &Path, now: SystemTime) -> bool`; `run_once(&IndexerCommand) -> Result<(), String>`; `run_with_retries(&IndexerCommand, schedule: &[Duration], sleep: impl FnMut(Duration)) -> Result<(), String>`; `ReindexPolicy::{new(min_interval, now), mark_dirty(), on_started(now), due(now) -> bool}`; `Indexer::{new(cmd), command() -> &IndexerCommand, status() -> IndexStatus, mark_dirty(), due() -> bool, start(on_status: impl Fn(IndexStatus) + Send + 'static) -> bool}` (`Indexer: Clone + Send + Sync`); `spawn_scheduler(indexer, tick, on_status: impl Fn(IndexStatus) + Clone + Send + 'static)`.

- [ ] **Step 1: Write the failing tests** (`/bin/sh -c` scripts stand in for Python; a counter file proves the number of attempts)

**File:** `app/src-tauri/src/indexer.rs`
```rust
#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::mpsc;
    use std::time::UNIX_EPOCH;

    fn sh(script: &str, cwd: &Path) -> IndexerCommand {
        IndexerCommand { program: "/bin/sh".into(), args: vec!["-c".into(), script.into()], cwd: cwd.into() }
    }

    #[test]
    fn command_resolution_prefers_env_then_repo_venv() {
        let dev = resolve_command(None, None, "/r/app/src-tauri");
        assert_eq!(dev, IndexerCommand {
            program: "/r/.venv/bin/python".into(),
            args: vec!["-m".into(), "neurotree".into(), "all".into()],
            cwd: "/r".into(),
        });
        let repo = resolve_command(None, Some("/x".into()), "/r/app/src-tauri");
        assert_eq!((repo.program, repo.cwd), ("/x/.venv/bin/python".into(), "/x".into()));
        let py = resolve_command(Some("/opt/py".into()), Some("".into()), "/r/app/src-tauri");
        assert_eq!((py.program, py.cwd), ("/opt/py".into(), "/r".into()));
    }

    #[test]
    fn child_path_adds_homebrew_once() {
        assert_eq!(child_path(Some("/usr/bin:/bin".into())), "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin");
        assert_eq!(child_path(Some("/opt/homebrew/bin:/usr/bin".into())), "/usr/local/bin:/opt/homebrew/bin:/usr/bin");
        assert_eq!(child_path(None), "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin");
    }

    #[test]
    fn backoff_schedule_is_1_5_30_seconds() {
        assert_eq!(BACKOFF, [Duration::from_secs(1), Duration::from_secs(5), Duration::from_secs(30)]);
    }

    #[test]
    fn stderr_tail_keeps_the_last_lines() {
        let err = b"Traceback (most recent call last):\n  File x\n\nValueError: boom\n";
        assert_eq!(stderr_tail(err, 2), "  File x\nValueError: boom");
        assert_eq!(stderr_tail(b"", 3), "");
        assert_eq!(stderr_tail("é".repeat(2000).as_bytes(), 1).chars().count(), 1000);
    }

    #[test]
    fn startup_index_when_graph_missing_or_stale() {
        let dir = tempfile::tempdir().unwrap();
        let graph = dir.path().join("graph.json");
        let now = SystemTime::now();
        assert!(needs_startup_index(&graph, now));
        fs::write(&graph, "{}").unwrap();
        assert!(!needs_startup_index(&graph, now));
        assert!(needs_startup_index(&graph, now + Duration::from_secs(7 * 3600)));
    }

    #[test]
    fn reindex_policy_waits_for_changes_and_ten_minutes() {
        let t0 = Instant::now();
        let mut p = ReindexPolicy::new(MIN_INTERVAL, t0);
        assert!(!p.due(t0 + Duration::from_secs(3600)), "no changes, no reindex");
        p.mark_dirty();
        assert!(!p.due(t0 + Duration::from_secs(60)));
        assert!(p.due(t0 + MIN_INTERVAL));
        p.on_started(t0 + MIN_INTERVAL);
        assert!(!p.due(t0 + MIN_INTERVAL * 3), "a run clears the changes");
    }

    #[test]
    fn retries_follow_the_schedule_and_report_stderr() {
        let dir = tempfile::tempdir().unwrap();
        let mut slept = Vec::new();
        let err = run_with_retries(&sh("echo try >> n; echo boom >&2; exit 3", dir.path()), &BACKOFF, |d| slept.push(d));
        assert_eq!(err, Err("boom".into()));
        assert_eq!(slept, BACKOFF.to_vec());
        assert_eq!(fs::read_to_string(dir.path().join("n")).unwrap().lines().count(), 4, "1 run + 3 retries");
    }

    #[test]
    fn a_retry_that_succeeds_stops_the_loop() {
        let dir = tempfile::tempdir().unwrap();
        let mut slept = Vec::new();
        let ok = run_with_retries(&sh("echo try >> n; [ $(wc -l < n) -ge 2 ]", dir.path()), &BACKOFF, |d| slept.push(d));
        assert_eq!(ok, Ok(()));
        assert_eq!(slept, vec![Duration::from_secs(1)]);
        let missing = IndexerCommand { program: "/no/python".into(), args: vec![], cwd: dir.path().into() };
        assert!(run_with_retries(&missing, &[], |_| {}).unwrap_err().contains("/no/python"));
    }

    #[test]
    fn indexer_runs_one_at_a_time_and_reports_start_and_end() {
        let dir = tempfile::tempdir().unwrap();
        let ix = Indexer::new(sh("sleep 0.3", dir.path()));
        let (tx, rx) = mpsc::channel();
        let tx2 = tx.clone();
        assert!(ix.start(move |s| tx.send(s).unwrap()));
        assert!(!ix.start(move |s| tx2.send(s).unwrap()), "second start while running");
        let first = rx.recv_timeout(Duration::from_secs(2)).unwrap();
        assert_eq!(first, IndexStatus { running: true, finished_at: None, error: None });
        let done = rx.recv_timeout(Duration::from_secs(5)).unwrap();
        assert!(!done.running && done.error.is_none());
        let at = done.finished_at.unwrap();
        let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs_f64();
        assert!((now - at).abs() < 5.0);
        assert_eq!(ix.status(), done);
    }
}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd app/src-tauri && cargo test indexer`
Expected: FAIL: `cannot find type 'IndexerCommand' in this scope`.

- [ ] **Step 3: Implement**

Put this **above** the test module in `app/src-tauri/src/indexer.rs`:
```rust
//! Runs and supervises the Python indexer (`python -m neurotree all`): one run at a time, retries with
//! backoff, a startup run when the graph is missing or stale, and a reindex at most every 10 minutes after
//! the watcher saw changes.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use crate::events::IndexStatus;

/// Waits before retry 1, 2 and 3 after a failed run.
pub const BACKOFF: [Duration; 3] = [Duration::from_secs(1), Duration::from_secs(5), Duration::from_secs(30)];
/// A graph older than this gets a full index at startup.
pub const STARTUP_MAX_AGE: Duration = Duration::from_secs(6 * 3600);
/// Change-triggered reindexes are at least this far apart.
pub const MIN_INTERVAL: Duration = Duration::from_secs(600);
const EXTRA_PATH: &[&str] = &["/opt/homebrew/bin", "/usr/local/bin"];

#[derive(Debug, Clone, PartialEq)]
pub struct IndexerCommand {
    pub program: PathBuf,
    pub args: Vec<String>,
    pub cwd: PathBuf,
}

fn non_empty(v: Option<String>) -> Option<String> {
    v.filter(|s| !s.is_empty())
}

/// Python = `NEUROTREE_PYTHON`, else `<repo>/.venv/bin/python`; repo = `NEUROTREE_REPO`, else
/// `<manifest_dir>/../..` (the checkout this binary was built from). Runs `-m neurotree all` in the repo.
pub fn resolve_command(python: Option<String>, repo: Option<String>, manifest_dir: &str) -> IndexerCommand {
    let repo = non_empty(repo).map(PathBuf::from).unwrap_or_else(|| {
        let m = Path::new(manifest_dir);
        m.parent().and_then(Path::parent).unwrap_or(m).to_path_buf()
    });
    let program = non_empty(python).map(PathBuf::from).unwrap_or_else(|| repo.join(".venv/bin/python"));
    IndexerCommand { program, args: vec!["-m".into(), "neurotree".into(), "all".into()], cwd: repo }
}

pub fn command_from_env() -> IndexerCommand {
    resolve_command(
        std::env::var("NEUROTREE_PYTHON").ok(),
        std::env::var("NEUROTREE_REPO").ok(),
        env!("CARGO_MANIFEST_DIR"),
    )
}

/// PATH for the indexer: apps started from Finder get a bare PATH, but the indexer needs `git` and `gh`.
pub fn child_path(current: Option<String>) -> String {
    let current = non_empty(current).unwrap_or_else(|| "/usr/bin:/bin:/usr/sbin:/sbin".into());
    let have: Vec<&str> = current.split(':').collect();
    let mut parts: Vec<&str> = EXTRA_PATH.iter().copied().filter(|p| !have.contains(p)).collect();
    parts.extend(have);
    parts.join(":")
}

/// The last `max_lines` non-empty lines of stderr, at most 1000 characters (the end is kept).
pub fn stderr_tail(stderr: &[u8], max_lines: usize) -> String {
    let text = String::from_utf8_lossy(stderr);
    let lines: Vec<&str> = text.lines().map(str::trim_end).filter(|l| !l.is_empty()).collect();
    let tail = lines[lines.len().saturating_sub(max_lines)..].join("\n");
    let n = tail.chars().count();
    tail.chars().skip(n.saturating_sub(1000)).collect()
}

/// True if `graph.json` is missing or was written more than 6 h before `now`.
pub fn needs_startup_index(graph: &Path, now: SystemTime) -> bool {
    match std::fs::metadata(graph).and_then(|m| m.modified()) {
        Ok(modified) => now.duration_since(modified).map(|age| age > STARTUP_MAX_AGE).unwrap_or(false),
        Err(_) => true,
    }
}

/// One run. Err carries the stderr tail (or the exit status / spawn error when stderr is empty).
pub fn run_once(cmd: &IndexerCommand) -> Result<(), String> {
    let out = Command::new(&cmd.program)
        .args(&cmd.args)
        .current_dir(&cmd.cwd)
        .env("PATH", child_path(std::env::var("PATH").ok()))
        .stdin(Stdio::null())
        .stdout(Stdio::inherit())
        .stderr(Stdio::piped())
        .output()
        .map_err(|e| format!("cannot start {}: {e}", cmd.program.display()))?;
    if out.status.success() {
        return Ok(());
    }
    let tail = stderr_tail(&out.stderr, 8);
    Err(if tail.is_empty() { format!("indexer failed ({})", out.status) } else { tail })
}

/// Runs once, then retries after each wait in `schedule` until a run succeeds; returns the last error.
pub fn run_with_retries(cmd: &IndexerCommand, schedule: &[Duration], mut sleep: impl FnMut(Duration)) -> Result<(), String> {
    let mut result = run_once(cmd);
    for wait in schedule {
        if result.is_ok() {
            break;
        }
        sleep(*wait);
        result = run_once(cmd);
    }
    result
}

/// Decides when the watcher's changes justify a full reindex.
#[derive(Debug, Clone)]
pub struct ReindexPolicy {
    min_interval: Duration,
    dirty: bool,
    last_start: Instant,
}

impl ReindexPolicy {
    pub fn new(min_interval: Duration, now: Instant) -> Self {
        ReindexPolicy { min_interval, dirty: false, last_start: now }
    }
    pub fn mark_dirty(&mut self) {
        self.dirty = true;
    }
    pub fn on_started(&mut self, now: Instant) {
        self.dirty = false;
        self.last_start = now;
    }
    pub fn due(&self, now: Instant) -> bool {
        self.dirty && now.duration_since(self.last_start) >= self.min_interval
    }
}

struct State {
    running: bool,
    finished_at: Option<f64>,
    error: Option<String>,
    policy: ReindexPolicy,
}

/// Shared handle to the indexer; clone it freely.
#[derive(Clone)]
pub struct Indexer {
    cmd: Arc<IndexerCommand>,
    state: Arc<Mutex<State>>,
}

fn epoch_now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

impl Indexer {
    pub fn new(cmd: IndexerCommand) -> Self {
        let state = State { running: false, finished_at: None, error: None, policy: ReindexPolicy::new(MIN_INTERVAL, Instant::now()) };
        Indexer { cmd: Arc::new(cmd), state: Arc::new(Mutex::new(state)) }
    }

    pub fn command(&self) -> &IndexerCommand {
        &self.cmd
    }

    pub fn status(&self) -> IndexStatus {
        let s = self.state.lock().unwrap();
        IndexStatus { running: s.running, finished_at: s.finished_at, error: s.error.clone() }
    }

    pub fn mark_dirty(&self) {
        self.state.lock().unwrap().policy.mark_dirty();
    }

    /// True when changes were seen, nothing runs, and the last run started at least 10 minutes ago.
    pub fn due(&self) -> bool {
        let s = self.state.lock().unwrap();
        !s.running && s.policy.due(Instant::now())
    }

    /// Starts a run (with retries) on a background thread unless one is running. `on_status` gets
    /// `{running: true}` now and `{running: false, finished_at | error}` at the end.
    pub fn start<F>(&self, on_status: F) -> bool
    where
        F: Fn(IndexStatus) + Send + 'static,
    {
        {
            let mut s = self.state.lock().unwrap();
            if s.running {
                return false;
            }
            s.running = true;
            s.finished_at = None;
            s.error = None;
            s.policy.on_started(Instant::now());
        }
        let (cmd, state) = (self.cmd.clone(), self.state.clone());
        thread::spawn(move || {
            on_status(IndexStatus { running: true, finished_at: None, error: None });
            let result = run_with_retries(&cmd, &BACKOFF, thread::sleep);
            let done = {
                let mut s = state.lock().unwrap();
                s.running = false;
                match result {
                    Ok(()) => s.finished_at = Some(epoch_now()),
                    Err(e) => s.error = Some(e),
                }
                IndexStatus { running: false, finished_at: s.finished_at, error: s.error.clone() }
            };
            on_status(done);
        });
        true
    }
}

/// Checks every `tick` whether a change-triggered reindex is due and starts it.
pub fn spawn_scheduler<F>(indexer: Indexer, tick: Duration, on_status: F)
where
    F: Fn(IndexStatus) + Clone + Send + 'static,
{
    thread::spawn(move || loop {
        thread::sleep(tick);
        if indexer.due() {
            indexer.start(on_status.clone());
        }
    });
}
```

- [ ] **Step 4: Run the tests**

Run: `cd app/src-tauri && cargo test indexer`
Expected: `test result: ok. 9 passed`

- [ ] **Step 5: Commit**

```bash
git add app/src-tauri/src/indexer.rs app/src-tauri/src/lib.rs
git commit -m "Indexer supervisor: command resolution, retries 1/5/30 s, startup and 10 min policy"
```

---

### Task 7: Viewer commands

**Files:**
- Create: `app/src-tauri/src/commands.rs`
- Modify: `app/src-tauri/src/lib.rs` (register commands, manage `AppState`, add `status_sink`)

**Interfaces:**
- Consumes: `paths::data_dir()` (Task 2), `events::{IndexStatus, INDEX_STATUS}` (Task 3), `indexer::{Indexer, command_from_env}` (Task 6).
- Produces: `struct AppState { data_dir: PathBuf, indexer: Indexer }` (managed); `enum Action { Open, Reveal }`; `read_graph(dir) -> Result<String, String>`; `read_status(dir) -> Option<Value>`; `checked_path(path: &str, action: Action) -> Result<PathBuf, String>`; Tauri commands `load_graph` (async), `status`, `open_path`, `reveal_path`, `reindex`; in lib.rs `pub(crate) fn status_sink(app: &AppHandle) -> impl Fn(IndexStatus) + Clone + Send + 'static`.

- [ ] **Step 1: Write the failing tests** (no test calls `open`: only the path check is exercised, so nothing pops up during `cargo test`)

**File:** `app/src-tauri/src/commands.rs`
```rust
#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn graph_is_returned_raw_or_explained() {
        let dir = tempfile::tempdir().unwrap();
        let err = read_graph(dir.path()).unwrap_err();
        assert!(err.contains("graph.json") && err.contains("Reindex"), "{err}");
        fs::write(dir.path().join("graph.json"), r#"{"nodes":[]}"#).unwrap();
        assert_eq!(read_graph(dir.path()).unwrap(), r#"{"nodes":[]}"#);
    }

    #[test]
    fn status_is_parsed_or_none() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(read_status(dir.path()), None);
        fs::write(dir.path().join("status.json"), "not json").unwrap();
        assert_eq!(read_status(dir.path()), None);
        fs::write(dir.path().join("status.json"), r#"{"finished_at": 5.0, "steps": {}}"#).unwrap();
        assert_eq!(read_status(dir.path()).unwrap()["finished_at"], 5.0);
    }

    #[test]
    fn only_existing_absolute_paths_are_opened() {
        let dir = tempfile::tempdir().unwrap();
        let doc = dir.path().join("plan.md");
        fs::write(&doc, "x").unwrap();
        let doc_s = doc.to_string_lossy().into_owned();
        assert_eq!(checked_path(&doc_s, Action::Open).unwrap(), doc);
        assert_eq!(checked_path(&doc_s, Action::Reveal).unwrap(), doc);
        assert!(checked_path("plan.md", Action::Open).unwrap_err().contains("absolute"));
        assert!(checked_path("-a", Action::Reveal).is_err());
        let missing = dir.path().join("gone.md").to_string_lossy().into_owned();
        assert!(checked_path(&missing, Action::Open).unwrap_err().contains("does not exist"));
    }

    #[test]
    fn launchable_bundles_are_revealed_but_never_opened() {
        let dir = tempfile::tempdir().unwrap();
        for name in ["run.command", "Tool.app", "x.workflow"] {
            let p = dir.path().join(name);
            fs::write(&p, "").unwrap();
            let s = p.to_string_lossy().into_owned();
            assert!(checked_path(&s, Action::Open).is_err(), "{name}");
            assert!(checked_path(&s, Action::Reveal).is_ok(), "{name}");
        }
    }
}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd app/src-tauri && cargo test commands`
Expected: FAIL: `cannot find function 'read_graph' in this scope`.

- [ ] **Step 3: Implement**

Put this **above** the test module in `app/src-tauri/src/commands.rs`:
```rust
//! Commands the viewer invokes: `load_graph`, `status`, `open_path`, `reveal_path`, `reindex`.

use serde_json::Value;
use std::path::{Path, PathBuf};
use std::process::Command;
use tauri::{AppHandle, State};

use crate::indexer::Indexer;

/// Managed app state shared by commands, tray and background threads.
pub struct AppState {
    pub data_dir: PathBuf,
    pub indexer: Indexer,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    Open,
    Reveal,
}

/// Opening these would launch or install something instead of showing it.
const LAUNCHABLE: &[&str] = &["app", "command", "tool", "terminal", "workflow", "action", "pkg", "mpkg"];

pub fn read_graph(dir: &Path) -> Result<String, String> {
    let path = dir.join("graph.json");
    std::fs::read_to_string(&path)
        .map_err(|e| format!("no graph.json in {} ({e}); run Reindex from the menubar", dir.display()))
}

pub fn read_status(dir: &Path) -> Option<Value> {
    let text = std::fs::read_to_string(dir.join("status.json")).ok()?;
    serde_json::from_str(&text).ok()
}

/// Accepts only absolute paths that exist; `Open` also refuses launchable bundles and scripts.
pub fn checked_path(path: &str, action: Action) -> Result<PathBuf, String> {
    let p = PathBuf::from(path);
    if !p.is_absolute() {
        return Err(format!("not an absolute path: {path}"));
    }
    if !p.exists() {
        return Err(format!("does not exist: {path}"));
    }
    let ext = p.extension().map(|e| e.to_string_lossy().to_ascii_lowercase()).unwrap_or_default();
    if action == Action::Open && LAUNCHABLE.contains(&ext.as_str()) {
        return Err(format!("refusing to open a launchable file, use Reveal: {path}"));
    }
    Ok(p)
}

fn run_open(args: &[&std::ffi::OsStr]) -> Result<(), String> {
    let out = Command::new("/usr/bin/open").args(args).output().map_err(|e| format!("open failed: {e}"))?;
    if out.status.success() {
        Ok(())
    } else {
        Err(String::from_utf8_lossy(&out.stderr).trim().to_string())
    }
}

/// Raw text of `<data dir>/graph.json` (async: it can be tens of MB, keep it off the main thread).
#[tauri::command]
pub async fn load_graph(state: State<'_, AppState>) -> Result<String, String> {
    read_graph(&state.data_dir)
}

/// Parsed `status.json`, or null when there is none yet.
#[tauri::command]
pub fn status(state: State<'_, AppState>) -> Option<Value> {
    read_status(&state.data_dir)
}

/// `open <path>`: the file or folder in its default app.
#[tauri::command]
pub fn open_path(path: String) -> Result<(), String> {
    let p = checked_path(&path, Action::Open)?;
    run_open(&[p.as_os_str()])
}

/// `open -R <path>`: reveal in Finder.
#[tauri::command]
pub fn reveal_path(path: String) -> Result<(), String> {
    let p = checked_path(&path, Action::Reveal)?;
    run_open(&["-R".as_ref(), p.as_os_str()])
}

/// Starts a full reindex in the background; false if one is already running.
#[tauri::command]
pub fn reindex(app: AppHandle, state: State<'_, AppState>) -> bool {
    state.indexer.start(crate::status_sink(&app))
}
```

- [ ] **Step 4: Register the commands and the state in lib.rs**

**File:** `app/src-tauri/src/lib.rs`
```rust
//! Neurotree desktop shell: one window with the viewer, closing it only hides it.

mod claude_tail;
mod commands;
mod events;
mod indexer;
mod paths;
mod watcher;

use tauri::{AppHandle, Emitter, Manager, WindowEvent};

use crate::commands::AppState;
use crate::events::IndexStatus;
use crate::indexer::Indexer;

/// Where index runs report to: the `index-status` event for the viewer.
pub(crate) fn status_sink(app: &AppHandle) -> impl Fn(IndexStatus) + Clone + Send + 'static {
    let app = app.clone();
    move |s: IndexStatus| {
        let _ = app.emit(events::INDEX_STATUS, &s);
    }
}

pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            commands::load_graph,
            commands::status,
            commands::open_path,
            commands::reveal_path,
            commands::reindex
        ])
        .setup(|app| {
            app.manage(AppState { data_dir: paths::data_dir(), indexer: Indexer::new(indexer::command_from_env()) });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building Neurotree")
        .run(|app, event| {
            #[cfg(target_os = "macos")]
            if let tauri::RunEvent::Reopen { .. } = event {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.show();
                    let _ = w.set_focus();
                }
            }
        });
}

#[cfg(test)]
mod config_contract {
    use serde_json::Value;

    fn conf() -> Value {
        serde_json::from_str(include_str!("../tauri.conf.json")).unwrap()
    }

    #[test]
    fn build_points_at_the_viewer() {
        let c = conf();
        assert_eq!(c["build"]["frontendDist"], "../../viewer/dist");
        assert_eq!(c["build"]["devUrl"], "http://localhost:5173");
    }

    #[test]
    fn main_window_matches_the_viewer_contract() {
        let c = conf();
        let w = &c["app"]["windows"][0];
        assert_eq!(w["label"], "main");
        assert_eq!(w["title"], "Neurotree");
        assert_eq!(w["width"], 1440);
        assert_eq!(w["height"], 900);
        assert_eq!(w["backgroundColor"], "#05060a");
    }
}
```

- [ ] **Step 5: Run the tests and build**

Run: `cd app/src-tauri && cargo test commands && cargo build`
Expected: `test result: ok. 4 passed`; build finishes (dead-code warnings for the not-yet-wired watcher/tail are fine at this point).

- [ ] **Step 6: Commit**

```bash
git add app/src-tauri/src/commands.rs app/src-tauri/src/lib.rs
git commit -m "Viewer commands: load_graph, status, open_path, reveal_path, reindex"
```

---

### Task 8: Menubar and full wiring

**Files:**
- Create: `app/src-tauri/src/tray.rs`
- Modify: `app/src-tauri/src/lib.rs` (final version below)

**Interfaces:**
- Consumes: everything above; `icons/tray.png` (Task 1).
- Produces: `struct Summary { finished_at: Option<f64>, nodes: Option<u64>, failed_steps: usize }`; `summarize(status: Option<&Value>) -> Summary` (nodes from `status.steps.export.nodes`, failed = steps with an `error` key); `thousands(u64) -> String`; `status_line(&IndexStatus, &Summary, hhmm: impl Fn(f64) -> String) -> String`; `local_hhmm(epoch: f64) -> String`; `show_main(&AppHandle)`; `create(&AppHandle) -> tauri::Result<()>`; `refresh(&AppHandle)`.

- [ ] **Step 1: Write the failing tests**

**File:** `app/src-tauri/src/tray.rs`
```rust
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn idle() -> IndexStatus {
        IndexStatus { running: false, finished_at: None, error: None }
    }

    fn at(_: f64) -> String {
        "21:56".into()
    }

    #[test]
    fn thousands_separators() {
        assert_eq!(thousands(0), "0");
        assert_eq!(thousands(999), "999");
        assert_eq!(thousands(3412), "3,412");
        assert_eq!(thousands(1234567), "1,234,567");
    }

    #[test]
    fn summary_reads_finish_time_nodes_and_failed_steps() {
        let status = json!({"finished_at": 100.0, "steps": {
            "git": {"ok": 3, "seconds": 1.0},
            "github": {"ok": 0, "errors": 1, "error": "FileNotFoundError: gh"},
            "export": {"nodes": 3412, "edges": 9000, "seconds": 0.4}}});
        assert_eq!(summarize(Some(&status)), Summary { finished_at: Some(100.0), nodes: Some(3412), failed_steps: 1 });
        assert_eq!(summarize(None), Summary { finished_at: None, nodes: None, failed_steps: 0 });
    }

    #[test]
    fn status_lines() {
        let done = Summary { finished_at: Some(1.0), nodes: Some(3412), failed_steps: 0 };
        assert_eq!(status_line(&idle(), &done, at), "Indexed 21:56 · 3,412 nodes");
        let partial = Summary { failed_steps: 2, ..done.clone() };
        assert_eq!(status_line(&idle(), &partial, at), "Indexed 21:56 · 3,412 nodes · 2 sources failed");
        let running = IndexStatus { running: true, ..idle() };
        assert_eq!(status_line(&running, &done, at), "Indexing…");
        let failed = IndexStatus { error: Some("boom".into()), ..idle() };
        assert_eq!(status_line(&failed, &done, at), "Index failed");
        let never = Summary { finished_at: None, nodes: None, failed_steps: 0 };
        assert_eq!(status_line(&idle(), &never, at), "Not indexed yet");
    }

    #[test]
    fn local_time_is_hours_and_minutes() {
        let s = local_hhmm(1_700_000_000.0);
        assert_eq!(s.len(), 5);
        assert_eq!(&s[2..3], ":");
    }
}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd app/src-tauri && cargo test tray` (after adding `mod tray;` to lib.rs)
Expected: FAIL: `cannot find struct, variant or union type 'Summary'`.

- [ ] **Step 3: Implement the tray**

Put this **above** the test module in `app/src-tauri/src/tray.rs`:
```rust
//! Menubar icon: a status line, "Reindex now", "Show Neurotree", "Quit".

use serde_json::Value;
use tauri::image::Image;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, Wry};

use crate::commands::{read_status, AppState};
use crate::events::IndexStatus;

const TRAY_ID: &str = "neurotree";

/// The disabled first menu item, kept so its text can change.
pub struct TrayStatus(pub MenuItem<Wry>);

/// What the menubar needs from `status.json`.
#[derive(Debug, Clone, PartialEq)]
pub struct Summary {
    pub finished_at: Option<f64>,
    pub nodes: Option<u64>,
    pub failed_steps: usize,
}

pub fn summarize(status: Option<&Value>) -> Summary {
    let steps = status.and_then(|s| s.get("steps")).and_then(Value::as_object);
    Summary {
        finished_at: status.and_then(|s| s.get("finished_at")).and_then(Value::as_f64),
        nodes: steps.and_then(|s| s.get("export")).and_then(|e| e.get("nodes")).and_then(Value::as_u64),
        failed_steps: steps.map_or(0, |s| s.values().filter(|v| v.get("error").is_some()).count()),
    }
}

pub fn thousands(n: u64) -> String {
    let digits = n.to_string();
    let mut out = String::new();
    for (i, ch) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i) % 3 == 0 {
            out.push(',');
        }
        out.push(ch);
    }
    out
}

/// "Indexed 21:56 · 3,412 nodes" / "Indexing…" / "Index failed" / "Not indexed yet".
pub fn status_line(ix: &IndexStatus, summary: &Summary, hhmm: impl Fn(f64) -> String) -> String {
    if ix.running {
        return "Indexing…".into();
    }
    if ix.error.is_some() {
        return "Index failed".into();
    }
    let Some(t) = summary.finished_at else { return "Not indexed yet".into() };
    let mut line = format!("Indexed {}", hhmm(t));
    if let Some(n) = summary.nodes {
        line += &format!(" · {} nodes", thousands(n));
    }
    if summary.failed_steps > 0 {
        let s = if summary.failed_steps == 1 { "" } else { "s" };
        line += &format!(" · {} source{s} failed", summary.failed_steps);
    }
    line
}

/// Local wall-clock "HH:MM" for epoch seconds.
pub fn local_hhmm(epoch: f64) -> String {
    let t = epoch as libc::time_t;
    // SAFETY: localtime_r writes only into the zeroed `tm` we own and reads `t`.
    let mut tm: libc::tm = unsafe { std::mem::zeroed() };
    if unsafe { libc::localtime_r(&t, &mut tm) }.is_null() {
        return "--:--".into();
    }
    format!("{:02}:{:02}", tm.tm_hour, tm.tm_min)
}

pub fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

/// Builds the menubar icon and its menu. Call once from `setup`, after `AppState` is managed.
pub fn create(app: &AppHandle) -> tauri::Result<()> {
    let status = MenuItem::with_id(app, "status", "Not indexed yet", false, None::<&str>)?;
    let reindex = MenuItem::with_id(app, "reindex", "Reindex now", true, None::<&str>)?;
    let show = MenuItem::with_id(app, "show", "Show Neurotree", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[
        &status,
        &PredefinedMenuItem::separator(app)?,
        &reindex,
        &show,
        &PredefinedMenuItem::separator(app)?,
        &quit,
    ])?;
    TrayIconBuilder::with_id(TRAY_ID)
        .icon(Image::from_bytes(include_bytes!("../icons/tray.png"))?)
        .icon_as_template(true)
        .tooltip("Neurotree")
        .menu(&menu)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "reindex" => {
                if let Some(state) = app.try_state::<AppState>() {
                    state.indexer.start(crate::status_sink(app));
                }
            }
            "show" => show_main(app),
            "quit" => app.exit(0),
            _ => {}
        })
        .build(app)?;
    app.manage(TrayStatus(status));
    refresh(app);
    Ok(())
}

/// Re-reads `status.json` and the indexer state and updates the status line and tooltip.
pub fn refresh(app: &AppHandle) {
    let (Some(state), Some(tray)) = (app.try_state::<AppState>(), app.try_state::<TrayStatus>()) else { return };
    let ix = state.indexer.status();
    let summary = summarize(read_status(&state.data_dir).as_ref());
    let _ = tray.0.set_text(status_line(&ix, &summary, local_hhmm));
    if let Some(icon) = app.tray_by_id(TRAY_ID) {
        let tip = ix.error.as_deref().map_or_else(|| "Neurotree".to_string(), |e| format!("Neurotree: {e}"));
        let _ = icon.set_tooltip(Some(tip));
    }
}
```

- [ ] **Step 4: Final lib.rs** (tray, live pipeline, scheduler with a 30 s tick, startup index; `status_sink` now also refreshes the menubar)

**File:** `app/src-tauri/src/lib.rs`
```rust
//! Neurotree desktop shell: the viewer window (closing only hides it), a menubar icon, the indexer
//! supervisor and the live watcher that fires `fs-change` events into the viewer.

mod claude_tail;
mod commands;
mod events;
mod indexer;
mod paths;
mod tray;
mod watcher;

use std::path::Path;
use std::sync::Mutex;
use std::time::{Duration, SystemTime};

use tauri::{AppHandle, Emitter, Manager, WindowEvent};

use crate::commands::AppState;
use crate::events::{Change, IndexStatus, Source};
use crate::indexer::Indexer;

/// Keeps the FSEvents stream alive for the life of the app.
struct LiveWatcher(#[allow(dead_code)] Mutex<notify::RecommendedWatcher>);

/// Where index runs report to: the `index-status` event for the viewer, then the menubar line.
pub(crate) fn status_sink(app: &AppHandle) -> impl Fn(IndexStatus) + Clone + Send + 'static {
    let app = app.clone();
    move |s: IndexStatus| {
        let _ = app.emit(events::INDEX_STATUS, &s);
        tray::refresh(&app);
    }
}

/// Watcher -> (Claude tail) -> batcher -> `fs-change`; every change also marks the index dirty.
fn start_live(app: &AppHandle, home: &Path, indexer: &Indexer) {
    let emitter = app.clone();
    let changes = events::spawn_batcher(events::WINDOW, events::SETTLE, move |batch| {
        let _ = emitter.emit(events::FS_CHANGE, &batch);
    });
    let claude_root = paths::claude_projects(home);
    let to_batcher = changes.clone();
    let logs = claude_tail::spawn(claude_root.clone(), home.to_path_buf(), move |paths| {
        for path in paths {
            let _ = to_batcher.send(Change { source: Source::Claude, path });
        }
    });
    let (ix_fs, ix_log) = (indexer.clone(), indexer.clone());
    let watcher = watcher::start(
        &paths::watch_roots(home),
        home.to_path_buf(),
        claude_root,
        move |p| {
            ix_fs.mark_dirty();
            let _ = changes.send(Change { source: Source::Fs, path: p.to_string_lossy().into_owned() });
        },
        move |p| {
            ix_log.mark_dirty();
            let _ = logs.send(p);
        },
    );
    match watcher {
        Ok(w) => {
            app.manage(LiveWatcher(Mutex::new(w)));
        }
        Err(e) => eprintln!("[neurotree] live watcher unavailable: {e}"),
    }
}

pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            commands::load_graph,
            commands::status,
            commands::open_path,
            commands::reveal_path,
            commands::reindex
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            let home = paths::home_dir();
            let data_dir = paths::data_dir();
            let indexer = Indexer::new(indexer::command_from_env());
            let cmd = indexer.command();
            eprintln!("[neurotree] data {}, indexer {} in {}", data_dir.display(), cmd.program.display(), cmd.cwd.display());
            app.manage(AppState { data_dir: data_dir.clone(), indexer: indexer.clone() });
            tray::create(&handle)?;
            start_live(&handle, &home, &indexer);
            let sink = status_sink(&handle);
            indexer::spawn_scheduler(indexer.clone(), Duration::from_secs(30), sink.clone());
            if indexer::needs_startup_index(&data_dir.join("graph.json"), SystemTime::now()) {
                indexer.start(sink);
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building Neurotree")
        .run(|app, event| {
            #[cfg(target_os = "macos")]
            if let tauri::RunEvent::Reopen { .. } = event {
                tray::show_main(app);
            }
        });
}

#[cfg(test)]
mod config_contract {
    use serde_json::Value;

    fn conf() -> Value {
        serde_json::from_str(include_str!("../tauri.conf.json")).unwrap()
    }

    #[test]
    fn build_points_at_the_viewer() {
        let c = conf();
        assert_eq!(c["build"]["frontendDist"], "../../viewer/dist");
        assert_eq!(c["build"]["devUrl"], "http://localhost:5173");
    }

    #[test]
    fn main_window_matches_the_viewer_contract() {
        let c = conf();
        let w = &c["app"]["windows"][0];
        assert_eq!(w["label"], "main");
        assert_eq!(w["title"], "Neurotree");
        assert_eq!(w["width"], 1440);
        assert_eq!(w["height"], 900);
        assert_eq!(w["backgroundColor"], "#05060a");
    }
}
```

- [ ] **Step 5: Run everything and build without warnings**

Run: `cd app/src-tauri && cargo test && cargo build 2>&1 | grep -c '^warning' || true`
Expected: `test result: ok. 38 passed`; warning count `0`.

- [ ] **Step 6: Smoke launch for 8 seconds with a fresh fake graph (no index run, no window left behind)**

```bash
S=$(mktemp -d) && echo '{"meta":{},"nodes":[],"edges":[],"events":[]}' > $S/graph.json \
  && echo '{"finished_at": 1793500000.0, "steps": {"export": {"nodes": 3412}}}' > $S/status.json \
  && cd app/src-tauri && NEUROTREE_HOME=$S perl -e 'alarm shift; exec @ARGV' 8 ./target/debug/neurotree; \
  pgrep -fl target/debug/neurotree || echo "no app process left"
```
Expected: one line `[neurotree] data <tmp>, indexer <repo>/.venv/bin/python in <repo>`, no panic, then `no app process left`.

- [ ] **Step 7: Commit**

```bash
git add app/src-tauri/src/tray.rs app/src-tauri/src/lib.rs
git commit -m "Menubar status and full wiring: watcher, Claude tail, batcher, scheduler, startup index"
```

---

### Task 9: Release bundle and manual checks

**Files:** none committed (a throwaway `viewer/dist/index.html` only when the real viewer is not built yet; `dist/` is gitignored).

- [ ] **Step 1: Build the `.app`**

With the real viewer: `cd app && npm run build` (builds `../viewer` then `tauri build --bundles app`).
Without it: `mkdir -p viewer/dist && echo '<!doctype html><title>Neurotree</title>' > viewer/dist/index.html && cd app && npx tauri build --bundles app`.
Expected: `app/src-tauri/target/release/bundle/macos/Neurotree.app`. Check `git status --short` shows nothing under `viewer/`.

- [ ] **Step 2: Dev run with the viewer** (two terminals)

```bash
npm --prefix viewer run dev            # Vite on http://localhost:5173
cd app && NEUROTREE_REPO=~/Desktop/Neurotree npx tauri dev
```
(`NEUROTREE_REPO` is only needed when the binary was built from a different checkout, e.g. a worktree.)

- [ ] **Step 3: Manual checklist**
  - Window "Neurotree", 1440x900, dark before the page paints.
  - Menubar icon; first line reads "Indexed HH:MM · N nodes" (or "Indexing…" when graph.json was missing or older than 6 h).
  - "Reindex now" -> "Indexing…" -> "Indexed …"; the viewer receives two `index-status` events.
  - Close the window: the app stays in the menubar; "Show Neurotree" (or the Dock icon) brings it back; "Quit" exits.
  - `touch ~/Desktop/<some project>/README.md`: within ~2 s the viewer gets `fs-change` `{paths: [...], source: "fs"}`; `touch ~/Desktop/x/.env` produces nothing.
  - Let Claude Code edit a file: the viewer gets `fs-change` with `source: "claude"` and that file's path.
  - Performance gate (spec): 20,000 nodes at >= 30 fps in this window with the viewer's fixture; otherwise the spec says switch the shell to Electron.
