"""Load local secrets (e.g. GROQ_API_KEY) from <project root>/.env into the process environment.

* The .env file is git-ignored; .env.example lists the variable names with empty values.
* Variables already set in the environment always win (override=False), so a key exported in the shell
  or set by CI is never replaced by the file.
* Only the project's own .env is read (no search up the directory tree).
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from ingestion.config import PROJECT_ROOT

ENV_FILE = PROJECT_ROOT / ".env"


def load_env(path: Path = ENV_FILE) -> bool:
    """Load `path` if it exists. Returns True if the file was found and read."""
    return load_dotenv(path, override=False) if path.is_file() else False
