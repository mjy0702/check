"""
야놀자 직접 API는 봇 차단이 강해 카카오 로컬 API로 국내 숙박시설을 수집합니다.
카카오 로컬 API의 숙박 카테고리(AD5)는 모텔·호텔·펜션·리조트·게스트하우스를 모두 포함합니다.
"""
import os
import httpx
from datetime import date, timedelta
from typing import Optional

KAKAO_API_KEY = os.getenv("KAKAO_API_KEY", "")

# 카카오 로컬 숙박 카테고리 코드
CATEGORY_CODE = "AD5"


async def search_nearby(lat: float, lng: float, radius_km: float = 2.0) -> list[dict]:
    if not KAKAO_API_KEY:
        return await _naver_search(lat, lng, radius_km)
    return await _kakao_search(lat, lng, radius_km)


async def _kakao_search(lat: float, lng: float, radius_km: float) -> list[dict]:
    url = "https://dapi.kakao.com/v2/local/search/category.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_API_KEY}"}
    listings = []
    page = 1
    while len(listings) < 45:
        params = {
            "category_group_code": CATEGORY_CODE,
            "x": lng,
            "y": lat,
            "radius": min(int(radius_km * 1000), 20000),  # 최대 20km
            "size": 15,
            "page": page,
            "sort": "distance",
        }
        try:
            async with httpx.AsyncClient(timeout=10, headers=headers) as client:
                r = await client.get(url, params=params)
                r.raise_for_status()
                data = r.json()
                docs = data.get("documents", [])
                if not docs:
                    break
                for doc in docs:
                    item = _parse_kakao_doc(doc)
                    if item:
                        listings.append(item)
                if data.get("meta", {}).get("is_end"):
                    break
                page += 1
        except Exception:
            break
    return listings


def _parse_kakao_doc(doc: dict) -> Optional[dict]:
    try:
        place_id = str(doc.get("id", ""))
        name = doc.get("place_name", "")
        cat = doc.get("category_name", "")
        # 숙박 카테고리 필터 (음식점 등 제외)
        if "숙박" not in cat and not any(k in cat for k in ["호텔", "모텔", "펜션", "리조트", "게스트", "민박"]):
            return None
        lat = float(doc.get("y", 0))
        lng = float(doc.get("x", 0))
        address = doc.get("road_address_name") or doc.get("address_name", "")
        phone = doc.get("phone", "")
        place_url = doc.get("place_url", "")
        # 카테고리로 room_type 결정
        room_type = "숙박"
        for kw in ["호텔", "모텔", "펜션", "리조트", "게스트하우스", "민박", "콘도"]:
            if kw in cat or kw in name:
                room_type = kw
                break
        return {
            "id": place_id,
            "platform": "yanolja",
            "name": name,
            "lat": lat,
            "lng": lng,
            "price": None,
            "rating": None,
            "review_count": 0,
            "room_type": room_type,
            "url": place_url,
            "image": None,
            "occupancy_rate": None,
            "address": address,
            "phone": phone,
        }
    except Exception:
        return None


async def _naver_search(lat: float, lng: float, radius_km: float) -> list[dict]:
    """카카오 API 키 없을 때 Naver 지역검색 fallback"""
    NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "")
    NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")
    if not NAVER_CLIENT_ID:
        return []

    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    queries = ["호텔", "모텔", "펜션", "게스트하우스"]
    listings = []
    for q in queries[:2]:  # 쿼터 절약
        params = {"query": q, "display": 5, "sort": "random"}
        try:
            async with httpx.AsyncClient(timeout=10, headers=headers) as client:
                r = await client.get("https://openapi.naver.com/v1/search/local.json", params=params)
                if r.status_code == 200:
                    for item in r.json().get("items", []):
                        listings.append({
                            "id": item.get("link", "").split("/")[-1],
                            "platform": "yanolja",
                            "name": item.get("title", "").replace("<b>", "").replace("</b>", ""),
                            "lat": float(item.get("mapy", 0)) / 1e7,
                            "lng": float(item.get("mapx", 0)) / 1e7,
                            "price": None, "rating": None, "review_count": 0,
                            "room_type": q,
                            "url": item.get("link"), "image": None, "occupancy_rate": None,
                        })
        except Exception:
            pass
    return listings


async def get_availability(listing_id: str, checkin: date, checkout: date) -> dict:
    return {"is_available": None}
