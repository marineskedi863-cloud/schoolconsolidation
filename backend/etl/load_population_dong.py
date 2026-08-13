"""
읍면동(행정동) 인구 시계열 ETL: 2019~2024년 12월 말 기준 -> data/processed/population_dong.csv

원본(2026-08-13, data/raw/population_dong/)은 출처가 둘로 나뉘어 포맷이 다르다(의사결정_기록.md 항목38):
- 2022~2024: data.go.kr(행정안전부) 파일 — 행정기관코드·시도명·시군구명·읍면동명이 이미 분리된 깔끔한 컬럼
- 2019~2021: jumin.mois.go.kr 원천 사이트 — "행정구역" 컬럼 하나에 "시도+시군구+읍면동 이름(코드)"가
  합쳐져 있고, 시도/시군구 집계행과 읍면동행이 전부 섞여 있음(콤마 포함 숫자 문자열)

읍면동 leaf 코드만 남기기 위해 2022~2024 파일에서 얻은 행정기관코드 집합(대부분 안정적으로 유지됨,
2019~2022 사이 교집합 3,500/3,576 = 약 98% 확인됨)을 기준으로 2019~2021 데이터를 필터링한다.
이 과정에서 코드가 바뀐 소수의 동(신설/폐지/명칭변경)은 해당 연도 데이터에서 누락될 수 있음.

사용법: python load_population_dong.py
"""
import re
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "population_dong"
OUT = PROJECT_ROOT / "data" / "processed" / "population_dong.csv"

NEW_FORMAT = {
    2022: "지역별_행정동_인구수_20221231.csv",
    2023: "지역별_행정동_인구수_20231231.csv",
    2024: "지역별_행정동_인구수_20241231.csv",
}
OLD_FORMAT = {
    2019: "지역별_행정동_인구수_20191231.csv",
    2020: "지역별_행정동_인구수_20201231.csv",
    2021: "지역별_행정동_인구수_20211231.csv",
}


def load_new_format():
    rows = []
    code_to_name = {}
    for year, fn in NEW_FORMAT.items():
        df = pd.read_csv(RAW_DIR / fn, dtype={"행정기관코드": str})
        for r in df.itertuples():
            code_to_name[r.행정기관코드] = (r.시도명, r.시군구명, r.읍면동명)
        out = df.rename(columns={"행정기관코드": "dong_code", "시도명": "sido_name",
                                  "시군구명": "sigungu_name", "읍면동명": "dong_name", "계": "population"})
        out["year"] = year
        rows.append(out[["year", "dong_code", "sido_name", "sigungu_name", "dong_name", "population"]])
    return pd.concat(rows, ignore_index=True), code_to_name


def load_old_format(valid_codes: set, code_to_name: dict):
    rows = []
    for year, fn in OLD_FORMAT.items():
        df = pd.read_csv(RAW_DIR / fn)
        col0, col_pop = df.columns[0], df.columns[1]
        parsed = df[col0].str.extract(r"^(.*?)\s*\((\d+)\)$")
        df = df.assign(dong_code=parsed[1])
        df = df[df["dong_code"].isin(valid_codes)].copy()
        df["population"] = df[col_pop].str.replace(",", "", regex=False).astype(int)
        df["sido_name"] = df["dong_code"].map(lambda c: code_to_name[c][0])
        df["sigungu_name"] = df["dong_code"].map(lambda c: code_to_name[c][1])
        df["dong_name"] = df["dong_code"].map(lambda c: code_to_name[c][2])
        df["year"] = year
        rows.append(df[["year", "dong_code", "sido_name", "sigungu_name", "dong_name", "population"]])
    return pd.concat(rows, ignore_index=True)


def main():
    new_df, code_to_name = load_new_format()
    old_df = load_old_format(set(code_to_name), code_to_name)
    result = pd.concat([old_df, new_df], ignore_index=True).sort_values(["year", "dong_code"])

    result.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"저장: {OUT} ({len(result)}행, {result['year'].nunique()}개 연도: {sorted(result['year'].unique())})")
    gg = result[result["sido_name"] == "경기도"]
    print(f"경기도: {gg['dong_code'].nunique()}개 읍면동 x {gg['year'].nunique()}개 연도")


if __name__ == "__main__":
    main()
