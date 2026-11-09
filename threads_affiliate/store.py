"""data/ 以下のYAMLファイルの読み書き（GitHub上で手編集しやすいようYAMLにしている）."""
from __future__ import annotations

from pathlib import Path

import yaml

from .config import DATA_DIR

PRODUCTS = DATA_DIR / "products.yaml"
QUEUE = DATA_DIR / "queue.yaml"
POSTED = DATA_DIR / "posted.yaml"


def load(path: Path, default):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return default if data is None else data


def save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, width=1000)
