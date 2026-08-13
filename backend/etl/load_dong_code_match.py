"""
법정동-행정동 매칭표 ETL: 국가데이터처 법정동 연계정보 -> data/processed/dong_code_match_경기도.csv

원본(2026-08-13, data/raw/dong_code_match/)은 2013-04-01~2025-04-01 분기별(49개 시점) 전체
스냅샷이 반복 수록된 구조다(의사결정_기록.md 항목38 — 처음엔 엑셀 행수 한도와 우연히 일치해
잘림으로 의심했으나 검증 결과 정상임을 확인). 시뮬레이터는 "현재" 매칭만 필요하므로 가장 최신
시점(개정일자 최댓값) + 경기도만 남긴다.

사용법: python load_dong_code_match.py
"""
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW = PROJECT_ROOT / "data" / "raw" / "dong_code_match" / "법정동_행정동_연계정보_20250602.csv"
OUT = PROJECT_ROOT / "data" / "processed" / "dong_code_match_경기도.csv"


def main():
    df = pd.read_csv(RAW, dtype={"행정구역코드": str, "행정동코드": str, "법정동코드": str})
    latest = df["개정일자"].max()
    result = df[(df["개정일자"] == latest) & (df["시도명"] == "경기도")].copy()
    result = result.rename(columns={
        "시도명": "sido_name", "시군구명": "sigungu_name", "행정동명": "adm_dong_name",
        "법정동명": "leg_dong_name", "행정구역코드": "region_code", "행정동코드": "adm_dong_code",
        "법정동코드": "leg_dong_code", "개정일자": "asof_date",
    }).drop(columns=["연결번호"])

    result.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"저장: {OUT} ({len(result)}행, 기준일자 {latest}, 행정동 {result['adm_dong_code'].nunique()}개)")


if __name__ == "__main__":
    main()
