from pathlib import Path

from neurotree.kinds import classify, noise_factor
from neurotree.privacy import is_excluded, is_secret_name

HOME = Path.home()


def test_secret_names():
    for name in [".env", ".env.local", "server.pem", "id_rsa", "id_ed25519.pub", "aws-credentials.json",
                 "secret-plan.md", "talk.key", ".npmrc"]:
        assert is_secret_name(name), name
    for name in ["core.py", "id_mapping.py", "notes.md", "keynote.pdf"]:
        assert not is_secret_name(name), name


def test_excluded_paths():
    assert is_excluded(HOME / ".ssh" / "config")
    assert is_excluded(HOME / "Library" / "Mobile Documents" / "x.md")
    assert is_excluded("/repo/node_modules/react/index.js")
    assert is_excluded("/repo/.venv/lib/site.py")
    assert is_excluded("/repo/app/.env")
    assert is_excluded("/private/stuff/plan.md", extra=["/private/stuff"])
    assert not is_excluded("/repo/app/main.py")
    assert not is_excluded(HOME / ".claude" / "projects" / "-x" / "s.jsonl")


def test_classify_and_noise():
    assert classify("a/b.py") == "code" and classify("x.TSX") == "code" and classify("pyproject.toml") == "code"
    assert classify("notes.md") == "doc" and classify("slides.pptx") == "doc"
    assert classify("photo.jpg") is None and classify("talk.key") is None
    assert noise_factor("package-lock.json") == 0.1
    assert noise_factor("web/vendor/lib.js") == 0.1
    assert noise_factor("dist2/app.min.js") == 0.1
    assert noise_factor("tests/test_core.py") == 0.5
    assert noise_factor("src/app.test.ts") == 0.5
    assert noise_factor("pkg/core.py") == 1.0


def test_secret_folders_are_excluded_too():
    assert is_excluded("/repo/references/secrets-store/README.md")
    assert is_excluded("/repo/aws-credentials/notes.md")
    assert not is_excluded("/repo/references/store/README.md")
