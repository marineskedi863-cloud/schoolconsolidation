"""공공데이터포털(data.go.kr) Open API 공용 클라이언트 — 서비스키 로딩만 담당."""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_env_file(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def get_api_key() -> str | None:
    import os
    key = os.environ.get("DATA_GO_KR_SERVICE_KEY")
    if key:
        return key
    return _load_env_file(PROJECT_ROOT / ".env").get("DATA_GO_KR_SERVICE_KEY")
