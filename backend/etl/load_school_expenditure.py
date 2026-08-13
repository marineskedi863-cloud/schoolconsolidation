"""
학교 세출 항목(운영비 등) ETL: 2025년 학교회계/사립학교 교비회계 세출 자료 -> data/processed/school_expenditure_2025.csv

원본은 load_school_budget.py와 동일(data/raw/budget_2025/). 그쪽 스크립트는 기본적/선택적
교육활동(교육활동비 지표용)만 추출하는데, 이 스크립트는 나머지 6개 세출 과목(인적자원운용,
학생복지/교육격차해소, 교육활동 지원, 학교 일반운영, 학교시설 확충, 학교 재무활동)을 추출한다.
"학교 일반운영"이 추가자료 체크리스트 2번 항목의 "운영비"에 해당(2026-08-13 검증, 의사결정_기록.md 항목36).

세입(교특회계전입금)은 이 원본에 없음(세출 전용 공시자료) — 별도 자료 확보 시 추가 예정.

사용법: python load_school_expenditure.py
"""
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "budget_2025"
SCHOOL_DIM = PROJECT_ROOT / "data" / "processed" / "school_dim.csv"
OUT = PROJECT_ROOT / "data" / "processed" / "school_expenditure_2025.csv"

CODE_COL = "정보공시 \n 학교코드"
FILES = [
    "2025년_사립학교 교비회계 예·결산서_예산_세출(초)_전체.xlsx",
    "2025년_학교회계 예·결산서_예산_세출(초)_전체.xlsx",
    "2025년_사립학교 교비회계 예·결산서_결산_세출(중)_전체.xlsx",
    "2025년_학교회계 예·결산서_결산_세출(중)_전체.xlsx",
]
CATEGORY_COLS = {
    "인적자원운용": "hr_operation_krw",
    "학생복지 \n /교육격차해소": "student_welfare_krw",
    "교육활동 지원": "edu_activity_support_krw",
    "학교 일반운영": "general_operation_krw",
    "학교시설 확충": "facility_expansion_krw",
    "학교 재무활동": "financial_activity_krw",
}


def main():
    dim = pd.read_csv(SCHOOL_DIM, dtype={"school_code": str})
    codes = set(dim["school_code"])

    matched = []
    for fn in FILES:
        df = pd.read_excel(RAW_DIR / fn, dtype={CODE_COL: str})
        matched.append(df[df[CODE_COL].isin(codes)])
    combined = pd.concat(matched, ignore_index=True)

    result = combined.rename(columns={CODE_COL: "school_code", "세입세출구분": "seoutguse_gubun",
                                       **CATEGORY_COLS})
    result = result[["school_code", "seoutguse_gubun", *CATEGORY_COLS.values()]]
    result["fiscal_year"] = 2025

    unmatched = codes - set(result["school_code"])
    if unmatched:
        preview = sorted(unmatched)[:20]
        print(f"경고: 세출자료 매칭 실패 {len(unmatched)}개교. 예시: {preview}")

    result.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"저장: {OUT} ({len(result)}/{len(codes)}개교 매칭)")


if __name__ == "__main__":
    main()
