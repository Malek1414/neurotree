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
