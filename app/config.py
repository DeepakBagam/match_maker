from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    timezone: str = "Asia/Kolkata"
    host: str = "0.0.0.0"
    port: int = 8000
    lookback_days: int | None = None


def _load_dotenv() -> None:
    root_dir = Path(__file__).resolve().parent.parent
    env_path = root_dir / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            os.environ.setdefault(key, value)


def load_settings() -> Settings:
    _load_dotenv()
    database_url = os.getenv("DATABASE_URL", "").strip()
    lookback_raw = os.getenv("LOOKBACK_DAYS")

    if not database_url:
        root_dir = Path(__file__).resolve().parent.parent
        database_url = f"sqlite:///{(root_dir / 'matchlayer.db').as_posix()}"

    return Settings(
        database_url=database_url,
        timezone=os.getenv("APP_TIMEZONE", "Asia/Kolkata"),
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "8000")),
        lookback_days=int(lookback_raw) if lookback_raw and lookback_raw.strip() else None,
    )


def load_settings_optional() -> Settings | None:
    return load_settings()
