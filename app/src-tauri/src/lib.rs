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
            commands::reindex,
            commands::load_areas,
            commands::save_areas
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
        assert_eq!(c["build"]["devUrl"], "http://127.0.0.1:5173");
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
