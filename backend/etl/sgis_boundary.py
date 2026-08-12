"""
통계청 SGIS(통계지리정보서비스) Open API 클라이언트 — 요구사항정의서 5.1절 확정 GIS 경계 소스.

인증 방식(2026-08 확인): consumer_key/consumer_secret으로 accessToken을 발급받아
행정구역경계(hadmarea) API 호출에 사용한다. 키는 https://sgis.kostat.go.kr 회원가입 후
무료 테스트 키 발급(즉시 발급). 프로젝트 루트 .env에 SGIS_CONSUMER_KEY / SGIS_CONSUMER_SECRET로 저장.
"""
from __future__ import annotations

from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
API_BASE = "https://sgisapi.kostat.go.kr/OpenAPI3"
AUTH_URL = f"{API_BASE}/auth/authentication.json"
BOUNDARY_URL = f"{API_BASE}/boundary/hadmarea.geojson"


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


def get_credentials() -> tuple[str | None, str | None]:
    import os
    env = _load_env_file(PROJECT_ROOT / ".env")
    key = os.environ.get("SGIS_CONSUMER_KEY") or env.get("SGIS_CONSUMER_KEY")
    secret = os.environ.get("SGIS_CONSUMER_SECRET") or env.get("SGIS_CONSUMER_SECRET")
    return key, secret


def get_access_token(consumer_key: str | None = None, consumer_secret: str | None = None,
                      timeout: float = 5.0) -> str | None:
    if not consumer_key or not consumer_secret:
        consumer_key, consumer_secret = get_credentials()
    if not consumer_key or not consumer_secret:
        return None
    try:
        resp = requests.get(AUTH_URL, params={"consumer_key": consumer_key, "consumer_secret": consumer_secret},
                             timeout=timeout)
        resp.raise_for_status()
        return resp.json()["result"]["accessToken"]
    except (requests.RequestException, KeyError, ValueError):
        return None


def fetch_boundary(adm_cd: str, year: str, access_token: str, low_search: int = 0,
                    timeout: float = 10.0) -> dict | None:
    """행정구역경계 GeoJSON 조회. adm_cd에 2자리(시도) 코드를 넣으면 하위 시군구 경계 목록을 반환한다."""
    try:
        resp = requests.get(
            BOUNDARY_URL,
            params={"accessToken": access_token, "year": year, "adm_cd": adm_cd, "low_search": low_search},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or "features" not in data:
            return None
        return data
    except (requests.RequestException, ValueError):
        return None
