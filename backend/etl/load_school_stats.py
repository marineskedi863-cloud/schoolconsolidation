"""
학교 통계 ETL: CSV(2014-2024) + xlsx(2025/2026) -> school / school_year_stat

컬럼 매핑 근거: db/컬럼매핑_CSV_xlsx.md
스키마: db/schema.sql

사용법:
    python load_school_stats.py --office-contains 연천 --dry-run
        -> data/processed/ 에 정제 결과 CSV만 생성(DB 연결 없이 검증용)

    DATABASE_URL 환경변수를 설정하고 --dry-run을 빼면 실제 DB(schema.sql 적용된 PostgreSQL)에 적재한다.
    (SQLAlchemy + psycopg 필요: pip install sqlalchemy psycopg[binary])

1차 MVP는 시범지역(연천교육지원청)만 다루므로 기본 필터는 --office-contains 연천.
전국 확장 시에는 필터를 제거하되, 컬럼매핑 문서의 "학교명 매칭" 주의사항을 재검증할 것.
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW = PROJECT_ROOT
OUT = PROJECT_ROOT / "data" / "processed"

CSV_FILE = RAW / "복사본 (제공) 2014-2024_초중등학교_교육통계자료.csv"
XLSX_FILES = {
    (2025, "초등학교"): RAW / "2025년_학년별·학급별 학생수(초)_전체.xlsx",
    (2025, "중학교"): RAW / "2025년_학년별·학급별 학생수(중)_전체.xlsx",
    (2026, "초등학교"): RAW / "2026년_학년별·학급별 학생수(초)_전체.xlsx",
    (2026, "중학교"): RAW / "2026년_학년별·학급별 학생수(중)_전체.xlsx",
}


def load_xlsx(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path)
    return df.iloc[1:].copy()  # 첫 데이터행은 서브헤더(학급수/학생수/학급당학생수)


def build_school_dim(office_contains: str) -> pd.DataFrame:
    """xlsx(2025년 기준)를 표준으로 학교 차원 테이블 생성."""
    rows = []
    for (year, level), path in XLSX_FILES.items():
        if year != 2025:
            continue
        df = load_xlsx(path)
        if office_contains:
            df = df[df["교육지원청"].astype(str).str.contains(office_contains, na=False)]
        for _, r in df.iterrows():
            rows.append({
                "school_code": r["정보공시 \n 학교코드"],
                "school_name": r["학교명"],
                "school_level": level,
                "establishment": r["설립구분"],
                "office_name": r["교육지원청"],
                "sido_name": r["시도교육청"],
                "region_name": r["지역"],
                "excluded": str(r.get("제외여부", "N")).strip().upper() == "Y",
            })
    dim = pd.DataFrame(rows).drop_duplicates(subset=["school_code"])
    return dim


def build_year_stats_from_xlsx(school_dim: pd.DataFrame, office_contains: str) -> pd.DataFrame:
    rows = []
    for (year, level), path in XLSX_FILES.items():
        df = load_xlsx(path)
        if office_contains:
            df = df[df["교육지원청"].astype(str).str.contains(office_contains, na=False)]
        grade_col_count = 6 if level == "초등학교" else 3
        for _, r in df.iterrows():
            grades = {f"grade{g}_student": None for g in range(1, 7)}
            for g in range(1, grade_col_count + 1):
                col = f"{g}학년.1"  # xN학년 = 학급수, N학년.1 = 학생수
                grades[f"grade{g}_student"] = pd.to_numeric(r.get(col), errors="coerce")
            rows.append({
                "school_code": r["정보공시 \n 학교코드"],
                "year": year,
                "source": "xlsx",
                "class_count": pd.to_numeric(r.get("계"), errors="coerce"),
                "student_count": pd.to_numeric(r.get("계.1"), errors="coerce"),
                "teacher_count": pd.to_numeric(r.get("교사수"), errors="coerce"),
                "special_class_count": pd.to_numeric(r.get("특수학급"), errors="coerce"),
                **grades,
            })
    return pd.DataFrame(rows)


# CSV(2014-2024)의 "시도" 컬럼은 xlsx/office_contains와 표기 체계가 달라 접미사 없는 축약형이다
# (예: "경기도"가 아니라 "경기"). 시도 단위로 필터링할 때만 이 별칭으로 보정한다.
CSV_SIDO_ALIAS = {
    "경기도": "경기", "강원특별자치도": "강원", "충청북도": "충북", "충청남도": "충남",
    "전북특별자치도": "전북", "전라남도": "전남", "경상북도": "경북", "경상남도": "경남",
    "제주특별자치도": "제주", "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구",
    "인천광역시": "인천", "광주광역시": "광주", "대전광역시": "대전", "울산광역시": "울산",
    "세종특별자치시": "세종",
}


def build_year_stats_from_csv(school_dim: pd.DataFrame, office_contains: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """CSV는 학교코드가 없으므로 (학교명, 학교급) -> school_code 매핑을 xlsx 기준으로 역matching.
    반환: (연도별 통계, 매칭 실패 행 목록)
    """
    name_level_to_code = {}
    for _, r in school_dim.iterrows():
        name_level_to_code[(r["school_name"], r["school_level"])] = r["school_code"]

    df = pd.read_csv(CSV_FILE, encoding="cp949", low_memory=False)
    df = df[df["학제명"].isin(["초등학교", "중학교"])]  # 고등학교 제외 (2.3절)
    if office_contains:
        mask = df["교육지원청"].astype(str).str.contains(office_contains, na=False)
        csv_sido = CSV_SIDO_ALIAS.get(office_contains)
        if csv_sido:
            mask = mask | (df["시도"] == csv_sido)
        df = df[mask]

    df["year"] = (df["조사기준일"] // 10000).astype(int)
    df["school_code"] = df.apply(
        lambda r: name_level_to_code.get((r["학교명"], r["학제명"])), axis=1
    )

    unmatched = df[df["school_code"].isna()][
        ["year", "학교명", "학제명", "교육지원청", "학교상태"]
    ].drop_duplicates()

    matched = df.dropna(subset=["school_code"]).copy()
    rows = []
    for _, r in matched.iterrows():
        rows.append({
            "school_code": r["school_code"],
            "year": r["year"],
            "source": "csv",
            "class_count": pd.to_numeric(r.get("학급수_전체"), errors="coerce"),
            "student_count": pd.to_numeric(r.get("학생수 전체"), errors="coerce"),
            "teacher_count": pd.to_numeric(r.get("전체교원수"), errors="coerce"),
            "special_class_count": pd.to_numeric(r.get("특수학급수"), errors="coerce"),
            "grade1_student": pd.to_numeric(r.get("학생수_1학년"), errors="coerce"),
            "grade2_student": pd.to_numeric(r.get("학생수_2학년"), errors="coerce"),
            "grade3_student": pd.to_numeric(r.get("학생수_3학년"), errors="coerce"),
            "grade4_student": pd.to_numeric(r.get("학생수_4학년"), errors="coerce") if r["학제명"] == "초등학교" else None,
            "grade5_student": pd.to_numeric(r.get("학생수_5학년"), errors="coerce") if r["학제명"] == "초등학교" else None,
            "grade6_student": pd.to_numeric(r.get("학생수_6학년"), errors="coerce") if r["학제명"] == "초등학교" else None,
        })
    return pd.DataFrame(rows), unmatched


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--office-contains", default="연천", help="교육지원청명 필터(부분일치). 빈 문자열이면 전국.")
    ap.add_argument("--dry-run", action="store_true", help="DB에 적재하지 않고 data/processed/*.csv 로만 출력")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] 학교 차원 테이블 생성 (교육지원청 필터: '{args.office_contains or '전국'}')")
    school_dim = build_school_dim(args.office_contains)
    print(f"      -> 학교 {len(school_dim)}개 (제외표시 {school_dim['excluded'].sum()}개 포함)")

    print("[2/4] xlsx(2025/2026) 연도별 통계 생성")
    xlsx_stats = build_year_stats_from_xlsx(school_dim, args.office_contains)
    print(f"      -> {len(xlsx_stats)}행")

    print("[3/4] CSV(2014-2024) 연도별 통계 생성 + 학교코드 역매칭")
    csv_stats, unmatched = build_year_stats_from_csv(school_dim, args.office_contains)
    print(f"      -> {len(csv_stats)}행 매칭 성공, 매칭 실패 {len(unmatched)}건(학교명 기준 고유)")
    if len(unmatched):
        print("      매칭 실패 학교명 예시:", unmatched["학교명"].unique()[:10].tolist())

    year_stats = pd.concat([csv_stats, xlsx_stats], ignore_index=True)

    print(f"[4/4] 결과 저장 -> {OUT}")
    school_dim.to_csv(OUT / "school_dim.csv", index=False, encoding="utf-8-sig")
    year_stats.to_csv(OUT / "school_year_stat.csv", index=False, encoding="utf-8-sig")
    unmatched.to_csv(OUT / "school_year_stat_unmatched.csv", index=False, encoding="utf-8-sig")

    if args.dry_run:
        print("\n--dry-run 지정됨: DB 적재는 건너뜀. data/processed/*.csv 결과만 확인하세요.")
        return

    try:
        from sqlalchemy import create_engine
    except ImportError:
        print("\nERROR: DB 적재를 하려면 `pip install sqlalchemy psycopg[binary]` 필요.", file=sys.stderr)
        sys.exit(1)

    import os
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("\nERROR: DATABASE_URL 환경변수가 설정되지 않았습니다 (예: postgresql://user:pw@host/db).", file=sys.stderr)
        sys.exit(1)

    engine = create_engine(db_url)
    with engine.begin() as conn:
        school_dim.rename(columns={"school_name": "school_name"}).to_sql(
            "school_stage", conn, if_exists="replace", index=False
        )
        year_stats.to_sql("school_year_stat_stage", conn, if_exists="replace", index=False)
    print("스테이징 테이블(school_stage, school_year_stat_stage)에 적재 완료. "
          "schema.sql의 school/school_year_stat로 옮기는 UPSERT는 후속 스크립트에서 처리.")


if __name__ == "__main__":
    main()
