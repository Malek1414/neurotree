//! Commands the viewer invokes: `load_graph`, `status`, `open_path`, `reveal_path`, `reindex`,
//! `load_areas`, `save_areas`.

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

/// areas.json only lists area names, colors and project ids; anything bigger is not from the viewer.
pub const MAX_AREAS_BYTES: usize = 2_000_000;

pub fn read_areas(dir: &Path) -> Result<String, String> {
    std::fs::read_to_string(dir.join("areas.json")).map_err(|e| format!("no areas.json yet ({e}); run Reindex first"))
}

/// Checks the shape (`{"areas": {slug: {name, color, projects: [id]}}}`) and replaces the file atomically.
pub fn write_areas(dir: &Path, text: &str) -> Result<(), String> {
    if text.len() > MAX_AREAS_BYTES {
        return Err("areas.json too large".into());
    }
    let v: Value = serde_json::from_str(text).map_err(|e| format!("not JSON: {e}"))?;
    let areas = v.get("areas").and_then(Value::as_object).ok_or("missing \"areas\" object")?;
    for (slug, a) in areas {
        let ok = a.get("name").is_some_and(Value::is_string)
            && a.get("color").is_some_and(Value::is_string)
            && a.get("projects").and_then(Value::as_array).is_some_and(|p| p.iter().all(Value::is_string));
        if !ok || slug.is_empty() || slug.contains('/') || slug.contains("..") {
            return Err(format!("bad area {slug:?}"));
        }
    }
    let tmp = dir.join("areas.json.tmp");
    std::fs::write(&tmp, text).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, dir.join("areas.json")).map_err(|e| e.to_string())
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

/// Raw text of `<data dir>/areas.json` (the indexer's proposal, or Malek's reviewed areas).
#[tauri::command]
pub fn load_areas(state: State<'_, AppState>) -> Result<String, String> {
    read_areas(&state.data_dir)
}

/// Saves reviewed areas; the indexer keeps this placement on every later run.
#[tauri::command]
pub fn save_areas(state: State<'_, AppState>, text: String) -> Result<(), String> {
    write_areas(&state.data_dir, &text)
}

/// Starts a full reindex in the background; false if one is already running.
#[tauri::command]
pub fn reindex(app: AppHandle, state: State<'_, AppState>) -> bool {
    state.indexer.start(crate::status_sink(&app))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn areas_are_validated_and_written_atomically() {
        let dir = tempfile::tempdir().unwrap();
        assert!(read_areas(dir.path()).is_err());
        let good = r##"{"reviewed":true,"areas":{"hack":{"name":"Hack","color":"#f5a524","projects":["project:p"]}}}"##;
        write_areas(dir.path(), good).unwrap();
        assert_eq!(read_areas(dir.path()).unwrap(), good);
        assert!(!dir.path().join("areas.json.tmp").exists());
        for bad in [
            "{nope",
            r#"{"x":1}"#,
            r##"{"areas":{"a":{"name":1,"color":"#fff","projects":[]}}}"##,
            r##"{"areas":{"a":{"name":"A","color":"#fff","projects":[1]}}}"##,
            r##"{"areas":{"../x":{"name":"A","color":"#fff","projects":[]}}}"##,
        ] {
            assert!(write_areas(dir.path(), bad).is_err(), "{bad}");
        }
        assert_eq!(read_areas(dir.path()).unwrap(), good, "a rejected write leaves the file alone");
        assert!(write_areas(dir.path(), &" ".repeat(MAX_AREAS_BYTES + 1)).is_err());
    }

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
