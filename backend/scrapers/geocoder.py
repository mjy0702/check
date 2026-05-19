import httpx
import os

KAKAO_API_KEY = os.getenv("KAKAO_API_KEY", "")


async def address_to_coords(address: str) -> dict:
    """주소를 위경도로 변환 (Kakao API 우선, fallback: Nominatim)"""
    if KAKAO_API_KEY:
        return await _kakao_geocode(address)
    return await _nominatim_geocode(address)


async def _kakao_geocode(address: str) -> dict:
    url = "https://dapi.kakao.com/v2/local/search/address.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_API_KEY}"}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url, headers=headers, params={"query": address})
        r.raise_for_status()
        docs = r.json().get("documents", [])
        if not docs:
            raise ValueError(f"주소를 찾을 수 없습니다: {address}")
        doc = docs[0]
        return {
            "lat": float(doc["y"]),
            "lng": float(doc["x"]),
            "address": doc.get("address_name", address),
        }


async def _nominatim_geocode(address: str) -> dict:
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": address, "format": "json", "limit": 1}
    headers = {"User-Agent": "VacancyTracker/1.0"}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url, params=params, headers=headers)
        r.raise_for_status()
        results = r.json()
        if not results:
            raise ValueError(f"주소를 찾을 수 없습니다: {address}")
        return {
            "lat": float(results[0]["lat"]),
            "lng": float(results[0]["lon"]),
            "address": results[0].get("display_name", address),
        }
