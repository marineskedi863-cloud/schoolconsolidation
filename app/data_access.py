"""Streamlit 앱 데이터 로딩 헬퍼. DB 없이 data/processed/*.csv를 직접 읽는다(2026-08-11 스택 전환 결정).

2026-08-12 경기도 전역 확대: school_dim/population_grid/admin_boundary는 경기도 전체를 담은
파일 하나로 통합되어 있고, 사용자가 1단계에서 고른 시군구(region, "연천군" 같은 접두어 없는 짧은
이름)로 그때그때 필터링한다. 카카오모빌리티 이동시간만 지역별로 별도 파일(연산 비용이 크기 때문에
선택된 지역만 온디맨드로 계산·캐시, backend/etl/load_travel_time_matrix.py 참조).
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from functools import lru_cache

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED = PROJECT_ROOT / "data" / "processed"

SIDO_NAME = "경기도"
PILOT_REGION = "연천군"  # 기본 선택값(1차 MVP 시범지역) — 1단계에서 다른 시군구로 바꿀 수 있음
SMALL_SCHOOL_THRESHOLD = 60  # 요구사항정의서 3절: 파라미터화, 미확정 지역은 60명 임시 기준 사용
CAPACITY_DEFAULT = 1080      # 확정(7.3절, 2026-08-11)


@lru_cache
def load_school_dim() -> pd.DataFrame:
    return pd.read_csv(PROCESSED / "school_dim.csv", dtype={"school_code": str})


@lru_cache
def load_school_year_stat() -> pd.DataFrame:
    return pd.read_csv(PROCESSED / "school_year_stat.csv", dtype={"school_code": str})


@lru_cache
def _load_population_grid_all() -> pd.DataFrame:
    df = pd.read_csv(PROCESSED / f"population_grid_{SIDO_NAME}.csv")
    df["polygon_lonlat"] = df["polygon_lonlat"].apply(ast.literal_eval)
    return df


def available_regions() -> list[str]:
    """시뮬레이션 가능한 시군구 목록(격자인구 데이터 기준, 31개 — SGIS·격자는 시 단위까지만 있음)."""
    return sorted(_load_population_grid_all()["region_name"].unique())


def load_population_grid(region: str) -> pd.DataFrame:
    """region: '연천군'처럼 경기도 접두어 없는 시군구명."""
    df = _load_population_grid_all()
    return df[df["region_name"] == region]


def schools_in_region(region: str) -> pd.DataFrame:
    """선택 시군구에 속한 학교. school_dim은 대도시 7곳을 일반구 단위로 더 세분화해 담고 있어
    (예: '경기도 고양시 덕양구') 접두어 일치 + 하위 구 포함으로 매칭한다."""
    dim = load_school_dim()
    prefix = f"{SIDO_NAME} {region}"
    mask = (dim["region_name"] == prefix) | dim["region_name"].str.startswith(prefix + " ")
    return dim[mask]


def region_office_name(region: str) -> str | None:
    """선택 시군구의 대표 교육지원청명(화면 표시용). 구 단위로 교육지원청이 갈리는 경우 첫 값 사용."""
    schools = schools_in_region(region)
    if schools.empty:
        return None
    return schools["office_name"].mode().iloc[0]


def load_school_coordinates() -> pd.DataFrame | None:
    """전국초중등학교위치표준데이터(확정 5.2절) 적재 결과. 아직 파일이 없으면 None.

    준비되면 data/raw/school_locations.csv 에 최소 컬럼 [school_code, lat, lon]으로 두면 자동 인식.
    """
    path = PROJECT_ROOT / "data" / "raw" / "school_locations.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, dtype={"school_code": str})
    if not {"school_code", "lat", "lon"}.issubset(df.columns):
        return None
    return df[["school_code", "lat", "lon"]]  # 다른 df와 병합 시 컬럼명 충돌 방지(school_name 등 제외)


@lru_cache
def _load_admin_boundary_all() -> dict | None:
    """SGIS 행정구역경계(확정 5.1절) 경기도 전체. backend/etl/load_admin_boundary.py 산출물."""
    path = PROCESSED / f"admin_boundary_{SIDO_NAME}.geojson"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_admin_boundary(region: str) -> list[dict]:
    """선택 시군구의 경계 폴리곤(들). 대도시 7곳은 SGIS가 일반구 단위로 여러 개를 반환하므로
    리스트로 반환 — 지도에서 각각 그리면 시 전체 경계로 보인다. 없으면 빈 리스트(호출측이 마커로 대체)."""
    data = _load_admin_boundary_all()
    if data is None:
        return []
    prefix = f"{SIDO_NAME} {region}"
    return [f for f in data["features"]
            if f["properties"]["adm_nm"] == prefix or f["properties"]["adm_nm"].startswith(prefix + " ")]


def load_travel_time_matrix(region: str) -> pd.DataFrame | None:
    """카카오모빌리티 실측 이동시간(확정 6.2절). backend/etl/load_travel_time_matrix.py 산출물.

    아직 해당 지역을 계산한 적이 없으면 None — 호출측(5단계)이 온디맨드로 계산하도록 안내한다.
    """
    path = PROCESSED / f"travel_time_matrix_{region}.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, dtype={"school_code": str})
    return df[df["minutes"].notna()][["grid_id", "school_code", "minutes"]]


def load_school_budget() -> pd.DataFrame | None:
    """2025년 학교 예결산 세출(기본적/선택적 교육활동비). backend/etl/load_school_budget.py 산출물.

    현재는 연천군만 확보됨(school_budget_2025.csv). 다른 지역은 아직 없으면 None으로 처리되고
    호출측(3단계)이 해당 지표를 생략한다 — 원본이 사용자 제공 파일이라 자동 확장 불가.
    """
    path = PROCESSED / "school_budget_2025.csv"
    if not path.exists():
        return None
    return pd.read_csv(path, dtype={"school_code": str})


def latest_year_stat(school_code: str) -> pd.Series | None:
    stat = load_school_year_stat()
    rows = stat[stat["school_code"] == school_code].sort_values("year")
    if rows.empty:
        return None
    return rows.iloc[-1]


def year_stat(school_code: str, year: int) -> pd.Series | None:
    stat = load_school_year_stat()
    rows = stat[(stat["school_code"] == school_code) & (stat["year"] == year)]
    if rows.empty:
        return None
    return rows.iloc[0]


def is_small_school(school_code: str, threshold: int = SMALL_SCHOOL_THRESHOLD) -> bool:
    row = latest_year_stat(school_code)
    if row is None or pd.isna(row["student_count"]):
        return False
    return row["student_count"] <= threshold


@lru_cache
def province_averages(school_level: str) -> dict | None:
    """경기도 전체 동일 학교급 학교들의 학급당 학생수·교원1인당 학생수 가중평균(총학생수÷총학급수 등,
    학교별 최신연도 기준). 3단계에서 학교별 지표 옆에 "경기도 평균"으로 병기하는 비교값."""
    dim = load_school_dim()
    stat = load_school_year_stat()
    codes = dim[dim["school_level"] == school_level]["school_code"]
    subset = stat[stat["school_code"].isin(codes)].sort_values("year")
    latest = subset.groupby("school_code").tail(1)
    total_students = latest["student_count"].sum()
    total_classes = latest["class_count"].sum()
    total_teachers = latest["teacher_count"].sum()
    if not total_classes or not total_teachers:
        return None
    return {
        "per_class": total_students / total_classes,
        "per_teacher": total_students / total_teachers,
    }


@lru_cache
def province_budget_averages(school_level: str) -> dict | None:
    """학생1인당 기본적/선택적교육활동비의 가중평균(총 예산÷총 2025년 학생수).

    2026-08-12 기준 school_budget_2025.csv는 연천군 19개교만 확보돼 있어, 이 함수가 반환하는 값은
    엄밀히는 "경기도 평균"이 아니라 "예산자료가 확보된 학교들의 평균"이다(호출측이 그 사실을
    캡션에 명시할 것). 다른 지역 예산자료가 추가되면 이 함수는 코드 변경 없이 자동으로 넓은
    범위를 반영하게 된다.
    """
    budget = load_school_budget()
    if budget is None:
        return None
    dim = load_school_dim()
    codes = dim[dim["school_level"] == school_level]["school_code"]
    b = budget[budget["school_code"].isin(codes)]
    if b.empty:
        return None
    stat = load_school_year_stat()
    y2025 = stat[(stat["school_code"].isin(b["school_code"])) & (stat["year"] == 2025)]
    merged = b.merge(y2025[["school_code", "student_count"]], on="school_code", how="inner")
    total_students = merged["student_count"].sum()
    if not total_students:
        return None
    return {
        "basic_per_student": merged["basic_edu_activity_krw"].sum() / total_students,
        "elective_per_student": merged["elective_edu_activity_krw"].sum() / total_students,
        "school_count": int(len(merged)),
    }


def office_percentile(school_code: str, school_level: str) -> float | None:
    """동일 교육지원청 내 동일 학교급 학교들 대비 학생수 백분위(낮을수록 소규모).

    2026-08-12 수정: school_dim이 경기도 전역으로 확대되면서, 이 함수가 같은 학교급이면
    비교 대상을 지역 구분 없이 경기도 전체로 잡던 버그를 발견해 수정 — 대상 학교와 같은
    교육지원청(office_name) 소속 학교로만 비교하도록 바로잡았다(5.3절 "동일 교육지원청 내" 요건).
    """
    dim = load_school_dim()
    stat = load_school_year_stat()
    latest_year = stat["year"].max()

    own_office = dim.loc[dim["school_code"] == school_code, "office_name"]
    if own_office.empty:
        return None
    office_name = own_office.iloc[0]

    peers = dim[(dim["school_level"] == school_level) & (dim["office_name"] == office_name)]["school_code"]
    peer_stats = stat[(stat["school_code"].isin(peers)) & (stat["year"] == latest_year)]
    if peer_stats.empty or school_code not in peer_stats["school_code"].values:
        return None
    target = peer_stats.loc[peer_stats["school_code"] == school_code, "student_count"].iloc[0]
    return float((peer_stats["student_count"] <= target).mean() * 100)
