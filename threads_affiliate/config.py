"""設定ファイルと環境変数の読み込み."""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """ローカル実行用に .env を読む（GitHub Actions では Secrets を使う）。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def load_settings(path: Path | None = None) -> dict:
    path = path or ROOT / "settings.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def env(name: str, required: bool = True) -> str:
    value = os.environ.get(name, "")
    if required and not value:
        raise SystemExit(f"環境変数 {name} が設定されていません（.env.example を参照）")
    return value


def tz(settings: dict) -> ZoneInfo:
    return ZoneInfo(settings.get("posting", {}).get("timezone", "Asia/Tokyo"))
