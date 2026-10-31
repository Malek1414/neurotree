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
