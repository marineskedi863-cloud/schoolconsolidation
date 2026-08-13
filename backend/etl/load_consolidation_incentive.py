"""
적정규모학교육성지원(통폐합 인센티브) 예산 ETL -> data/processed/consolidation_incentive_경기도.csv

출처: 지방교육재정알리미(eduinfo.go.kr) 예산공시 > 재정규모 > 사업별 세출 > 교육행정일반 >
적정규모학교육성지원 > Sheet "시·도 교육청별" > 경기 행(2026-08-13 조사, 의사결정_기록.md 항목40).
별도 원본 파일 없이 조사 당시 확인한 수치를 직접 기록한다 — 학교별 개별 집행액이 아니라
경기도 전체 합계이며, 통학차량 운영 지원처럼 이 표준 세부사업에 없는 항목은 포함하지 않는다.

2026년은 당초예산 기준(최종예산 미확정, 회계연도 진행 중).

사용법: python load_consolidation_incentive.py
"""
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUT = PROJECT_ROOT / "data" / "processed" / "consolidation_incentive_경기도.csv"

DATA = [
    {"fiscal_year": 2022, "budget_krw": 12_486_733_000, "budget_basis": "최종"},
    {"fiscal_year": 2023, "budget_krw": 4_488_372_000, "budget_basis": "최종"},
    {"fiscal_year": 2024, "budget_krw": 3_052_226_000, "budget_basis": "최종"},
    {"fiscal_year": 2025, "budget_krw": 2_646_060_000, "budget_basis": "최종"},
    {"fiscal_year": 2026, "budget_krw": 838_498_000, "budget_basis": "당초(미확정)"},
]


def main():
    df = pd.DataFrame(DATA)
    df["program_name"] = "적정규모학교육성지원"
    df["region"] = "경기도"
    df.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"저장: {OUT} ({len(df)}개 연도)")


if __name__ == "__main__":
    main()
