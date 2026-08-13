"""
소규모학교 통폐합 대상교 선정 시뮬레이터 — 1차 MVP (Streamlit)

2026-08-11 결정: 최종보고회까지는 DB 없이 Streamlit 단일 앱으로 구현(의사결정_기록.md 참조).
승인 시 React+FastAPI+PostgreSQL/PostGIS로 전환 후 Lovable 클라우드와 연계 예정.

실행: streamlit run app/streamlit_app.py
"""
import os
import sys
import time
from pathlib import Path

import streamlit as st
import pandas as pd
import altair as alt
import folium
from streamlit_folium import st_folium
from folium.plugins import HeatMap

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from algo.cpmp import (  # noqa: E402
    solve_cpmp, baseline_nearest_assignment, p_sweep_travel_minutes,
    DemandPoint, School, expected_teacher_count,
)
from etl.kakao_travel_time import compute_matrix  # noqa: E402

import data_access as da  # noqa: E402

st.set_page_config(page_title="소규모학교 통폐합 시뮬레이터", page_icon="🏫", layout="wide")

# 6단계 학구 경계 시각화용 — 존치 학교별로 구분되는 색상(최대 13개교, 초등 최대 p와 동일)
CATCHMENT_PALETTE = [
    "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
    "#46f0f0", "#f032e6", "#9a6324", "#008080", "#e6beff",
    "#800000", "#808000", "#000075",
]

STEPS = [
    "로그인",
    "1. 기초자치단체 선정",
    "2. 지역 학교 현황",
    "3. 학교현황 기본통계",
    "4. 학생거주정보 선택",
    "5. 시뮬레이션 실행",
    "6. 결과 생성",
]

# ---------------------------------------------------------------- session state
ss = st.session_state
ss.setdefault("logged_in", False)
ss.setdefault("step", 0)
ss.setdefault("selected_region", da.PILOT_REGION)
ss.setdefault("selected_school_codes", [])
ss.setdefault("grid_level", "초등학교")
ss.setdefault("sim_result", None)
ss.setdefault("sim_baseline", None)
ss.setdefault("sim_params", {})


def goto(step: int):
    ss.step = max(0, min(len(STEPS) - 1, step))


def add_region_boundary(m: folium.Map, region: str) -> None:
    """선택 시군구 경계를 굵은 선(굵기 4, 검정, 채우기 없음)으로 지도에 얹는다 — 여러 단계 지도에서 공통 사용."""
    for feat in da.load_admin_boundary(region):
        folium.GeoJson(
            feat,
            tooltip=folium.GeoJsonTooltip(fields=["adm_nm"], aliases=["행정구역"]),
            style_function=lambda _: {"color": "#000000", "weight": 4, "fill": False},
        ).add_to(m)


# ---------------------------------------------------------------- login
def render_login():
    st.title("🏫 소규모학교 통폐합 대상교 선정 시뮬레이터")
    st.caption("1차 MVP · 대상 범위: 경기도 (1단계에서 시·군 선택)")
    col = st.columns([1, 1, 1])[1]
    with col:
        with st.form("login"):
            username = st.text_input("ID", value="")
            password = st.text_input("PASSWORD", type="password")
            submitted = st.form_submit_button("로그인", use_container_width=True)
        st.caption("단일 공유계정 · 추후 역할기반 권한(RBAC) 확장 예정 (요구사항정의서 4.1절)")
        if submitted:
            valid_id = os.environ.get("APP_LOGIN_ID", "kedi")
            valid_pw = os.environ.get("APP_LOGIN_PASSWORD", "[REDACTED]")
            if username == valid_id and password == valid_pw:
                ss.logged_in = True
                ss.step = 1
                st.rerun()
            else:
                st.error("ID 또는 비밀번호가 올바르지 않습니다.")


# ---------------------------------------------------------------- step 1
def render_step1():
    st.header("1단계 · 기초자치단체 선정")

    regions = da.available_regions()
    default_idx = regions.index(ss.selected_region) if ss.selected_region in regions else regions.index(da.PILOT_REGION)
    chosen = st.selectbox("분석 대상 시·군 선택 (경기도)", regions, index=default_idx)
    if chosen != ss.selected_region:
        # 지역을 바꾸면 이전 지역 기준으로 골라둔 학교·시뮬레이션 결과는 더 이상 유효하지 않다
        ss.selected_region = chosen
        ss.selected_school_codes = []
        ss.sim_result = None
        ss.sim_params = {}

    grid = da.load_population_grid(ss.selected_region)
    center_lat, center_lon = grid["centroid_lat"].mean(), grid["centroid_lon"].mean()
    boundary_features = da.load_admin_boundary(ss.selected_region)
    office_name = da.region_office_name(ss.selected_region)

    m = folium.Map(location=[center_lat, center_lon], zoom_start=11)
    if boundary_features:
        st.success("✅ 시군구 경계는 통계청 SGIS Open API 실측 폴리곤을 사용합니다 (확정 5.1절).")
        for feat in boundary_features:
            folium.GeoJson(
                feat,
                tooltip=folium.GeoJsonTooltip(fields=["adm_nm"], aliases=["행정구역"]),
                style_function=lambda _: {"color": "#2a78d6", "weight": 2, "fillOpacity": 0.15},
            ).add_to(m)
    else:
        st.info(f"🔶 '{ss.selected_region}' 경계 폴리곤을 찾지 못해 지도는 중심점 마커로만 표시합니다.")
        folium.Marker(
            [center_lat, center_lon],
            tooltip=f"{ss.selected_region} → {office_name or '?'}",
            icon=folium.Icon(color="blue"),
        ).add_to(m)
    st_folium(m, height=420, use_container_width=True, returned_objects=[])

    office_label = f" → **{office_name}**" if office_name else ""
    st.success(f"선택된 지역: **경기도 {ss.selected_region}**{office_label}")
    if st.button("다음 단계 →", type="primary"):
        goto(2)
        st.rerun()


# ---------------------------------------------------------------- step 2
def render_step2():
    st.header("2단계 · 지역 학교 현황")
    st.caption("이 지역의 학교 현황을 확인하는 화면입니다. 시뮬레이션은 항상 초등학교·중학교 중 하나만 대상으로 하므로"
               "(4단계에서 재확인·변경 가능), 학교급·설립구분도 하나씩만 골라 확인합니다.")
    dim = da.schools_in_region(ss.selected_region)

    c1, c2 = st.columns(2)
    with c1:
        ss.grid_level = st.radio("학교급", ["초등학교", "중학교"], horizontal=True,
                                  index=0 if ss.grid_level == "초등학교" else 1)
        level = ss.grid_level
    with c2:
        estab_options = sorted(dim["establishment"].unique())
        estab = st.radio("설립구분", estab_options, horizontal=True)

    view = dim[(dim["school_level"] == level) & (dim["establishment"] == estab)].copy()
    def _latest_student_count(code: str):
        row = da.latest_year_stat(code)
        return row["student_count"] if row is not None else None

    view["소규모"] = view["school_code"].apply(da.is_small_school)
    view["학생수(최신)"] = view["school_code"].apply(_latest_student_count)
    view_display = view[["school_name", "school_level", "establishment", "학생수(최신)", "소규모"]].rename(
        columns={"school_name": "학교명", "school_level": "학교급", "establishment": "설립"}
    )
    st.dataframe(view_display, use_container_width=True, hide_index=True)
    st.caption(f"소규모학교 배지 기준: 최신연도 학생수 {da.SMALL_SCHOOL_THRESHOLD}명 이하 "
               "(교육지원청별 파라미터, 요구사항정의서 3절 — 임시값, 확정 필요)")

    coords = da.load_school_coordinates()
    if coords is not None:
        merged = view.merge(coords, on="school_code", how="left")
        m = folium.Map(location=[merged["lat"].mean(), merged["lon"].mean()], zoom_start=11)
        add_region_boundary(m, ss.selected_region)
        for _, r in merged.dropna(subset=["lat", "lon"]).iterrows():
            color = "red" if r["소규모"] else "blue"
            folium.CircleMarker(
                [r["lat"], r["lon"]], radius=6, color=color, fill=True, fill_opacity=0.8,
                tooltip=f"{r['school_name']} ({'소규모' if r['소규모'] else '일반'})",
            ).add_to(m)
        st_folium(m, height=420, use_container_width=True, returned_objects=[])
        st.caption("🔴 소규모학교 · 🔵 일반학교")
    else:
        st.warning(
            "🔶 학교 좌표(위경도) 데이터가 아직 없어 지도에 마커를 표시할 수 없습니다. "
            "확정 소스는 공공데이터포털 \"전국초중등학교위치표준데이터\"(5.2절) — "
            "`data/raw/school_locations.csv`(school_code, lat, lon 컬럼)로 추가되면 자동 반영됩니다."
        )

    ss.selected_school_codes = view["school_code"].tolist()

    b1, b2 = st.columns(2)
    with b1:
        if st.button("← 이전 단계"):
            goto(1); st.rerun()
    with b2:
        if st.button("다음 단계 →", type="primary", disabled=view.empty):
            goto(3); st.rerun()


# ---------------------------------------------------------------- step 3
def render_step3():
    st.header("3단계 · 학교현황 기본통계")
    dim = da.load_school_dim().set_index("school_code")
    stat = da.load_school_year_stat()
    budget = da.load_school_budget()
    if budget is not None:
        budget = budget.set_index("school_code")
    office_name = da.region_office_name(ss.selected_region)

    if not ss.selected_school_codes:
        st.warning("이 조건에 해당하는 학교가 없습니다. 2단계에서 학교급·설립구분을 조정해 주세요.")
    for code in ss.selected_school_codes:
        info = dim.loc[code]
        small = da.is_small_school(code)
        pct = da.office_percentile(code, info["school_level"])
        st.subheader(info["school_name"])
        badge = "🔴 소규모학교" if small else "🔵 일반학교"
        pct_label = f" · {office_name} 내 규모 백분위: 하위 {pct:.0f}%" if pct is not None and office_name else ""
        st.markdown(f"**판정: {badge}**" + pct_label)

        series = stat[stat["school_code"] == code].sort_values("year")
        c1, c2 = st.columns([2, 1])
        with c1:
            st.line_chart(series.set_index("year")["student_count"], height=220)
            st.caption("학생수 추이 2014 → 2026 (CSV 2014-2024 + xlsx 2025/2026 통합, 컬럼매핑 문서 참조)")
        with c2:
            latest = series.iloc[-1]
            class_count = latest["class_count"]
            student_count = latest["student_count"]
            teacher_count = latest["teacher_count"]
            per_class = student_count / class_count if pd.notna(student_count) and pd.notna(class_count) and class_count else None
            per_teacher = student_count / teacher_count if pd.notna(student_count) and pd.notna(teacher_count) and teacher_count else None

            prov_avg = da.province_averages(info["school_level"])

            sc1, sc2 = st.columns(2)
            with sc1:
                st.metric("학급수", int(class_count) if pd.notna(class_count) else "-")
                st.metric("학생수", int(student_count) if pd.notna(student_count) else "-")
                st.metric("교사수", int(teacher_count) if pd.notna(teacher_count) else "-")
                st.metric("특수학급", int(latest["special_class_count"]) if pd.notna(latest["special_class_count"]) else "-")
            with sc2:
                st.metric("학급당 학생수", f"{per_class:.1f}명" if per_class is not None else "-")
                if prov_avg is not None:
                    st.caption(f"경기도 {info['school_level']} 평균 {prov_avg['per_class']:.1f}명")
                st.metric("교사1인당 학생수", f"{per_teacher:.1f}명" if per_teacher is not None else "-")
                if prov_avg is not None:
                    st.caption(f"경기도 {info['school_level']} 평균 {prov_avg['per_teacher']:.1f}명")

        if budget is not None and code in budget.index:
            brow = budget.loc[code]
            stat_2025 = da.year_stat(code, 2025)
            student_2025 = stat_2025["student_count"] if stat_2025 is not None else None
            if student_2025 is not None and pd.notna(student_2025) and student_2025 > 0:
                prov_budget_avg = da.province_budget_averages(info["school_level"])
                bc1, bc2 = st.columns(2)
                with bc1:
                    st.metric("학생1인당 기본적교육활동비", f"{brow['basic_edu_activity_krw'] / student_2025:,.0f}원")
                    if prov_budget_avg is not None:
                        st.caption(f"경기도 {info['school_level']} 평균 {prov_budget_avg['basic_per_student']:,.0f}원 "
                                   f"(예산자료 확보 {prov_budget_avg['school_count']}개교 기준)")
                with bc2:
                    st.metric("학생1인당 선택적교육활동비", f"{brow['elective_edu_activity_krw'] / student_2025:,.0f}원")
                    if prov_budget_avg is not None:
                        st.caption(f"경기도 {info['school_level']} 평균 {prov_budget_avg['elective_per_student']:,.0f}원 "
                                   f"(예산자료 확보 {prov_budget_avg['school_count']}개교 기준)")
                st.caption(f"2025년 예결산 세출({brow['seoutguse_gubun']}) ÷ 2025년 학생수 {int(student_2025)}명 기준")
        st.divider()

    b1, b2 = st.columns(2)
    with b1:
        if st.button("← 이전 단계", key="b3prev"):
            goto(2); st.rerun()
    with b2:
        if st.button("다음 단계 →", type="primary", key="b3next"):
            goto(4); st.rerun()


# ---------------------------------------------------------------- step 4
def render_step4():
    st.header("4단계 · 학생거주정보 선택")
    ss.grid_level = st.radio("학교급", ["초등학교", "중학교"], horizontal=True,
                              index=0 if ss.grid_level == "초등학교" else 1)
    st.selectbox("격자 갱신연도", ["2024-10 (국토정보플랫폼)"], disabled=True)
    st.selectbox("통학 이동시간 산정 기준시점", ["평일 · 등교시간대(07:30-08:30)"], disabled=True)

    grid = da.load_population_grid(ss.selected_region)
    grid = grid[(grid["school_level"] == ss.grid_level) & (grid["student_pop"] > 0)]
    st.caption(f"{ss.selected_region} 500m 격자 중 {ss.grid_level} 상주인구 > 0 인 격자 {len(grid)}개, "
               f"합계 {grid['student_pop'].sum():.0f}명 (국토정보플랫폼, 개인정보 비식별 집계값)")

    m = folium.Map(location=[grid["centroid_lat"].mean(), grid["centroid_lon"].mean()], zoom_start=11)
    add_region_boundary(m, ss.selected_region)
    HeatMap(
        list(zip(grid["centroid_lat"], grid["centroid_lon"], grid["student_pop"])),
        radius=18, blur=14,
    ).add_to(m)
    st_folium(m, height=420, use_container_width=True, returned_objects=[])

    b1, b2 = st.columns(2)
    with b1:
        if st.button("← 이전 단계", key="b4prev"):
            goto(3); st.rerun()
    with b2:
        if st.button("다음 단계 →", type="primary", key="b4next"):
            goto(5); st.rerun()


# ---------------------------------------------------------------- step 5
def render_step5():
    st.header("5단계 · 시뮬레이션 실행")
    coords = da.load_school_coordinates()
    if coords is None:
        st.error(
            "🔶 학교 좌표 데이터가 없어 시뮬레이션을 실행할 수 없습니다. "
            "`data/raw/school_locations.csv`(school_code, lat, lon)를 추가한 뒤 다시 시도하세요. "
            "(확정 소스: 공공데이터포털 전국초중등학교위치표준데이터, 5.2절)"
        )
        if st.button("← 이전 단계", key="b5prev-blocked"):
            goto(4); st.rerun()
        return

    dim = da.schools_in_region(ss.selected_region)
    candidates = dim[dim["school_level"] == ss.grid_level]
    max_p = len(candidates)
    if max_p == 0:
        st.error(f"'{ss.selected_region}'에 '{ss.grid_level}' 학교가 없습니다. 4단계에서 학교급을 바꿔 주세요.")
        if st.button("← 이전 단계", key="b5prev-nocand"):
            goto(4); st.rerun()
        return

    c1, c2, c3 = st.columns(3)
    with c1:
        p = st.slider("잔존 학교 수 (p)", min_value=1, max_value=max_p, value=max(1, max_p - 3))
    with c2:
        capacity = st.number_input("학교별 수용 상한 (C̄_j)", value=da.CAPACITY_DEFAULT, step=10)
    with c3:
        time_limit = st.number_input("이동시간 제한(분)", value=30, step=5)

    matrix_df = da.load_travel_time_matrix(ss.selected_region)
    if matrix_df is not None:
        st.caption(f"✅ 이동시간(d_ij)은 **카카오모빌리티 실제 도로망 이동시간**을 사용합니다 "
                   f"({len(matrix_df)}쌍 확보, 요구사항정의서 6.2절 확정 소스). 매트릭스에 없는 쌍만 직선거리 근사로 대체됩니다.")
    else:
        region_grid = da.load_population_grid(ss.selected_region)
        region_grid = region_grid[region_grid["student_pop"] > 0]
        region_schools = da.schools_in_region(ss.selected_region).merge(coords, on="school_code", how="inner")
        pair_count = sum(
            len(region_grid[region_grid["school_level"] == lvl]) * len(region_schools[region_schools["school_level"] == lvl])
            for lvl in region_grid["school_level"].unique()
        )
        est_minutes = pair_count * 0.5 / 60
        st.warning(
            f"⚠ '{ss.selected_region}'은 아직 카카오모빌리티 실측 이동시간이 없어 **직선거리 기반 근사치**를 사용 중입니다 "
            f"(격자×학교 {pair_count}쌍, 지금 계산하면 약 {est_minutes:.1f}분 소요 — 최초 1회만, 이후엔 저장된 값을 재사용합니다)."
        )
        if pair_count > 0 and st.button(f"지금 실제 이동시간 계산하기 ({pair_count}쌍)"):
            progress = st.progress(0.0, text="계산 준비 중...")

            def _on_progress(done, total):
                progress.progress(done / total, text=f"{done}/{total}쌍 계산 중...")

            computed = compute_matrix(region_grid, region_schools, progress_cb=_on_progress)
            out_path = da.PROCESSED / f"travel_time_matrix_{ss.selected_region}.csv"
            computed.to_csv(out_path, index=False, encoding="utf-8-sig")
            ok = (computed["source"] == "kakao_mobility").sum()
            st.success(f"계산 완료: {ok}/{len(computed)}쌍 성공. 저장했습니다.")
            st.rerun()

    grid = da.load_population_grid(ss.selected_region)
    grid = grid[(grid["school_level"] == ss.grid_level) & (grid["student_pop"] > 0)]
    demand_points = [DemandPoint(r.grid_id, r.centroid_lat, r.centroid_lon, r.student_pop)
                      for r in grid.itertuples()]
    merged = candidates.merge(coords, on="school_code", how="inner")
    schools = [School(r.school_code, r.school_name, r.lat, r.lon) for r in merged.itertuples()]
    distance_matrix = None
    if matrix_df is not None:
        distance_matrix = {(r.grid_id, r.school_code): r.minutes for r in matrix_df.itertuples()}

    st.subheader("학교 수(통폐합 비율)에 따른 통학시간 분포")
    sweep_key = f"p_sweep_{ss.selected_region}_{ss.grid_level}_{'kakao' if matrix_df is not None else 'approx'}"
    sweep_pair_count = len(demand_points) * len(schools)
    # 2026-08-12 발견: 학교·격자가 많은 대도시(예: 화성시 107개교 x 격자 ~540개 = 5.8만 쌍)에서
    # p=1..전체 구간을 최대 30번 반복 계산하다 보니 CPMP(MILP) 연산이 수십 분 이상 걸려 5단계
    # 화면 전체가 멈추는 문제 발견 — 자동 계산 대신, 쌍이 많으면 사용자가 직접 버튼을 눌러야
    # 계산하도록 전환(카카오모빌리티 온디맨드 계산과 동일한 패턴, 회귀도 없음).
    SWEEP_AUTO_THRESHOLD = 5000
    if sweep_key not in ss and sweep_pair_count <= SWEEP_AUTO_THRESHOLD:
        with st.spinner("잔존 학교 수(p)별 통학시간 분포 계산 중..."):
            ss[sweep_key] = p_sweep_travel_minutes(demand_points, schools, distance_matrix=distance_matrix)
    elif sweep_key not in ss:
        st.warning(
            f"⚠ '{ss.selected_region}'은 학교·격자 조합이 많아({sweep_pair_count:,}쌍) 사전 탐색 계산에 "
            "시간이 오래 걸릴 수 있습니다(대략 수 분~수십 분). 필요하면 아래 버튼으로 직접 계산해 주세요 "
            "— 시뮬레이션 자체(아래 '시뮬레이션 시작')는 이 계산과 무관하게 바로 실행할 수 있습니다."
        )
        if st.button("사전 탐색 그래프 계산하기"):
            with st.spinner("잔존 학교 수(p)별 통학시간 분포 계산 중... (규모가 커 시간이 걸릴 수 있습니다)"):
                ss[sweep_key] = p_sweep_travel_minutes(demand_points, schools, distance_matrix=distance_matrix)
            st.rerun()
    sweep_rows = ss.get(sweep_key)
    if sweep_rows:
        sweep_df = pd.DataFrame(sweep_rows)
        chart = alt.Chart(sweep_df).mark_boxplot(size=18, color="#4c78a8").encode(
            x=alt.X("consolidation_pct:O", title="통폐합 대상 학교 비율(%) — 오른쪽일수록 잔존 학교 수(p) 적음",
                     axis=alt.Axis(format=".0f", labelFontSize=13, titleFontSize=14, labelAngle=0)),
            y=alt.Y("travel_minutes:Q", title="이동시간(분)",
                     axis=alt.Axis(labelFontSize=13, titleFontSize=14)),
            tooltip=[alt.Tooltip("p:Q", title="잔존 학교 수(p)"),
                     alt.Tooltip("consolidation_pct:Q", title="통폐합 비율(%)", format=".0f"),
                     alt.Tooltip("travel_minutes:Q", title="이동시간(분)", format=".1f")],
        ).properties(height=300)
        st.altair_chart(chart, use_container_width=True)
        st.markdown(
            "※ **그래프 해석**: 가로축은 오른쪽으로 갈수록 통폐합 비율이 높아짐 — 즉 **잔존 학교 수(p)가 더 적게 남는** 시나리오입니다"
            "(가장 왼쪽 0%는 현재처럼 학교를 하나도 닫지 않는 경우, 가장 오른쪽은 p=1까지 줄인 극단적 통합입니다). "
            "세로축은 그 시나리오에서 각 학생 거주지(격자)가 배정된 학교까지 가는 **통학시간(분)의 분포**로, "
            "상자는 25~75%(중앙 절반) 구간, 상자 안 가로선은 중앙값, 위아래 수염은 정상 범위, 동그라미는 이상치(특이하게 먼 지역)입니다. "
            "학교 수를 더 줄일수록 상자가 대체로 위(통학시간 증가)로 이동하는지를 보면 통폐합 강도별 이동 부담 증가를 가늠할 수 있습니다.  \n"
            "이 그래프는 수용상한·이동시간 제한 없이 p만 바꿔 계산한 참고용 탐색 결과이며, "
            "위 파라미터로 실행하는 아래 실제 시뮬레이션(수용상한·이동시간 제한 적용)과 값이 다를 수 있습니다."
        )
    elif sweep_key in ss:
        st.info("학교 수별 통학시간 분포를 계산할 수 없습니다.")

    if st.button("시뮬레이션 시작", type="primary"):
        progress = st.progress(0, text="시뮬레이션 진행중 · 파라미터 확인 중...")
        for pct in range(0, 30, 5):
            progress.progress(pct, text="시뮬레이션 진행중 · 수요지·학교 후보 구성 중...")
            time.sleep(0.05)

        progress.progress(35, text="시뮬레이션 진행중 · CPMP 최적화 연산 중(수요지×학교 조합 탐색)...")
        result = solve_cpmp(demand_points, schools, p=p, capacity_limit=capacity, time_limit_min=time_limit,
                             distance_matrix=distance_matrix)

        for pct in range(40, 85, 5):
            progress.progress(pct, text="시뮬레이션 진행중 · 통합 전(현재 상태) 기준선 계산 중...")
            time.sleep(0.05)
        baseline = baseline_nearest_assignment(demand_points, schools, distance_matrix=distance_matrix)

        progress.progress(100, text="시뮬레이션 완료")
        time.sleep(0.2)
        ss.sim_result = result
        ss.sim_baseline = baseline
        ss.sim_params = {"p": p, "capacity": capacity, "time_limit": time_limit, "level": ss.grid_level,
                          "total_population": grid["student_pop"].sum(), "school_count_before": len(schools)}

        if result.status == "infeasible":
            st.warning(result.message)
        else:
            st.success("시뮬레이션 완료")
            goto(6); st.rerun()

    b1, _ = st.columns(2)
    with b1:
        if st.button("← 이전 단계", key="b5prev"):
            goto(4); st.rerun()


# ---------------------------------------------------------------- step 6
def render_step6():
    st.header("6단계 · 결과 생성")
    result = ss.sim_result
    if result is None or result.status != "solved":
        st.warning("5단계에서 시뮬레이션을 먼저 실행해 주세요.")
        if st.button("← 5단계로", key="b6back"):
            goto(5); st.rerun()
        return

    dim = da.schools_in_region(ss.selected_region).set_index("school_code")
    coords = da.load_school_coordinates()

    # 격자별 배정 결과(학구) — 5.6절 "재배치된 학구 경계 시각화" · "통학시간 분포(평균/최대)"
    grid = da.load_population_grid(ss.selected_region)
    grid = grid[(grid["school_level"] == ss.sim_params.get("level")) & (grid["student_pop"] > 0)].copy()
    grid["assigned_school"] = grid["grid_id"].map(result.assignment)
    grid["travel_minutes"] = grid["grid_id"].map(result.travel_minutes)

    avg_minutes, max_minutes = {}, {}
    for code, g in grid.dropna(subset=["assigned_school"]).groupby("assigned_school"):
        avg_minutes[code] = (g["travel_minutes"] * g["student_pop"]).sum() / g["student_pop"].sum()
        max_minutes[code] = g["travel_minutes"].max()

    color_map = {}
    for code, retained in sorted(result.retained.items()):
        if retained:
            color_map[code] = CATCHMENT_PALETTE[len(color_map) % len(CATCHMENT_PALETTE)]

    rows = []
    unassigned_retained = 0
    for code, retained in result.retained.items():
        name = dim.loc[code, "school_name"] if code in dim.index else code
        load = result.school_load.get(code, 0)
        if retained and load > 0:
            category = "존치(거점학교)"
        elif retained:
            category = "존치(배정 학생 없음)"
            unassigned_retained += 1
        else:
            category = "폐교대상"
        rows.append({
            "학교명": name,
            "구분": category,
            "배정 학생수": int(load),
            "평균 통학시간(분)": round(avg_minutes[code], 1) if code in avg_minutes else "-",
            "최대 통학시간(분)": round(max_minutes[code], 1) if code in max_minutes else "-",
            "예상 교원수": round(expected_teacher_count(load), 1) if retained else "-",
        })
    result_df = pd.DataFrame(rows).sort_values(["구분", "배정 학생수"], ascending=[True, False])
    retained_count = sum(1 for r in result.retained.values() if r)
    closed_count = len(result.retained) - retained_count
    c1, c2 = st.columns(2)
    c1.metric("존치(거점학교)", f"{retained_count}개교")
    c2.metric("폐교대상", f"{closed_count}개교")
    st.dataframe(result_df, use_container_width=True, hide_index=True)
    if unassigned_retained:
        st.caption(
            f"ℹ '존치(배정 학생 없음)' {unassigned_retained}개교는 폐교 대상이 아니라 계속 운영되는 학교입니다. "
            "다만 이번 시뮬레이션에서는 인근 학생 거주지가 다른 학교로 배정돼, 이 학교로 통학하도록 배정된 학생이 없다는 뜻입니다"
            "(모형이 '정확히 p개교를 존치'하도록 강제하는 제약 때문에 나타날 수 있는 결과이며, 실제로 학생이 0명이라는 의미는 아닙니다)."
        )

    st.subheader("통합 전후 비교")
    baseline = ss.sim_baseline
    total_students = sum(result.school_load.values())
    school_count_before = ss.sim_params.get("school_count_before", len(result.retained))
    if baseline is not None and total_students:
        avg_before = baseline.objective_minutes / total_students
        avg_after = result.objective_minutes / total_students
        d1, d2, d3, d4 = st.columns(4)
        with d1:
            st.metric("학교 수", f"{retained_count}개교")
            st.caption(f"통합 전 {school_count_before}개교 → 통합 후 {retained_count}개교")
        with d2:
            st.metric("학교당 평균 학생수", f"{total_students / retained_count:.0f}명" if retained_count else "-")
            st.caption(f"통합 전 {total_students / school_count_before:.0f}명 → "
                       f"통합 후 {total_students / retained_count:.0f}명" if retained_count and school_count_before else "")
        with d3:
            st.metric("총 가중 통학시간(분·명)", f"{result.objective_minutes:,.0f}")
            diff = result.objective_minutes - baseline.objective_minutes
            st.caption(f"통합 전 {baseline.objective_minutes:,.0f} → 통합 후 {result.objective_minutes:,.0f} "
                       f"({'+' if diff >= 0 else ''}{diff:,.0f})")
        with d4:
            st.metric("평균 통학시간(1인당, 분)", f"{avg_after:.1f}")
            st.caption(f"통합 전 {avg_before:.1f}분 → 통합 후 {avg_after:.1f}분 "
                       f"({'+' if avg_after >= avg_before else ''}{avg_after - avg_before:.1f}분)")
        st.caption(
            "※ '통합 전'은 대상 학교급의 현존 학교를 모두 둔 채(수용상한·이동시간 제한 없이) "
            "각 학생 거주지를 가장 가까운 학교로 배정한 기준선입니다 — 통학시간이 통합 후 늘어날 수 있는 것은 "
            "자연스러운 결과이며, 통폐합의 목적은 통학시간 단축이 아니라 학교 규모의 적정화입니다."
        )
    else:
        st.caption("통합 전 기준선을 계산하지 못해 비교를 표시할 수 없습니다.")

    if coords is not None:
        merged = dim.reset_index().merge(coords, on="school_code", how="inner")
        m = folium.Map(location=[merged["lat"].mean(), merged["lon"].mean()], zoom_start=11)
        add_region_boundary(m, ss.selected_region)

        for _, grow in grid.dropna(subset=["assigned_school"]).iterrows():
            color = color_map.get(grow["assigned_school"], "#999999")
            school_name = dim.loc[grow["assigned_school"], "school_name"] if grow["assigned_school"] in dim.index else grow["assigned_school"]
            folium.Polygon(
                [(lat, lon) for lon, lat in grow["polygon_lonlat"]],
                color=color, weight=1, fill=True, fill_color=color, fill_opacity=0.35,
                tooltip=f"{school_name} 학구",
            ).add_to(m)

        # 원 크기 = 배정 학생수(규모) — 존치 학교 중 최댓값을 기준으로 4~24px 사이로 선형 비례
        max_load = max((result.school_load.get(code, 0) for code, r in result.retained.items() if r), default=0)

        def _radius(load: float) -> float:
            if max_load <= 0 or load <= 0:
                return 4.0
            return 4.0 + (load / max_load) * 20.0

        for code, retained in result.retained.items():
            row = merged[merged["school_code"] == code]
            if row.empty:
                continue
            row = row.iloc[0]
            load = result.school_load.get(code, 0)
            marker_color = color_map.get(code, "gray") if retained else "gray"
            folium.CircleMarker(
                [row["lat"], row["lon"]], radius=_radius(load),
                color=marker_color, fill=True, fill_color=marker_color, fill_opacity=0.9,
                tooltip=f"{row['school_name']} — {'존치' if retained else '폐교대상'} (배정 {int(load)}명)",
            ).add_to(m)
        st_folium(m, height=420, use_container_width=True, returned_objects=[])

        lc1, lc2 = st.columns(2)
        with lc1:
            if max_load > 0:
                size_steps = sorted({round(max_load * f / 10) * 10 or 10 for f in (0.25, 0.5, 0.75, 1.0)})
                size_rows = "".join(
                    f'<div style="display:flex;align-items:center;margin:2px 0;">'
                    f'<span style="display:inline-block;width:{_radius(v) * 2:.0f}px;height:{_radius(v) * 2:.0f}px;'
                    f'border-radius:50%;background:#888;flex-shrink:0;"></span>'
                    f'<span style="margin-left:8px;">{v:,}명</span></div>'
                    for v in size_steps
                )
                st.markdown(
                    f'<div style="font-size:0.85em;"><b>학생 수(원 크기)</b><br>{size_rows}</div>',
                    unsafe_allow_html=True,
                )
        with lc2:
            legend_items = "".join(
                f'<div style="display:flex;align-items:center;margin:2px 0;">'
                f'<span style="display:inline-block;width:12px;height:12px;background:{color};'
                f'border-radius:50%;flex-shrink:0;"></span>'
                f'<span style="margin-left:8px;">{dim.loc[code, "school_name"] if code in dim.index else code}</span></div>'
                for code, color in color_map.items()
            )
            st.markdown(
                f'<div style="font-size:0.85em;"><b>배정 학교(색상 = 학구)</b><br>{legend_items}'
                f'<div style="display:flex;align-items:center;margin:2px 0;">'
                f'<span style="display:inline-block;width:12px;height:12px;background:gray;'
                f'border-radius:50%;flex-shrink:0;"></span>'
                f'<span style="margin-left:8px;">폐교대상</span></div></div>',
                unsafe_allow_html=True,
            )

    total_students = sum(result.school_load.values())
    total_population = ss.sim_params.get("total_population")

    st.metric("총 가중 이동시간(분·명, 목적함수값)", f"{result.objective_minutes:,.0f}")
    total_label = f"{total_students:.0f}명" + (f" (전체 {total_population:.0f}명 중)" if total_population is not None else "")
    st.caption(f"p={ss.sim_params.get('p')}, 수용상한={ss.sim_params.get('capacity')}명, "
               f"이동시간제한={ss.sim_params.get('time_limit')}분 · 총 배정학생수={total_label}")

    if st.button("← 5단계로 돌아가 다른 시나리오 비교", key="b6back2"):
        goto(5); st.rerun()


# ---------------------------------------------------------------- main
def main():
    if not ss.logged_in:
        render_login()
        return

    with st.sidebar:
        st.markdown("### 진행 단계")
        for idx, label in enumerate(STEPS[1:], start=1):
            marker = "▶" if ss.step == idx else ("✅" if ss.step > idx else "○")
            st.markdown(f"{marker} {label}")
        st.divider()
        if st.button("로그아웃"):
            ss.logged_in = False
            ss.step = 0
            st.rerun()

    renderers = {1: render_step1, 2: render_step2, 3: render_step3,
                 4: render_step4, 5: render_step5, 6: render_step6}
    renderers[ss.step]()


if __name__ == "__main__":
    main()
