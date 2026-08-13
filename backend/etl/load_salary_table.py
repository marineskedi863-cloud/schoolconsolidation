"""
공무원 호봉별 봉급표 ETL: 인사혁신처 공공데이터포털 자료 -> data/processed/salary_table_2025.csv

원본(2026-08-13, data/raw/salary_table/): 2025년 기준 전체 공무원(일반직/교원/경찰/소방 등)
직종별·호봉별 월 봉급액. B/C 분석에서 교육공무원(교원) 인건비 추정에 사용.

사용법: python load_salary_table.py
"""
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW = PROJECT_ROOT / "data" / "raw" / "salary_table" / "공무원_직급별_급여_20260108.csv"
OUT = PROJECT_ROOT / "data" / "processed" / "salary_table_2025.csv"


def main():
    df = pd.read_csv(RAW)
    df.columns = [c.strip() for c in df.columns]
    result = df.rename(columns={
        "년도": "fiscal_year",
        "구분": "job_category",
        "계급": "rank",
        "호봉": "step",
        "봉급": "monthly_salary_krw",
    })
    result["job_category"] = result["job_category"].str.strip()
    result["rank"] = result["rank"].str.strip()

    result.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"저장: {OUT} ({len(result)}행, 직종 {result['job_category'].nunique()}종)")


if __name__ == "__main__":
    main()
