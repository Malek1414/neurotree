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
