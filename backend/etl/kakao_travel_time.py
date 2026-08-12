"""
카카오모빌리티 길찾기(Directions) API 클라이언트 — 요구사항정의서 6.2절 확정 이동시간 소스.

2026-08-11 사용자가 KAKAO_REST_API_KEY 제공, 실제 길찾기 응답(200) 확인 완료(의사결정_기록.md 참조).
키는 프로젝트 루트 .env 파일(KAKAO_REST_API_KEY=...)에 두고 코드에는 하드코딩하지 않는다.
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DIRECTIONS_URL = "https://apis-navi.kakaomobility.com/v1/directions"


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
    key = os.environ.get("KAKAO_REST_API_KEY")
    if key:
        return key
    return _load_env_file(PROJECT_ROOT / ".env").get("KAKAO_REST_API_KEY")


def kakao_minutes(lat1: float, lon1: float, lat2: float, lon2: float,
                   api_key: str | None = None, timeout: float = 5.0) -> float | None:
    """카카오모빌리티 자동차 길찾기 기준 실제 소요시간(분). 실패 시 None(호출측에서 근사치로 대체)."""
    api_key = api_key or get_api_key()
    if not api_key:
        return None
    try:
        resp = requests.get(
            DIRECTIONS_URL,
            headers={"Authorization": f"KakaoAK {api_key}"},
            params={"origin": f"{lon1},{lat1}", "destination": f"{lon2},{lat2}"},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        route = data["routes"][0]
        if route["result_code"] != 0:
            return None  # 경로 없음(도서지역 등) — result_msg에 사유
        return route["summary"]["duration"] / 60.0
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return None


def compute_matrix(grid_df: pd.DataFrame, schools_df: pd.DataFrame, sleep_sec: float = 0.15,
                    api_key: str | None = None, progress_cb=None) -> pd.DataFrame:
    """격자x학교 이동시간 매트릭스 계산 — 동일 school_level끼리만 짝짓는다.

    grid_df: grid_id, centroid_lat, centroid_lon, school_level 컬럼 필요
    schools_df: school_code, lat, lon, school_level 컬럼 필요
    progress_cb(done, total): 진행 상황 콜백(선택) — 5단계 온디맨드 계산 UI 진행률 표시용
    반환: grid_id, school_code, minutes, source 컬럼의 DataFrame(실패한 쌍은 minutes=None, source="failed")
    """
    api_key = api_key or get_api_key()
    pairs = []
    for level in grid_df["school_level"].unique():
        g = grid_df[grid_df["school_level"] == level]
        s = schools_df[schools_df["school_level"] == level]
        for gi in g.itertuples():
            for si in s.itertuples():
                pairs.append((gi.grid_id, gi.centroid_lat, gi.centroid_lon, si.school_code, si.lat, si.lon))

    rows = []
    for idx, (grid_id, glat, glon, school_code, slat, slon) in enumerate(pairs, 1):
        minutes = kakao_minutes(glat, glon, slat, slon, api_key=api_key)
        rows.append({
            "grid_id": grid_id, "school_code": school_code, "minutes": minutes,
            "source": "kakao_mobility" if minutes is not None else "failed",
        })
        if progress_cb:
            progress_cb(idx, len(pairs))
        time.sleep(sleep_sec)
    return pd.DataFrame(rows, columns=["grid_id", "school_code", "minutes", "source"])
