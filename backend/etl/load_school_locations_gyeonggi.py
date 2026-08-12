"""
학교 위치 ETL(경기도 전역): 공공데이터포털 "전국초중등학교위치표준데이터" Open API
-> data/raw/school_locations_gyeonggi_raw.json

2026-08-12: 연천 시범 시점에는 파일데이터를 브라우저로 수동 확보했으나(school_locations_yeoncheon_raw.json),
전국 파일 다운로드는 CAPTCHA가 걸려 있어 경기도 전역 확장 시점부터는 같은 데이터셋의 Open API
(자동승인, https://api.data.go.kr/openapi/tn_pubr_public_elesch_mskul_lc_api)로 전환.
schoolId는 이 데이터셋 고유 코드(예: B000...)로 school_dim의 "정보공시 학교코드"(S...)와
체계가 달라 학교명+학교급으로 재매칭 필요 — load_school_coordinates.py에서 처리(기존과 동일 방식).

사용법: python load_school_locations_gyeonggi.py
"""
import json
from pathlib import Path

import requests

from data_go_kr_client import get_api_key

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUT = PROJECT_ROOT / "data" / "raw" / "school_locations_gyeonggi_raw.json"
ENDPOINT = "https://api.data.go.kr/openapi/tn_pubr_public_elesch_mskul_lc_api"
SIDO_NAME = "경기도교육청"
PAGE_SIZE = 1000


def fetch_all(api_key: str) -> list[dict]:
    rows = []
    page = 1
    total = None
    while total is None or len(rows) < total:
        resp = requests.get(ENDPOINT, params={
            "serviceKey": api_key, "pageNo": page, "numOfRows": PAGE_SIZE,
            "type": "json", "cddcNm": SIDO_NAME,
        }, timeout=15)
        resp.raise_for_status()
        body = resp.json()["body"]
        total = body["totalCount"]
        items = body["items"]["item"] if body["items"] else []
        rows.extend(items)
        print(f"  {page}페이지: 누적 {len(rows)}/{total}")
        page += 1
    return rows


def main():
    api_key = get_api_key()
    if not api_key:
        print("DATA_GO_KR_SERVICE_KEY가 없습니다(.env 확인). 중단합니다.")
        return

    raw = fetch_all(api_key)
    if not raw:
        print("조회 결과 없음. 중단합니다.")
        return

    # load_school_coordinates.py가 기대하는 컬럼 형태(name, level, lat, lon, status, office)로 정리
    result = [
        {
            "name": r["schoolNm"], "level": r["schoolSe"],
            "lat": float(r["latitude"]), "lon": float(r["longitude"]),
            "status": r["operSttus"], "office": r["edcSportNm"],
        }
        for r in raw
    ]
    OUT.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(f"저장: {OUT} ({len(result)}행)")


if __name__ == "__main__":
    main()
