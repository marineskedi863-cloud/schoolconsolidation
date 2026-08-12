"""
학교 좌표 ETL: 공공데이터포털 "전국초중등학교위치표준데이터"(한국교육시설안전원) -> data/raw/school_locations.csv

원본 확보 방법: 연천 시범 시점(2026-08-11)에는 data.go.kr 파일데이터를 브라우저 네트워크 탭에서
확인해 호출(school_locations_yeoncheon_raw.json, 21행). 경기도 전역 확장(2026-08-12) 시점부터는
파일 다운로드에 CAPTCHA가 걸려 있어 같은 데이터셋의 Open API로 전환
(load_school_locations_gyeonggi.py -> school_locations_gyeonggi_raw.json, 2,558행).
SCHOOL_ID(API의 schoolId)는 이 데이터셋 고유 ID로, xlsx의 "정보공시 학교코드"와 체계가 달라
학교명+학교급으로 재매칭 필요 — 컬럼매핑 문서 §1과 동일한 이슈.

사용법: python load_school_coordinates.py
"""
import json
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_JSON = PROJECT_ROOT / "data" / "raw" / "school_locations_gyeonggi_raw.json"
SCHOOL_DIM = PROJECT_ROOT / "data" / "processed" / "school_dim.csv"
OUT = PROJECT_ROOT / "data" / "raw" / "school_locations.csv"


def main():
    with open(RAW_JSON, encoding="utf-8") as f:
        raw = json.load(f)
    raw_df = pd.DataFrame(raw)
    raw_df = raw_df[raw_df["level"].isin(["초등학교", "중학교"])]  # 고등학교 제외 (2.3절)

    dim = pd.read_csv(SCHOOL_DIM, dtype={"school_code": str})

    # 학교명+학교급만으로는 동명이교(같은 이름의 학교가 다른 교육지원청에 존재)가 중복 매칭된다
    # (2026-08-12 경기도 확장 시 실제로 13건 발견: 탑동초등학교, 상원초등학교 등) — 교육지원청까지 키에 포함.
    merged = dim.merge(
        raw_df.rename(columns={"name": "school_name", "level": "school_level", "office": "office_name"}),
        on=["school_name", "school_level", "office_name"], how="left",
    )

    dup = merged[merged.duplicated("school_code", keep=False) & merged["lat"].notna()]
    if len(dup):
        print(f"경고: 교육지원청까지 포함해도 여전히 중복 매칭 {dup['school_code'].nunique()}개교 — 수동 확인 필요")

    unmatched = merged[merged["lat"].isna()]
    if len(unmatched):
        names = unmatched["school_name"].tolist()
        preview = ", ".join(names[:20]) + (f" 외 {len(names) - 20}건" if len(names) > 20 else "")
        print(f"경고: 좌표 매칭 실패 {len(unmatched)}건. {preview}")

    result = merged.dropna(subset=["lat", "lon"])[["school_code", "school_name", "lat", "lon"]]
    result.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"저장: {OUT} ({len(result)}/{len(dim)}개교 좌표 매칭)")


if __name__ == "__main__":
    main()
