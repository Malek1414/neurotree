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
