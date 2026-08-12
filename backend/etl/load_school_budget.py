"""
학교 예결산 ETL: 2025년 학교회계/사립학교 교비회계 세출 자료 -> data/processed/school_budget_2025.csv

원본(사용자 제공, 2026-08-12, data/raw/budget_2025/): 초등은 예산세출, 중등은 결산세출 자료만 확보됨
(같은 학교급의 예산/결산이 둘 다 있는 게 아니라 서로 다른 종류라 학교급별로 기준이 다름 — 아래 OUT의
seoutguse_gubun 컬럼에 그대로 남겨 사용자가 인지할 수 있게 함). 사립학교 파일도 함께 조회하지만
연천교육지원청은 전부 공립이라 실제로는 공립 파일에서만 매칭됨(2026-08-12 확인).

기본적 교육활동/선택적 교육활동 컬럼은 학교 단위 연간 총액(원)이며, 학생1인당 값은
app 단에서 2025년 학생수(school_year_stat.csv)로 나눠 계산한다(요구사항: 학생수 자료와 연동).

사용법: python load_school_budget.py
"""
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "budget_2025"
SCHOOL_DIM = PROJECT_ROOT / "data" / "processed" / "school_dim.csv"
OUT = PROJECT_ROOT / "data" / "processed" / "school_budget_2025.csv"

CODE_COL = "정보공시 \n 학교코드"
FILES = [
    "2025년_사립학교 교비회계 예·결산서_예산_세출(초)_전체.xlsx",
    "2025년_학교회계 예·결산서_예산_세출(초)_전체.xlsx",
    "2025년_사립학교 교비회계 예·결산서_결산_세출(중)_전체.xlsx",
    "2025년_학교회계 예·결산서_결산_세출(중)_전체.xlsx",
]


def main():
    dim = pd.read_csv(SCHOOL_DIM, dtype={"school_code": str})
    codes = set(dim["school_code"])

    matched = []
    for fn in FILES:
        df = pd.read_excel(RAW_DIR / fn, dtype={CODE_COL: str})
        matched.append(df[df[CODE_COL].isin(codes)])
    combined = pd.concat(matched, ignore_index=True)

    dup = combined[combined.duplicated(CODE_COL, keep=False)]
    if len(dup):
        preview = sorted(dup[CODE_COL].unique())[:20]
        print(f"경고: 학교코드 중복 매칭 {len(dup)}건. 예시: {preview}")

    result = combined.rename(columns={
        CODE_COL: "school_code",
        "기본적 교육활동": "basic_edu_activity_krw",
        "선택적 교육활동": "elective_edu_activity_krw",
        "세입세출구분": "seoutguse_gubun",
    })[["school_code", "basic_edu_activity_krw", "elective_edu_activity_krw", "seoutguse_gubun"]]
    result["fiscal_year"] = 2025

    unmatched = codes - set(result["school_code"])
    if unmatched:
        preview = sorted(unmatched)[:20]
        print(f"경고: 예산자료 매칭 실패 {len(unmatched)}개교. 예시: {preview}")

    result.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"저장: {OUT} ({len(result)}/{len(codes)}개교 매칭)")


if __name__ == "__main__":
    main()
