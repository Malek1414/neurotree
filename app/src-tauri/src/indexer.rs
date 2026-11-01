//! Runs and supervises the Python indexer (`python -m neurotree all`, which lowers itself to background
//! priority): one run at a time, retries with backoff, a startup run when the graph is missing or stale, and a
//! reindex at most every 10 minutes after the watcher saw changes (30 on battery, 60 in Low Power Mode).

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
        self.due_scaled(now, 1)
    }
    /// Due with the interval stretched `factor` times (see `power::factor`).
    pub fn due_scaled(&self, now: Instant, factor: u32) -> bool {
        self.dirty && now.duration_since(self.last_start) >= self.min_interval * factor
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

    /// True when changes were seen, nothing runs, and the last run started at least 10 minutes ago (times the
    /// power factor; pmset is asked only once the plain 10 minutes are over).
    pub fn due(&self) -> bool {
        let now = Instant::now();
        let base = {
            let s = self.state.lock().unwrap();
            !s.running && s.policy.due(now)
        };
        if !base {
            return false;
        }
        let factor = crate::power::factor(crate::power::current());
        let s = self.state.lock().unwrap();
        !s.running && s.policy.due_scaled(now, factor)
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
    fn on_battery_the_interval_stretches() {
        let t0 = Instant::now();
        let mut p = ReindexPolicy::new(MIN_INTERVAL, t0);
        p.mark_dirty();
        assert!(p.due_scaled(t0 + MIN_INTERVAL, 1));
        assert!(!p.due_scaled(t0 + MIN_INTERVAL * 2, 3), "battery: 30 minutes");
        assert!(p.due_scaled(t0 + MIN_INTERVAL * 3, 3));
        assert!(!p.due_scaled(t0 + MIN_INTERVAL * 5, 6), "Low Power Mode: 60 minutes");
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
