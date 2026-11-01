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
