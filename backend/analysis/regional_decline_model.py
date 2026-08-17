"""
지역소멸가속화 사건연구모형(event-study) — KEDI CRR2025-45 "학교 통폐합이 지역소멸에
미치는 영향" 방법론을 이 프로젝트가 실제 확보한 데이터 범위 안에서 재현한 버전.

방법론 개요 (CRR2025-45 준용)
----------------------------------------------------------------------
1. 패널: 읍면동(법정동코드) x 연도(2019-2024), 종속변수 = 인구(population).
2. Two-way Fixed Effects: 개체(읍면동) 고정효과 + 시점(연도) 고정효과.
   -> statsmodels/linearmodels이 설치되지 않는 실행환경이므로, 개체·시점
      이중 평균차분(double demeaning, within estimator) + numpy.linalg.lstsq
      OLS로 직접 구현. 표준오차는 읍면동 단위 군집강건표준오차(cluster-robust SE,
      sandwich estimator, 소표본 보정 포함).
3. 처치(Treated) 정의: 폐교재산 대장(closed_school_assets_경기도.csv)에서
   2019~2024년 사이 폐교된 학교의 소재 읍면동 = 처치 읍면동.
   -> 도로명주소는 종종 동/리 명이 아니라 '~로' 도로명이라 파싱이 불가능한 경우가
      많아, 지번주소(옛 주소 체계, 항상 읍면동/리+지번 형태)를 우선 사용해 파싱한다.
      정규식으로 시군구 토큰과 읍/면/동/리 토큰을 분리하고, dong_code_match_경기도.csv의
      (시군구명, 법정동명) 조합에 가장 구체적인(마지막) 토큰부터 매칭을 시도한다.
4. Static TWFE DiD: Treated_post_it (처치 읍면동이고 연도>=폐교연도이면 1) 계수.
5. Event-study: 상대시점(r = 연도 - 폐교연도) 더미. r<=-2는 하나로 묶고(r_le_m2),
   r>=3도 하나로 묶어(r_ge_3) 표본 소진을 방지. 기준시점(base)은 r=-1(omitted).
   CRR2025-45의 r=-2..+3 구조를 그대로 따르되, 패널이 6개년(2019-2024)뿐이라
   경계값은 위와 같이 이진화(binning)한다.

알려진 한계 (투명하게 문서화)
----------------------------------------------------------------------
- 처치 표본이 매우 작다(2019-2024년 폐교 15건 중 주소 파싱 성공 13건, 그 중
  '균형패널'(2019-2024 전 연도 존재)에 속하는 읍면동은 8개뿐). 통계적 검정력이
  낮으므로 이 스크립트의 결과는 "방법론 검증용 데모"로 해석해야 하며, KEDI
  CRR2025-45 수준의 표본(전국 단위, 다수 처치 사례)에 대한 결론으로 일반화할 수 없다.
- business_employment.csv(사업체/종사자, 2010/2015/2020 3개 시점만 존재)는
  공변량으로 포함하지 않는다. 2019-2024 패널에서 개체 고정효과와 함께 쓰면,
  가장 가까운 연도값을 채워넣더라도 사실상 개체별로 시간불변값이 되어 개체
  고정효과에 완전히 흡수(perfectly collinear)되어 추정이 불가능하기 때문이다.
- 부천시 구(區) 개편(2024년 소사구/오정구/원미구 재설치)으로 동일 지역의
  법정동코드가 연도에 따라 달라지는 불연속이 있다. '균형패널'(2019-2024 전
  연도에 코드가 존재하는 읍면동만 사용)로 이 문제를 우회한다 — 예: 부천시
  '구.복사초'(2024년 폐교, 소사구 소사본동)는 개편 후 코드만 존재해 균형패널에서
  자동 제외된다.
- 주소 파싱 실패 2건(부천시 대장동, 남양주시 화도읍 녹촌리)은 각각 (a) 대장동이
  구 개편 이후 소속 구 표기가 누락된 것으로 보이는 사례, (b) 국가 법정동 연계표
  자체에 화도읍 산하 리 목록에 '녹촌리'가 없는(정부 원자료 자체의 누락으로 추정)
  사례로, 강제 매칭 대신 결측 처리하고 그대로 드러낸다.

사용법: python regional_decline_model.py
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED = PROJECT_ROOT / "data" / "processed"

SIDO = "경기도"
PANEL_YEARS = list(range(2019, 2025))  # 2019-2024

# ----------------------------------------------------------------------
# 1. 데이터 로드
# ----------------------------------------------------------------------

def _load_inputs():
    population = pd.read_csv(
        PROCESSED / "population_dong.csv", encoding="utf-8-sig", dtype={"dong_code": str}
    )
    dong_match = pd.read_csv(
        PROCESSED / "dong_code_match_경기도.csv",
        encoding="utf-8-sig",
        dtype={"adm_dong_code": str, "leg_dong_code": str},
    )
    closed = pd.read_csv(PROCESSED / "closed_school_assets_경기도.csv", encoding="utf-8-sig")
    return population, dong_match, closed


# ----------------------------------------------------------------------
# 2. 폐교 주소 -> 읍면동(adm_dong_code) 파싱
# ----------------------------------------------------------------------

_SIGUNGU_RE = re.compile(r"^(\S+?[시군])(\s+(\S+구))?")
_DONG_TOKEN_RE = re.compile(r"^\S+[읍면동리]$")


def _parse_address_to_dong(addr: str, dong_match: pd.DataFrame) -> dict | None:
    """지번/도로명 주소 문자열에서 (시군구, 읍면동)을 찾아 adm_dong_code를 반환.

    지번주소는 항상 '시군구 + 읍면동/리 + 지번' 순서를 따르므로 이 형식을 우선
    가정한다. 도로명주소는 동/리 대신 도로명이 오는 경우가 많아 매칭률이 낮다
    (검증 결과 지번주소 사용 시 101/103, 도로명주소만 사용 시 84/103).
    """
    if not isinstance(addr, str) or not addr.strip():
        return None
    a = re.sub(r"^경기도\s*", "", addr.strip())

    m = _SIGUNGU_RE.match(a)
    if not m:
        return None
    sigungu = m.group(1) + (f" {m.group(3)}" if m.group(3) else "")
    rest = a[m.end():].strip()

    dong_candidates = []
    for tok in rest.split():
        if _DONG_TOKEN_RE.match(tok) and not re.search(r"\d", tok):
            dong_candidates.append(tok)
        else:
            break
    if not dong_candidates:
        return None

    cands = dong_match[dong_match["sigungu_name"] == sigungu]
    if cands.empty:
        # 구가 신설/폐지된 경우 등: 시/군 단위로 넓혀서 재시도
        base = sigungu.split()[0]
        cands = dong_match[dong_match["sigungu_name"].str.startswith(base)]

    # 가장 구체적인(마지막) 토큰부터 매칭 시도 (예: '~면 ~리'에서 '리'가 더 구체적)
    for tok in reversed(dong_candidates):
        hit = cands[cands["leg_dong_name"] == tok]
        if not hit.empty:
            row = hit.iloc[0]
            return {
                "sigungu_name": row["sigungu_name"],
                "matched_token": tok,
                "adm_dong_code": row["adm_dong_code"],
                "adm_dong_name": row["adm_dong_name"],
            }
    return None


def _build_treatment_table(closed: pd.DataFrame, dong_match: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in closed.iterrows():
        addr = r["lot_address"] if isinstance(r["lot_address"], str) and r["lot_address"].strip() else r["road_address"]
        parsed = _parse_address_to_dong(addr, dong_match)
        rows.append({
            "school_name": r["school_name"],
            "closed_year": r["closed_year"],
            "address_used": addr,
            "adm_dong_code": parsed["adm_dong_code"] if parsed else None,
            "adm_dong_name": parsed["adm_dong_name"] if parsed else None,
            "matched": parsed is not None,
        })
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------
# 3. 균형패널 구성 + 처치변수 부착
# ----------------------------------------------------------------------

def _build_panel(population: pd.DataFrame, treatment: pd.DataFrame):
    pop = population[population["sido_name"] == SIDO].copy()
    pop = pop[pop["year"].isin(PANEL_YEARS)]

    counts = pop.groupby("dong_code")["year"].nunique()
    balanced_codes = set(counts[counts == len(PANEL_YEARS)].index)
    pop_balanced = pop[pop["dong_code"].isin(balanced_codes)].copy()

    # 2019-2024 사이 폐교 & 주소매칭 성공 & 균형패널에 속하는 읍면동만 처치사례로 사용
    usable = treatment[
        treatment["matched"]
        & treatment["closed_year"].between(PANEL_YEARS[0], PANEL_YEARS[-1])
        & treatment["adm_dong_code"].isin(balanced_codes)
    ].copy()

    # 같은 읍면동에서 여러 학교가 같은 해에 폐교된 경우 -> 읍면동당 최초 폐교연도 1개로 축약
    treat_year_by_dong = (
        usable.groupby("adm_dong_code")["closed_year"].min().rename("closed_year").reset_index()
    )

    pop_balanced = pop_balanced.merge(
        treat_year_by_dong, left_on="dong_code", right_on="adm_dong_code", how="left"
    )
    pop_balanced["treated"] = pop_balanced["closed_year"].notna()
    pop_balanced["treated_post"] = (
        pop_balanced["treated"] & (pop_balanced["year"] >= pop_balanced["closed_year"])
    ).astype(float)

    # 상대시점 r = year - closed_year, 미처치 읍면동은 전부 0(기준시점 R=-1 대비 효과 없음 가정)
    r = pop_balanced["year"] - pop_balanced["closed_year"]
    pop_balanced["rel_time"] = r

    return pop_balanced, usable, balanced_codes, treat_year_by_dong


def _event_dummies(panel: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """상대시점 더미 D_r 생성. r<=-2는 'r_le_m2', r>=3은 'r_ge_3'로 이진화(binning).
    기준시점 r=-1은 생략(omitted base)."""
    bins = {
        "r_le_m2": lambda r: r <= -2,
        "r_0": lambda r: r == 0,
        "r_1": lambda r: r == 1,
        "r_2": lambda r: r == 2,
        "r_ge_3": lambda r: r >= 3,
    }
    out = panel.copy()
    cols = []
    for name, cond in bins.items():
        out[name] = (out["treated"] & cond(out["rel_time"])).astype(float)
        cols.append(name)
    return out, cols


# ----------------------------------------------------------------------
# 4. Two-way FE (double demeaning) + cluster-robust OLS
# ----------------------------------------------------------------------

def _demean_twoway(df: pd.DataFrame, cols: list[str], entity_col="dong_code", time_col="year"):
    """개체·시점 이중 평균차분(within estimator). 반환값은 차분된 값들의 dict."""
    out = {}
    entity_mean = df.groupby(entity_col)[cols].transform("mean")
    time_mean = df.groupby(time_col)[cols].transform("mean")
    grand_mean = df[cols].mean()
    for c in cols:
        out[c] = df[c] - entity_mean[c] - time_mean[c] + grand_mean[c]
    return pd.DataFrame(out, index=df.index)


def _twfe_regress(panel: pd.DataFrame, y_col: str, x_cols: list[str], entity_col="dong_code", time_col="year"):
    all_cols = [y_col] + x_cols
    demeaned = _demean_twoway(panel, all_cols, entity_col, time_col)

    y = demeaned[y_col].to_numpy()
    X = demeaned[x_cols].to_numpy()

    beta, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta

    n = len(y)
    n_entity = panel[entity_col].nunique()
    n_time = panel[time_col].nunique()
    k = len(x_cols)
    dof = n - k - (n_entity - 1) - (n_time - 1) - 1  # 절편은 이중차분으로 이미 제거됨
    dof = max(dof, 1)

    # cluster-robust (sandwich) SE, 개체(dong_code) 단위 군집
    XtX_inv = np.linalg.pinv(X.T @ X)
    clusters = panel[entity_col].to_numpy()
    meat = np.zeros((k, k))
    for g in np.unique(clusters):
        mask = clusters == g
        Xg = X[mask]
        ug = resid[mask]
        score_g = Xg.T @ ug
        meat += np.outer(score_g, score_g)

    n_g = len(np.unique(clusters))
    correction = (n_g / (n_g - 1)) * ((n - 1) / dof) if n_g > 1 else 1.0
    vcov = correction * XtX_inv @ meat @ XtX_inv
    se = np.sqrt(np.clip(np.diag(vcov), 0, None))

    t_stat = np.divide(beta, se, out=np.full_like(beta, np.nan), where=se > 0)

    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2_within = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    return pd.DataFrame({
        "term": x_cols,
        "coef": beta,
        "se_cluster": se,
        "t": t_stat,
    }), {"n_obs": n, "n_entity": n_entity, "n_time": n_time, "dof": dof, "r2_within": r2_within, "n_clusters": n_g}


# ----------------------------------------------------------------------
# 5. 실행 & 출력
# ----------------------------------------------------------------------

def run():
    population, dong_match, closed = _load_inputs()
    treatment = _build_treatment_table(closed, dong_match)
    panel, usable, balanced_codes, treat_year_by_dong = _build_panel(population, treatment)

    print("=" * 72)
    print("STEP 1. 폐교 주소 -> 읍면동 매칭")
    print("=" * 72)
    n_ok = treatment["matched"].sum()
    print(f"전체 폐교 {len(treatment)}건 중 주소->읍면동 매칭 성공 {n_ok}건")
    for _, r in treatment[~treatment["matched"]].iterrows():
        print(f"  [매칭 실패] {r['school_name']} ({r['closed_year']}) : {r['address_used']}")

    print()
    print("=" * 72)
    print(f"STEP 2. 균형패널(2019-2024 전 연도 존재) 구성 — 경기도 전체 읍면동 중 균형패널 {len(balanced_codes)}개")
    print("=" * 72)
    print(f"2019-2024년 사이 폐교 & 주소매칭 & 균형패널 조건을 모두 만족하는 처치 읍면동: {treat_year_by_dong['adm_dong_code'].nunique()}개")
    dong_name_map = dict(zip(dong_match["adm_dong_code"], dong_match["adm_dong_name"]))
    sigungu_map = dict(zip(dong_match["adm_dong_code"], dong_match["sigungu_name"]))
    for _, r in treat_year_by_dong.iterrows():
        nm = dong_name_map.get(r["adm_dong_code"], "?")
        sg = sigungu_map.get(r["adm_dong_code"], "?")
        print(f"  - {sg} {nm} ({r['adm_dong_code']}) : 폐교연도 {int(r['closed_year'])}")

    n_dropped_unbalanced = usable is not None and (
        treatment["matched"].sum() - treat_year_by_dong["adm_dong_code"].nunique()
    )
    print(f"\n(참고) 주소매칭은 됐지만 균형패널 조건 미충족으로 제외된 폐교 건수 등은 위 표와 STEP1 매칭실패 건을 함께 참고.")

    if treat_year_by_dong.empty:
        print("\n처치 읍면동이 0개라 회귀분석을 수행할 수 없습니다.")
        return

    print()
    print("=" * 72)
    print("STEP 3. Static TWFE DiD:  population_it = a_i + b_t + beta * Treated_post_it + e_it")
    print("=" * 72)
    static_res, static_meta = _twfe_regress(panel, "population", ["treated_post"])
    _print_reg_table(static_res, static_meta)

    print()
    print("=" * 72)
    print("STEP 4. Event-study (상대시점 더미, 기준시점 r=-1 생략)")
    print("=" * 72)
    panel_ev, ev_cols = _event_dummies(panel)
    ev_res, ev_meta = _twfe_regress(panel_ev, "population", ev_cols)
    _print_reg_table(ev_res, ev_meta)

    print()
    print("=" * 72)
    print("한계 및 해석 주의사항")
    print("=" * 72)
    print(f"- 처치 읍면동 수: {treat_year_by_dong['adm_dong_code'].nunique()}개 (표본이 작아 통계적 검정력이 낮음)")
    print("- 이 스크립트는 KEDI CRR2025-45 방법론의 '재현 가능성'을 이 프로젝트 데이터로 검증하기")
    print("  위한 데모이며, 계수의 유의성 여부와 무관하게 전국 수준의 정책적 결론으로 확대해석해서는 안 됨.")
    print("- business_employment.csv는 공변량 시도 시 개체고정효과와 완전공선성이 발생해 모형에서 제외함(위 모듈 docstring 참고).")


def _print_reg_table(res: pd.DataFrame, meta: dict):
    print(f"  n_obs={meta['n_obs']}, n_entity={meta['n_entity']}, n_time={meta['n_time']}, "
          f"dof={meta['dof']}, within R^2={meta['r2_within']:.4f}, clusters={meta['n_clusters']}")
    print(f"  {'term':<12} {'coef':>14} {'cluster-SE':>14} {'t':>8}")
    for _, r in res.iterrows():
        t_str = f"{r['t']:.2f}" if pd.notna(r["t"]) else "  NA"
        print(f"  {r['term']:<12} {r['coef']:>14,.2f} {r['se_cluster']:>14,.2f} {t_str:>8}")


if __name__ == "__main__":
    run()
