"""
비용효과분석(B/C) 간이 지표 계산 — KEDI RR2010-07 "농산어촌 소규모 학교 통폐합 효과 분석"의
구성요소법(ingredient method)을 이 프로젝트가 실제 확보한 데이터 범위 안에서 재현한 버전.

요약보고서(BC분석_지역소멸분석_재현가능성_검토보고서.md, 2026-08-13)에서 정리한 대로,
아래 항목은 우리 데이터로 직접 계산하고, 나머지(통학비용, 폐교자산 활용수익, 학교별
통폐합 지원금)는 데이터가 없어 계산에서 제외하고 명시적으로 플래그만 남긴다.

계산하는 항목
  비용① 통합본교 인건비 증가액 — 전입 학생수를 기존 학급당 학생수로 나눠 필요 학급(교원) 증가분을
        추정하고, 공무원 호봉표(salary_table_2025.csv, "고등이하교원")의 전체 호봉 평균 연봉을 곱함.
  비용② 통합본교 운영비 증가액 — school_expenditure_2025.csv의 "학교 일반운영"(general_operation_krw)을
        전입학생 비율만큼 비례 증가한다고 가정.
  수익① 폐교 인건비 절감액 — 폐교대상 학교(들)의 현재 교원수 × 교원 평균 연봉.
  수익② 폐교 학교회계 세출 절감액 — school_expenditure_2025.csv의 6개 세출 과목 합계
        (인적자원운용·학생복지/교육격차해소·교육활동지원·학교일반운영·학교시설확충·학교재무활동).
        주의: 학교회계 세출에는 정규 교원 정규 급여가 포함되지 않으므로(교육비특별회계에서 별도
        집행) 수익①과 이중계산이 아니다.

계산에서 제외(데이터 없음, 추가수집체크리스트.md 3·4·5번 항목 참조)
  - 통학차량 비용 증가분(운전원 인건비·차량유지비)
  - 폐교자산 활용수익(대부료·매각가 — 활성 학교의 부지·건물 면적 데이터 자체가 없음)
  - 통폐합 재정 인센티브의 학교별 개별 집행액(경기도 연도 합계만 있음 — consolidation_incentive_경기도.csv,
    참고용으로만 별도 출력하고 비율 계산에는 포함하지 않음)

사용법:
  python bc_ratio.py                                   # 데모 시나리오(연천군) 실행
  또는 모듈로 import해서 compute_bc_ratio(closed_school_codes, host_school_code) 직접 호출
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED = PROJECT_ROOT / "data" / "processed"
RAW = PROJECT_ROOT / "data" / "raw"


def _load_inputs():
    school_year_stat = pd.read_csv(PROCESSED / "school_year_stat.csv", encoding="utf-8-sig")
    school_dim = pd.read_csv(PROCESSED / "school_dim.csv", encoding="utf-8-sig")
    salary_table = pd.read_csv(PROCESSED / "salary_table_2025.csv", encoding="utf-8-sig")
    expenditure = pd.read_csv(PROCESSED / "school_expenditure_2025.csv", encoding="utf-8-sig")
    incentive = pd.read_csv(PROCESSED / "consolidation_incentive_경기도.csv", encoding="utf-8-sig")
    return school_year_stat, school_dim, salary_table, expenditure, incentive


def _avg_teacher_annual_salary(salary_table: pd.DataFrame) -> float:
    """'고등이하교원' 전체 호봉 평균 월봉급 x 12. 특정 호봉이 아닌 전 호봉 단순평균이라
    간이추정치임에 유의(실제 재직 교원의 호봉 분포와 다를 수 있음)."""
    t = salary_table[salary_table["rank"] == "고등이하교원"]
    if t.empty:
        raise ValueError("salary_table_2025.csv에서 '고등이하교원' 행을 찾지 못했습니다.")
    return float(t["monthly_salary_krw"].mean()) * 12


def _latest_school_stats(school_year_stat: pd.DataFrame, school_dim: pd.DataFrame) -> pd.DataFrame:
    latest_year = school_year_stat["year"].max()
    latest = school_year_stat[school_year_stat["year"] == latest_year][
        ["school_code", "class_count", "student_count", "teacher_count"]
    ]
    return school_dim.merge(latest, on="school_code", how="inner")


def _expenditure_total(expenditure_row: pd.Series) -> float:
    cols = [
        "hr_operation_krw", "student_welfare_krw", "edu_activity_support_krw",
        "general_operation_krw", "facility_expansion_krw", "financial_activity_krw",
    ]
    return float(expenditure_row[cols].fillna(0).sum())


def compute_bc_ratio(
    closed_school_codes: list[str],
    host_school_code: str,
    incentive_krw: float | None = None,
) -> dict:
    """폐교대상 학교들을 host_school_code로 통합했을 때의 간이 B/C 지표를 계산한다.

    incentive_krw: 이 통합 사례에 실제로 지급될 통폐합 재정 인센티브(있으면). 학교별 개별
    집행액 데이터가 없어 자동으로 채우지 않으므로, 알고 있으면 직접 넘겨준다. None이면
    "지원금 제외" 비율만 계산한다(RR2010-07도 지원금 제외 시 비율이 3.4~7.2배로 더 안정적이라고
    보고했음을 참고).
    """
    school_year_stat, school_dim, salary_table, expenditure, incentive_ref = _load_inputs()
    stats = _latest_school_stats(school_year_stat, school_dim)
    avg_teacher_annual = _avg_teacher_annual_salary(salary_table)

    closed = stats[stats["school_code"].isin(closed_school_codes)]
    host = stats[stats["school_code"] == host_school_code]
    if len(closed) != len(closed_school_codes):
        missing = set(closed_school_codes) - set(closed["school_code"])
        raise ValueError(f"school_year_stat/school_dim에서 찾지 못한 폐교대상 학교: {missing}")
    if host.empty:
        raise ValueError(f"school_year_stat/school_dim에서 찾지 못한 통합본교: {host_school_code}")
    host = host.iloc[0]

    incoming_students = float(closed["student_count"].sum())
    host_students_per_class = host["student_count"] / max(host["class_count"], 1)
    additional_classes = math.ceil(incoming_students / max(host_students_per_class, 1))
    additional_teachers = additional_classes  # 학급당 담임 1인 가정(간이)

    # 비용① 인건비 증가
    c1_hr_increase = additional_teachers * avg_teacher_annual

    # 비용② 운영비 증가(비례 가정)
    host_exp_row = expenditure[expenditure["school_code"] == host_school_code]
    if host_exp_row.empty:
        c2_operation_increase = None
        host_general_ops = None
    else:
        host_general_ops = float(host_exp_row.iloc[0]["general_operation_krw"] or 0)
        growth_ratio = incoming_students / max(host["student_count"], 1)
        c2_operation_increase = host_general_ops * growth_ratio

    # 수익① 폐교 인건비 절감
    b1_hr_savings = float(closed["teacher_count"].sum()) * avg_teacher_annual

    # 수익② 폐교 학교회계 세출 절감
    closed_exp = expenditure[expenditure["school_code"].isin(closed_school_codes)]
    b2_expenditure_savings = float(closed_exp.apply(_expenditure_total, axis=1).sum()) if not closed_exp.empty else None
    matched_closed_exp = set(closed_exp["school_code"]) if not closed_exp.empty else set()
    unmatched_exp = set(closed_school_codes) - matched_closed_exp

    total_cost_excl_incentive = c1_hr_increase + (c2_operation_increase or 0)
    total_benefit = b1_hr_savings + (b2_expenditure_savings or 0)

    result = {
        "closed_schools": closed[["school_code", "school_name", "student_count", "teacher_count"]].to_dict("records"),
        "host_school": {
            "school_code": host["school_code"], "school_name": host["school_name"],
            "student_count_before": host["student_count"], "class_count_before": host["class_count"],
        },
        "avg_teacher_annual_salary_krw": avg_teacher_annual,
        "cost": {
            "c1_host_hr_increase_krw": c1_hr_increase,
            "c1_note": f"전입학생 {incoming_students:.0f}명 / 기존 학급당학생수 {host_students_per_class:.1f}명"
                       f" → 추가학급(교원) {additional_classes}개 가정",
            "c2_host_operation_increase_krw": c2_operation_increase,
            "c3_transport_cost_krw": None,
            "c3_note": "데이터 없음(추가자료_수집_체크리스트.md 3번 항목) — 통학차량 운영실적 중앙공개자료 부재",
            "c4_incentive_krw": incentive_krw,
            "c4_note": "학교별 개별 집행액 데이터 없음 — 사용자가 직접 알고 있는 값을 넘기지 않으면 비율 계산에서 제외",
        },
        "benefit": {
            "b1_closed_hr_savings_krw": b1_hr_savings,
            "b2_closed_expenditure_savings_krw": b2_expenditure_savings,
            "b2_note": f"school_expenditure 매칭 안 된 학교: {unmatched_exp}" if unmatched_exp else "전부 매칭됨",
            "b3_closed_asset_utilization_krw": None,
            "b3_note": "데이터 없음(추가자료_수집_체크리스트.md 4번 항목) — 활성 학교의 부지·건물 면적 데이터 미보유",
        },
        "bc_ratio_excl_incentive": total_benefit / total_cost_excl_incentive if total_cost_excl_incentive else None,
        "bc_ratio_incl_incentive": (
            total_benefit / (total_cost_excl_incentive + incentive_krw)
            if incentive_krw is not None and (total_cost_excl_incentive + incentive_krw) else None
        ),
        "gyeonggi_incentive_reference": incentive_ref.to_dict("records"),
    }
    return result


def _print_result(r: dict) -> None:
    print("=== 폐교대상 ===")
    for s in r["closed_schools"]:
        print(f"  {s['school_name']}({s['school_code']}) 학생 {s['student_count']:.0f}명, 교원 {s['teacher_count']}명")
    h = r["host_school"]
    print(f"=== 통합본교: {h['school_name']}({h['school_code']}) 통합전 학생 {h['student_count_before']:.0f}명 ===")
    print()
    print("--- 비용 ---")
    c = r["cost"]
    print(f"① 인건비 증가: {c['c1_host_hr_increase_krw']:,.0f}원  [{c['c1_note']}]")
    if c["c2_host_operation_increase_krw"] is not None:
        print(f"② 운영비 증가: {c['c2_host_operation_increase_krw']:,.0f}원")
    else:
        print("② 운영비 증가: 산출 불가(host 학교 세출 데이터 없음)")
    print(f"③ 통학비용 증가: 데이터 없음 — {c['c3_note']}")
    print(f"④ 통폐합 지원금: {c['c4_incentive_krw']}  [{c['c4_note']}]")
    print()
    print("--- 수익 ---")
    b = r["benefit"]
    print(f"① 폐교 인건비 절감: {b['b1_closed_hr_savings_krw']:,.0f}원")
    if b["b2_closed_expenditure_savings_krw"] is not None:
        print(f"② 폐교 학교회계 세출 절감: {b['b2_closed_expenditure_savings_krw']:,.0f}원  [{b['b2_note']}]")
    else:
        print("② 폐교 학교회계 세출 절감: 산출 불가")
    print(f"③ 폐교자산 활용수익: 데이터 없음 — {b['b3_note']}")
    print()
    print(f"※ 지원금 제외 B/C 비율(수익/비용): {r['bc_ratio_excl_incentive']:.2f}"
          if r["bc_ratio_excl_incentive"] is not None else "※ 지원금 제외 B/C 비율: 산출 불가")
    if r["bc_ratio_incl_incentive"] is not None:
        print(f"※ 지원금 포함 B/C 비율: {r['bc_ratio_incl_incentive']:.2f}")
    print()
    print("참고: 경기도 연도별 통폐합 지원금 총액(적정규모학교육성지원 세부사업)")
    for row in r["gyeonggi_incentive_reference"]:
        print(f"  {row['fiscal_year']}년: {row['budget_krw']:,.0f}원")
    print()
    print("[유의] 통학비용·폐교자산 활용수익·학교별 인센티브는 데이터가 없어 이 비율에서 빠져 있다.")
    print("      즉 이 수치는 RR2010-07의 완전한 B/C 비율과 직접 비교할 수 없는, 인건비·운영비 중심의 부분 지표다.")


if __name__ == "__main__":
    # 데모 시나리오: 연천군 최소규모 초등학교 2곳(연천노곡초·백학초)을 전곡초등학교로 통합
    # (haversine 최단거리 기준 가장 가까운 대규모교 — 이 폴더의 데모용 예시이며 실제 CPMP
    # 시뮬레이션 결과를 대입하려면 closed_school_codes/host_school_code만 바꾸면 된다)
    demo = compute_bc_ratio(
        closed_school_codes=["S090003428", "S090003424"],  # 연천노곡초, 백학초
        host_school_code="S090003438",  # 전곡초
    )
    _print_result(demo)
