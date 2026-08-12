"""
행정경계 ETL: 통계청 SGIS Open API -> data/processed/admin_boundary_경기도.geojson

경기도 시군구(및 대도시 일반구) 경계 전체를 한 번에 조회해 저장한다(2026-08-12 확장 —
기존에는 시범지역 연천군만 필터링했으나, 경기도 전역으로 시뮬레이션 범위를 넓히면서
SGIS가 어차피 한 번의 호출로 경기도 전체를 반환하는 점을 활용해 전량 저장하는 방식으로 변경).
앱에서는 이 파일 하나를 로드해 사용자가 선택한 시군구 이름으로 걸러 쓴다.

SGIS 좌표계는 UTM-K(EPSG:5179)라 folium 지도 표시를 위해 WGS84(EPSG:4326)로 변환한다
(격자인구 ETL과 동일 방식, load_grid_population.py 참조).

사용법: python load_admin_boundary.py
"""
from pathlib import Path

import json

from pyproj import Transformer

from sgis_boundary import get_access_token, fetch_boundary

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED = PROJECT_ROOT / "data" / "processed"

GYEONGGI_ADM_CD = "31"  # SGIS 자체 시도코드 — 통계청 표준 행정구역코드(41)와 다름(2026-08 확인)
YEAR = "2024"
OUT = PROCESSED / "admin_boundary_경기도.geojson"

SRC_CRS = "EPSG:5179"
DST_CRS = "EPSG:4326"


def _reproject_coords(coords, transformer):
    """GeoJSON coordinates는 [x,y] 쌍이 임의 깊이로 중첩됨(Polygon/MultiPolygon 공용) — 재귀로 처리."""
    if isinstance(coords[0], (int, float)):
        lon, lat = transformer.transform(coords[0], coords[1])
        return [lon, lat]
    return [_reproject_coords(c, transformer) for c in coords]


def main():
    token = get_access_token()
    if not token:
        print("SGIS accessToken 발급 실패 — .env의 SGIS_CONSUMER_KEY/SGIS_CONSUMER_SECRET 확인. 중단합니다.")
        return

    data = fetch_boundary(GYEONGGI_ADM_CD, YEAR, token, low_search=1)
    if data is None:
        print("경계 조회 실패(API 응답 이상). 중단합니다.")
        return

    features = data["features"]
    transformer = Transformer.from_crs(SRC_CRS, DST_CRS, always_xy=True)
    for f in features:
        f["geometry"]["coordinates"] = _reproject_coords(f["geometry"]["coordinates"], transformer)

    out_geojson = {"type": "FeatureCollection", "features": features}
    OUT.write_text(json.dumps(out_geojson, ensure_ascii=False), encoding="utf-8")
    print(f"저장: {OUT} ({len(features)}개 시군구/구 폴리곤, EPSG:5179 -> EPSG:4326 변환 완료)")


if __name__ == "__main__":
    main()
