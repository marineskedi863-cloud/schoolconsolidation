"""
이동시간 매트릭스 ETL: 카카오모빌리티 길찾기 API -> data/processed/travel_time_matrix_<region>.csv

지정 지역 500m 격자(상주인구 > 0) x 동일 학교급 학교 전 쌍의 실제 도로망 이동시간(분)을 조회해 저장한다.
CPMP 시뮬레이션(backend/algo/cpmp.py)이 직선거리 근사(approx_minutes) 대신 이 매트릭스를 우선 사용한다.

2026-08-12 경기도 확장: population_grid_경기도.csv/school_dim.csv(경기도 전역 통합 파일)에서
지정 지역만 필터링하는 방식으로 변경(기존에는 region별 파일이 따로 있었음). 앱(streamlit_app.py)의
5단계 "지금 계산하기" 버튼은 이 스크립트와 별개로 kakao_travel_time.compute_matrix()를 직접 호출한다
(재실행 스킵 로직 없이 1회성으로 계산 — 이 배치 스크립트는 사전에 여러 지역을 미리 준비해둘 때 사용).

재실행 시 이미 저장된 (grid_id, school_code) 쌍은 건너뛰어 API 호출을 아낀다.
사용법: python load_travel_time_matrix.py --region 연천군
"""
import argparse
import time
from pathlib import Path

import pandas as pd

from kakao_travel_time import get_api_key, kakao_minutes

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED = PROJECT_ROOT / "data" / "processed"
RAW = PROJECT_ROOT / "data" / "raw"
SIDO_NAME = "경기도"
SLEEP_SEC = 0.15


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="연천군")
    args = ap.parse_args()
    region = args.region
    out_path = PROCESSED / f"travel_time_matrix_{region}.csv"

    api_key = get_api_key()
    if not api_key:
        print("KAKAO_REST_API_KEY가 없습니다 (.env 파일 확인). 중단합니다.")
        return

    grid = pd.read_csv(PROCESSED / f"population_grid_{SIDO_NAME}.csv")
    grid = grid[(grid["region_name"] == region) & (grid["student_pop"] > 0)]
    if grid.empty:
        print(f"'{region}' 격자를 찾지 못했습니다 — population_grid_{SIDO_NAME}.csv의 region_name 값을 확인하세요.")
        return

    dim = pd.read_csv(PROCESSED / "school_dim.csv", dtype={"school_code": str})
    prefix = f"{SIDO_NAME} {region}"
    dim = dim[(dim["region_name"] == prefix) | dim["region_name"].str.startswith(prefix + " ")]
    coords = pd.read_csv(RAW / "school_locations.csv", dtype={"school_code": str})
    schools = dim.merge(coords, on="school_code", how="inner")
    if schools.empty:
        print(f"'{region}'에 좌표가 있는 학교가 없습니다. 중단합니다.")
        return

    pairs = []
    for level in ["초등학교", "중학교"]:
        g = grid[grid["school_level"] == level]
        s = schools[schools["school_level"] == level]
        for gi in g.itertuples():
            for si in s.itertuples():
                pairs.append((gi.grid_id, gi.centroid_lat, gi.centroid_lon,
                               si.school_code, si.lat, si.lon))

    done = {}
    if out_path.exists():
        prev = pd.read_csv(out_path, dtype={"school_code": str})
        done = {(r.grid_id, r.school_code): r for r in prev.itertuples()}
        print(f"기존 결과 {len(done)}건 발견 — 이미 조회된 쌍은 건너뜁니다.")

    rows = list(done.values())
    todo = [p for p in pairs if (p[0], p[3]) not in done]
    print(f"[{region}] 전체 {len(pairs)}쌍, 신규 조회 대상 {len(todo)}쌍")

    ok, fail = 0, 0
    for idx, (grid_id, glat, glon, school_code, slat, slon) in enumerate(todo, 1):
        minutes = kakao_minutes(glat, glon, slat, slon, api_key=api_key)
        source = "kakao_mobility" if minutes is not None else "failed"
        rows.append({"grid_id": grid_id, "school_code": school_code, "minutes": minutes, "source": source})
        ok += minutes is not None
        fail += minutes is None
        if idx % 50 == 0 or idx == len(todo):
            print(f"  {idx}/{len(todo)} 처리 (성공 {ok}, 실패 {fail})")
        time.sleep(SLEEP_SEC)

    out_df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["grid_id", "school_code", "minutes", "source"])
    out_df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"저장: {out_path} (총 {len(out_df)}행, 실패 {fail}건은 source=failed로 표시. "
          f"CPMP는 이런 쌍을 만나면 haversine 근사치로 자동 대체)")


if __name__ == "__main__":
    main()
