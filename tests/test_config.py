import json
import stat

from jaba_da_hat import config


def test_roundtrip_and_permissions(tmp_path):
    s = config.Settings(jid="me@example.org", password="secret", theme="dark", local_groups=["X"])
    path = config.save(s, tmp_path / "c" / "config.json")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    loaded = config.load(path)
    assert loaded == s


def test_defaults_when_missing(tmp_path):
    s = config.load(tmp_path / "nope.json")
    assert s.jid == "" and s.theme == "system" and s.download_dir.endswith("Downloads")
    assert not s.has_credentials()


def test_unknown_keys_and_bad_theme_ignored(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"jid": "a@example.org", "bogus": 1, "theme": "purple"}))
    s = config.load(p)
    assert s.jid == "a@example.org" and s.theme == "system"


def test_corrupt_file_gives_defaults(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{not json")
    assert config.load(p) == config.Settings()
