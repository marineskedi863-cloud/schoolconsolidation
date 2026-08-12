"""
CPMP(Capacitated p-median Problem) 솔버 — 요구사항정의서 v1.0 7.2절 수식 그대로 구현.

변수/제약 대응(7.2절):
    i = 수요지 격자, j = 학교, a_i = 수요지 학생수, d_ij = 이동시간
    p = 잔존 학교 수, C_j = 학교별 수용 상한
    Y_j ∈ {0,1} 학교 존치 여부, X_ij ∈ {0,1} 격자 i -> 학교 j 배정 여부

    minimize  sum a_i * d_ij * X_ij
    s.t.      sum_j X_ij = 1                 (제약1)
              X_ij <= Y_j                     (제약2)
              sum_j Y_j = p                   (제약3)
              sum_i a_i*X_ij <= C_j * Y_j      (제약4)

주의: 이동시간(d_ij)은 기본적으로 직선거리(haversine) 기반 근사치를 사용한다.
카카오모빌리티 API(도로망 기준 실제 이동시간)를 연동하기 전까지는 UI에 반드시
"임시 근사치"임을 표기할 것 — 요구사항정의서 6.2절의 확정 데이터 소스가 아직 미연동 상태.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pulp


@dataclass
class DemandPoint:
    id: str
    lat: float
    lon: float
    pop: float  # a_i


@dataclass
class School:
    code: str
    name: str
    lat: float
    lon: float


@dataclass
class CpmpResult:
    status: str  # "solved" | "infeasible"
    retained: dict = field(default_factory=dict)       # school_code -> bool (Y_j)
    assignment: dict = field(default_factory=dict)      # grid_id -> school_code (X_ij=1인 j)
    school_load: dict = field(default_factory=dict)     # school_code -> 배정된 학생수 합
    travel_minutes: dict = field(default_factory=dict)  # grid_id -> 배정된 학교까지의 d_ij(분), 5.6절 통학시간 분포용
    objective_minutes: float | None = None              # 가중 평균 이동시간이 아닌 총 가중이동시간(분·명)
    message: str = ""


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def approx_minutes(lat1, lon1, lat2, lon2, avg_speed_kmh: float = 30.0) -> float:
    """직선거리를 평균 이동속도로 나눈 근사 이동시간(분). 실제 도로망 시간(카카오모빌리티) 대체용 임시값."""
    km = haversine_km(lat1, lon1, lat2, lon2)
    return (km / avg_speed_kmh) * 60.0


def solve_cpmp(
    demand_points: list[DemandPoint],
    schools: list[School],
    p: int,
    capacity_limit: float,
    time_limit_min: float,
    distance_fn=approx_minutes,
    distance_matrix: dict | None = None,
) -> CpmpResult:
    """CPMP MILP를 PuLP(CBC)로 풀어 결과를 반환한다.

    distance_matrix가 주어지면 (demand_point.id, school.code) -> 분 사전계산값을 우선 사용하고
    (카카오모빌리티 실측치, backend/etl/load_travel_time_matrix.py), 매트릭스에 없는 쌍만 distance_fn
    (기본값: haversine 근사)으로 대체한다.
    """
    if p <= 0 or p > len(schools):
        return CpmpResult(status="infeasible", message=f"p={p}는 학교 수(1~{len(schools)}) 범위를 벗어남")

    # d_ij 계산 + 이동시간 제한 초과 쌍은 후보에서 제외 (7.3절)
    d = {}
    for i in demand_points:
        for j in schools:
            dt = distance_matrix.get((i.id, j.code)) if distance_matrix is not None else None
            if dt is None:
                dt = distance_fn(i.lat, i.lon, j.lat, j.lon)
            if dt <= time_limit_min:
                d[(i.id, j.code)] = dt

    # 이동시간 제한 내에 도달 가능한 학교가 하나도 없는 수요지가 있으면 즉시 불능해로 처리한다.
    # (이전에는 해당 격자를 조용히 제외하고 나머지만으로 풀었으나, 사용자에게 결과가 일부 학생만
    # 반영된 값이라는 사실이 드러나지 않는 문제가 있어 2026-08-12 정책 변경: 전체 불능해로 통일)
    unreachable = [i for i in demand_points if not any((i.id, j.code) in d for j in schools)]
    if unreachable:
        unreachable_pop = sum(i.pop for i in unreachable)
        return CpmpResult(
            status="infeasible",
            message=f"목적함수를 찾을 수 없습니다: 이동시간 제한({time_limit_min}분) 내에 도달 가능한 학교가 "
                    f"없는 수요지가 {len(unreachable)}개(학생 {unreachable_pop:.0f}명) 있습니다. "
                    "이동시간 제한을 늘리거나 p·수용상한을 조정한 뒤 다시 시작해 주세요.",
        )

    prob = pulp.LpProblem("CPMP", pulp.LpMinimize)
    Y = {j.code: pulp.LpVariable(f"Y_{j.code}", cat="Binary") for j in schools}
    X = {
        (i.id, j.code): pulp.LpVariable(f"X_{i.id}_{j.code}", cat="Binary")
        for i in demand_points for j in schools if (i.id, j.code) in d
    }

    # 목적함수: sum a_i * d_ij * X_ij
    prob += pulp.lpSum(i.pop * d[(i.id, j.code)] * X[(i.id, j.code)]
                        for i in demand_points for j in schools if (i.id, j.code) in X)

    # 제약1: 각 수요지는 정확히 1개 학교에 배정 (위에서 후보가 없는 격자는 이미 불능해로 걸러졌음)
    for i in demand_points:
        candidates = [X[(i.id, j.code)] for j in schools if (i.id, j.code) in X]
        prob += pulp.lpSum(candidates) == 1

    # 제약2: X_ij <= Y_j
    for (i_id, j_code), var in X.items():
        prob += var <= Y[j_code]

    # 제약3: sum Y_j = p
    prob += pulp.lpSum(Y.values()) == p

    # 제약4: sum_i a_i*X_ij <= C_j * Y_j
    for j in schools:
        prob += pulp.lpSum(
            i.pop * X[(i.id, j.code)] for i in demand_points if (i.id, j.code) in X
        ) <= capacity_limit * Y[j.code]

    solver = pulp.PULP_CBC_CMD(msg=False)
    prob.solve(solver)

    status = pulp.LpStatus[prob.status]
    if status != "Optimal":
        return CpmpResult(status="infeasible",
                           message="수용 상한·이동시간 제한 조건에서 실행 가능한 해가 없습니다. "
                                   "수용 상한을 높이거나 p를 조정해 주세요.")

    result = CpmpResult(status="solved")
    result.retained = {code: (var.value() or 0) > 0.5 for code, var in Y.items()}
    load = {j.code: 0.0 for j in schools}
    assignment = {}
    travel_minutes = {}
    for (i_id, j_code), var in X.items():
        if (var.value() or 0) > 0.5:
            assignment[i_id] = j_code
            travel_minutes[i_id] = d[(i_id, j_code)]
            load[j_code] += next(i.pop for i in demand_points if i.id == i_id)
    result.assignment = assignment
    result.school_load = load
    result.travel_minutes = travel_minutes
    result.objective_minutes = pulp.value(prob.objective)
    return result


def baseline_nearest_assignment(
    demand_points: list[DemandPoint],
    schools: list[School],
    distance_fn=approx_minutes,
    distance_matrix: dict | None = None,
) -> CpmpResult:
    """통합 전(현재) 기준선: 대상 학교급의 현존 학교를 전부 존치한 채, 각 수요지를 최근접 학교로 배정.

    수용상한·이동시간제한 없이 순수 최근접 배정만 계산한다 — solve_cpmp의 결과(통합 후)와
    같은 필드 구조(CpmpResult)로 반환해 6단계 "통합 전후 비교"에서 그대로 나란히 쓸 수 있게 한다.
    """
    assignment, travel_minutes = {}, {}
    load = {j.code: 0.0 for j in schools}
    total = 0.0
    for i in demand_points:
        best_code, best_minutes = None, None
        for j in schools:
            dt = distance_matrix.get((i.id, j.code)) if distance_matrix is not None else None
            if dt is None:
                dt = distance_fn(i.lat, i.lon, j.lat, j.lon)
            if best_minutes is None or dt < best_minutes:
                best_minutes, best_code = dt, j.code
        assignment[i.id] = best_code
        travel_minutes[i.id] = best_minutes
        load[best_code] += i.pop
        total += i.pop * best_minutes
    return CpmpResult(
        status="solved",
        retained={j.code: True for j in schools},
        assignment=assignment,
        school_load=load,
        travel_minutes=travel_minutes,
        objective_minutes=total,
    )


def p_sweep_travel_minutes(
    demand_points: list[DemandPoint],
    schools: list[School],
    distance_matrix: dict | None = None,
    max_samples: int = 30,
) -> list[dict]:
    """잔존 학교 수(p) 1~전체 구간을 훑어 통폐합 비율별 통학시간 분포를 계산한다 — 5단계 사전 탐색용.

    수용상한·이동시간제한을 사실상 무제한으로 두어(순수 지리적 배정) p만 바꿔가며 CPMP를 반복
    실행한다. 사용자가 5단계에서 실제로 고른 수용상한·이동시간제한과는 별개의 탐색 결과이며,
    학교가 많은 지역에서 계산 시간이 과도해지지 않도록 p 값을 최대 max_samples개로 표본추출한다.

    반환: [{"p": int, "consolidation_pct": float, "travel_minutes": float}, ...]
    (수요지 격자 1개당 1행 — 배정된 통학시간 기준, 학생수 가중치는 적용하지 않음)
    """
    total = len(schools)
    if total == 0 or not demand_points:
        return []
    unconstrained_capacity = sum(i.pop for i in demand_points) or 1.0

    p_values = list(range(1, total + 1))
    if len(p_values) > max_samples:
        step = max(1, len(p_values) // max_samples)
        p_values = sorted(set(p_values[::step]) | {p_values[-1]})

    rows = []
    for p in p_values:
        result = solve_cpmp(demand_points, schools, p=p, capacity_limit=unconstrained_capacity,
                             time_limit_min=10_000, distance_matrix=distance_matrix)
        if result.status != "solved":
            continue
        pct = (total - p) / total * 100.0
        for grid_id, minutes in result.travel_minutes.items():
            rows.append({"p": p, "consolidation_pct": pct, "travel_minutes": minutes})
    return rows


def expected_teacher_count(student_count: float) -> float:
    """보조 산출식(7.5절): ŷ = 1.74 · x^0.511 (원 연구 84쪽, R²=0.928)"""
    if student_count <= 0:
        return 0.0
    return 1.74 * (student_count ** 0.511)
