"""
폐교재산 활용현황 ETL: 전국폐교재산기본정보표준데이터(data.go.kr) -> data/processed/closed_school_assets_경기도.csv

원본(2026-08-13, data/raw/closed_school_assets/): 전국 1,194건 표준데이터. 대부료·매각가 등
금액 정보는 없음(항목·건물연면적·대지면적·활용현황 구분만) — 금액 추정은 VWorld 개별공시지가
API 연동으로 별도 검토 중(의사결정_기록.md 항목39).

시뮬레이터 범위(경기도)만 필터링해 저장한다.

사용법: python load_closed_school_assets.py
"""
import json
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW = PROJECT_ROOT / "data" / "raw" / "closed_school_assets" / "전국폐교재산기본정보표준데이터_20260807.json"
OUT = PROJECT_ROOT / "data" / "processed" / "closed_school_assets_경기도.csv"

COLS = {
    "CLS_CO_NM": "school_name",
    "CLS_CO_YR": "closed_year",
    "SCHL_RK_SE_NM": "school_level",
    "SGG_NM": "sigungu_name",
    "PRCUSE_STUS_SE_NM": "utilization_status",
    "BLDG_TOTAREA": "building_area_sqm",
    "SITE": "site_area_sqm",
    "RDNMADR": "road_address",
    "LNMADR": "lot_address",
    "ED_NM": "office_name",
    "CRTR_YMD": "data_asof",
}


def main():
    with open(RAW, encoding="utf-8") as f:
        data = json.load(f)
    df = pd.DataFrame(data)

    gg = df[df["CTPV_NM"] == "경기도"].copy()
    result = gg.rename(columns=COLS)[list(COLS.values())]
    for col in ["building_area_sqm", "site_area_sqm"]:
        result[col] = pd.to_numeric(result[col], errors="coerce")

    result.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"저장: {OUT} (경기도 {len(result)}건 / 전국 {len(df)}건)")
    print(result["utilization_status"].value_counts())


if __name__ == "__main__":
    main()
