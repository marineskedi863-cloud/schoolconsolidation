"""
학생거주정보 ETL: 국토정보플랫폼 500m 격자 인구(shapefile) -> data/processed/population_grid_*.csv

원본: 국토지리정보원 인구 통계_경기도 초중등.zip (사용자 제공, 2026-10 기준)
좌표계: EPSG:5179 (Korea 2000 / Unified CS) -> EPSG:4326(WGS84)으로 변환해 지도 표시에 사용.

사용법:
    python load_grid_population.py --region 연천군
    python load_grid_population.py --all   # 경기도 31개 시군구 전체를 population_grid_경기도.csv 하나로 통합 저장
"""
import argparse
import re
import zipfile
from pathlib import Path

import shapefile
import pandas as pd
from pyproj import Transformer

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ZIP_PATH = PROJECT_ROOT / "국토지리정보원 인구 통계_경기도 초중등.zip"
RAW_DIR = PROJECT_ROOT / "data" / "raw"
OUT_DIR = PROJECT_ROOT / "data" / "processed"

SRC_CRS = "EPSG:5179"
DST_CRS = "EPSG:4326"


def list_regions_in_zip() -> list[str]:
    """zip에 들어있는 시군구 이름(경기도 접두어 제거) 목록. 대도시 일반구 단위 분리는 없음(예: 고양시는 하나)."""
    with zipfile.ZipFile(ZIP_PATH) as z:
        names = z.namelist()
    regions = set()
    for n in names:
        m = re.search(r"500M_경기도 (\S+)_\d{6}_(?:초등|중등)", n)
        if m:
            regions.add(m.group(1))
    return sorted(regions)


def extract_region_shapefiles(region: str) -> dict[str, Path]:
    """zip에서 해당 시군구의 초/중 shapefile을 data/raw/grid_<region>/ 로 추출."""
    out_dir = RAW_DIR / f"grid_{region}"
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {}
    with zipfile.ZipFile(ZIP_PATH) as z:
        targets = [n for n in z.namelist() if region in n and not n.endswith("/")]
        if not targets:
            raise ValueError(f"zip 안에서 '{region}' 관련 파일을 찾지 못함")
        for n in targets:
            level = "초등" if "초등" in n else "중등"
            base = Path(n).name
            dest = out_dir / f"{level}_{base}"
            with z.open(n) as src, open(dest, "wb") as dst:
                dst.write(src.read())
            if base.endswith(".shp"):
                result[level] = dest
    return result


def shapefile_to_df(shp_path: Path, region: str, school_level: str, data_year: str = "202410") -> pd.DataFrame:
    sf = shapefile.Reader(str(shp_path))
    transformer = Transformer.from_crs(SRC_CRS, DST_CRS, always_xy=True)

    rows = []
    for sr in sf.iterShapeRecords():
        val = sr.record["val"] or 0
        gid = sr.record["gid"]
        xs = [p[0] for p in sr.shape.points]
        ys = [p[1] for p in sr.shape.points]
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        lon, lat = transformer.transform(cx, cy)
        # 폴리곤 꼭짓점도 WGS84로 변환(지도에 정사각형 격자를 직접 그릴 때 사용)
        poly_lonlat = [transformer.transform(x, y) for x, y in sr.shape.points]
        rows.append({
            "grid_id": gid,
            "region_name": region,
            "school_level": school_level,
            "student_pop": val,
            "data_year": data_year,
            "centroid_lat": lat,
            "centroid_lon": lon,
            "polygon_lonlat": poly_lonlat,  # [(lon,lat), ...]
        })
    return pd.DataFrame(rows)


def build_region_frames(region: str) -> list[pd.DataFrame]:
    shp_paths = extract_region_shapefiles(region)
    frames = []
    for level, path in shp_paths.items():
        school_level = "초등학교" if level == "초등" else "중학교"
        df = shapefile_to_df(path, region, school_level)
        frames.append(df)
    return frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="연천군")
    ap.add_argument("--all", action="store_true", help="zip 안의 경기도 시군구 전체를 population_grid_경기도.csv로 통합 저장")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.all:
        regions = list_regions_in_zip()
        print(f"[1/2] zip 안 시군구 {len(regions)}개 발견: {regions}")
        all_frames = []
        for i, region in enumerate(regions, 1):
            frames = build_region_frames(region)
            for df in frames:
                nonzero = (df["student_pop"] > 0).sum()
                print(f"  [{i}/{len(regions)}] {region} {df['school_level'].iloc[0]}: "
                      f"격자 {len(df)}개(인구>0: {nonzero}개, 합계 {df['student_pop'].sum():.0f}명)")
            all_frames.extend(frames)
        result = pd.concat(all_frames, ignore_index=True)
        out_path = OUT_DIR / "population_grid_경기도.csv"
    else:
        print(f"[1/2] zip에서 '{args.region}' shapefile 추출")
        frames = build_region_frames(args.region)
        for df in frames:
            nonzero = (df["student_pop"] > 0).sum()
            print(f"[2/2] {df['school_level'].iloc[0]} 격자 {len(df)}개(인구>0: {nonzero}개, 합계 {df['student_pop'].sum():.0f}명)")
        result = pd.concat(frames, ignore_index=True)
        out_path = OUT_DIR / f"population_grid_{args.region}.csv"

    # 폴리곤 좌표는 문자열로 직렬화해서 CSV에 저장(로드 시 eval/json.loads)
    result["polygon_lonlat"] = result["polygon_lonlat"].apply(lambda pts: str(pts))
    result.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n저장: {out_path} ({len(result)}행)")


if __name__ == "__main__":
    main()
