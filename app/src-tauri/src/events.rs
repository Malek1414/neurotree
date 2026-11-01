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
