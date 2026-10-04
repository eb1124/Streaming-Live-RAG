"""Local .env loading for provider credentials (fake values only; no real key is used or needed)."""

import os
from pathlib import Path

from generation.env import ENV_FILE, load_env
from ingestion.config import PROJECT_ROOT

VAR = "GROQ_API_KEY"


def test_loads_key_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv(VAR, raising=False)
    f = tmp_path / ".env"
    f.write_text(f"{VAR}=test-placeholder-not-a-key\n", encoding="utf-8")
    assert load_env(f) is True
    assert os.environ[VAR] == "test-placeholder-not-a-key"
    monkeypatch.delenv(VAR)


def test_existing_environment_variable_wins(tmp_path, monkeypatch):
    monkeypatch.setenv(VAR, "from-shell")
    f = tmp_path / ".env"
    f.write_text(f"{VAR}=from-file\n", encoding="utf-8")
    load_env(f)
    assert os.environ[VAR] == "from-shell"


def test_missing_file_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.delenv(VAR, raising=False)
    assert load_env(tmp_path / "absent.env") is False
    assert VAR not in os.environ


def test_env_file_location_and_repository_hygiene():
    assert ENV_FILE == PROJECT_ROOT / ".env"
    ignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignore and "!.env.example" in ignore
    example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    assert f"{VAR}=" in example.splitlines()
    assert all(line.split("=", 1)[1] == "" for line in example.splitlines() if line.startswith(f"{VAR}="))


def test_blocked_run_sends_nothing_without_a_key(tmp_path, monkeypatch, capsys):
    import generation.env as env_mod
    from evaluation.generation import run

    monkeypatch.delenv(VAR, raising=False)
    monkeypatch.setattr(env_mod, "ENV_FILE", Path(tmp_path / "absent.env"))
    monkeypatch.setattr(env_mod.load_env, "__defaults__", (Path(tmp_path / "absent.env"),))
    assert run.main([]) == 2
    assert "BLOCKED" in capsys.readouterr().out
