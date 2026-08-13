"""
경기도 읍면동별 사업체수·종사자수 ETL (경제총조사 2010/2015/2020) -> data/processed/business_employment.csv

원본(2026-08-13, data/raw/business_employment/, KOSIS DT_1KI2003/DT_1KI1511/DT_2KI2011)은
지역명이 상위 지역 표시 없이 계층 순서대로만 나열되어 있다(의사결정_기록.md 항목38·42) —
예: "경기도"(합계) -> "고양시"(시 합계, 구 분할 대도시의 경우) -> "덕양구"(구 합계) -> "화전동"(읍면동)
처럼 부모-자식 관계가 오직 행 순서로만 표현된다.

이 스크립트는 다음 방식으로 계층을 복원한다:
1. "경기도" 행부터 다음 시도명이 나오기 전까지를 경기도 블록으로 잘라낸다
2. population_dong.csv의 경기도 sigungu_name 목록(예: "고양시 덕양구")에서 시/구 분할 도시
   7곳(고양·성남·수원·안양·부천·안산·용인)의 시명·구명 집합을 얻는다
3. 블록을 순서대로 훑으며 "시명" 또는 "구명"을 만나면 현재 소속 시군구를 갱신하고,
   그 외의 이름은 읍면동으로 보고 현재 시군구에 귀속시킨다

산업분류는 "전산업"(총계) 행만 사용— 세부 산업별 데이터가 필요해지면 이 필터를 조정할 것.

사용법: python load_business_employment.py
"""
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "business_employment"
POP_DONG = PROJECT_ROOT / "data" / "processed" / "population_dong.csv"
OUT = PROJECT_ROOT / "data" / "processed" / "business_employment.csv"

FILES = {
    2010: "101_DT_1KI2003_20260813132121.csv",
    2015: "101_DT_1KI1511_20260813133936.csv",
    2020: "101_DT_2KI2011_20260813131942.csv",
}
SIDO_LIST = [
    "서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시", "대전광역시", "울산광역시",
    "세종특별자치시", "경기도", "강원도", "강원특별자치도", "충청북도", "충청남도",
    "전라북도", "전북특별자치도", "전라남도", "경상북도", "경상남도", "제주특별자치도",
]


def load_gyeonggi_hierarchy():
    """population_dong.csv에서 경기도 시/구 분할 도시의 시명·구명·평시군명 집합을 얻는다."""
    pop = pd.read_csv(POP_DONG, dtype={"dong_code": str})
    sigungu = pop.loc[pop["sido_name"] == "경기도", "sigungu_name"].unique()
    si_with_gu, gu_to_si, plain_sigun = set(), {}, set()
    for name in sigungu:
        parts = name.split(" ")
        if len(parts) == 2:
            si, gu = parts
            si_with_gu.add(si)
            gu_to_si[gu] = si
        else:
            plain_sigun.add(name)
    return si_with_gu, gu_to_si, plain_sigun


def extract_gyeonggi_block(df: pd.DataFrame, col0: str) -> pd.DataFrame:
    # "경기도" 자체가 항목(사업체수/종사자수)별로 여러 행 반복되므로, 그 반복이 전부 끝난
    # 뒤부터 "다음 시도명"을 찾아야 한다(안 그러면 경기도의 두 번째 행에서 곧바로 멈춤).
    idx_gg_all = df.index[df[col0] == "경기도"]
    idx_first, idx_last = idx_gg_all.min(), idx_gg_all.max()
    rest = df.iloc[idx_last + 1:]
    next_sido = rest.index[rest[col0].isin(SIDO_LIST)]
    end = next_sido[0] if len(next_sido) else len(df)
    return df.iloc[idx_first:end]


def reconstruct_hierarchy(block: pd.DataFrame, col0: str, si_with_gu: set, gu_to_si: dict,
                           plain_sigun: set) -> pd.DataFrame:
    current_sigungu = None
    sigungu_col = []
    is_leaf = []
    for name in block[col0]:
        if name == "경기도" or name in si_with_gu:
            current_sigungu, leaf = current_sigungu if name != "경기도" else None, False
        elif name in gu_to_si:
            current_sigungu, leaf = f"{gu_to_si[name]} {name}", False
        elif name in plain_sigun:
            current_sigungu, leaf = name, False
        else:
            leaf = True
        sigungu_col.append(current_sigungu)
        is_leaf.append(leaf)
    block = block.copy()
    block["sigungu_name"] = sigungu_col
    block["is_leaf"] = is_leaf
    return block[block["is_leaf"]]


def main():
    si_with_gu, gu_to_si, plain_sigun = load_gyeonggi_hierarchy()

    all_years = []
    for year, fn in FILES.items():
        df = pd.read_csv(RAW_DIR / fn)
        col0, col_industry, col_item, col_val = df.columns[0], df.columns[1], df.columns[2], df.columns[4]
        df = df[df[col_industry] == "전산업"].reset_index(drop=True)

        block = extract_gyeonggi_block(df, col0)
        leaves = reconstruct_hierarchy(block, col0, si_with_gu, gu_to_si, plain_sigun)

        pivot = leaves.pivot_table(index=["sigungu_name", col0], columns=col_item, values=col_val,
                                    aggfunc="first").reset_index()
        pivot.columns.name = None
        pivot = pivot.rename(columns={col0: "dong_name"})
        item_cols = [c for c in pivot.columns if c not in ("sigungu_name", "dong_name")]
        rename_items = {c: ("num_establishments" if "사업체" in c else "num_employees") for c in item_cols}
        pivot = pivot.rename(columns=rename_items)
        pivot["year"] = year
        all_years.append(pivot)

    result = pd.concat(all_years, ignore_index=True)
    cols = ["year", "sigungu_name", "dong_name", "num_establishments", "num_employees"]
    result = result[cols].sort_values(["year", "sigungu_name", "dong_name"])

    # KOSIS 원본이 소규모 지역은 비공개 처리(값이 "..."로 표시)하는 경우가 있어 결측으로 변환
    for c in ("num_establishments", "num_employees"):
        before = result[c].astype(str).str.match(r"^\d+$")
        result[c] = pd.to_numeric(result[c], errors="coerce")
        suppressed = (~before).sum()
        if suppressed:
            print(f"참고: {c} {suppressed}건은 KOSIS 원본에서 비공개(...) 처리되어 결측으로 저장")

    result.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"저장: {OUT} ({len(result)}행, {result['year'].nunique()}개 연도)")
    for year in FILES:
        yr = result[result["year"] == year]
        print(f"  {year}년: 읍면동 {len(yr)}행, 시군구 {yr['sigungu_name'].nunique()}개")


if __name__ == "__main__":
    main()
