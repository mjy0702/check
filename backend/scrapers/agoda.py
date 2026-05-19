"""
Agoda: Playwright로 검색 후 API 응답 가로채기.
Agoda는 강력한 봇 감지를 사용하므로 여러 URL 패턴을 시도합니다.
"""
import re
import json
from datetime import date, timedelta
from typing import Optional
from . import browser as br


async def search_nearby(lat: float, lng: float, radius_km: float = 2.0) -> list[dict]:
    checkin = date.today() + timedelta(days=1)
    checkout = date.today() + timedelta(days=2)

    url = (
        f"https://www.agoda.com/search"
        f"?checkIn={checkin.strftime('%Y-%m-%d')}"
        f"&checkOut={checkout.strftime('%Y-%m-%d')}"
        f"&rooms=1&adults=2&children=0"
        f"&latitude={lat}&longitude={lng}"
        f"&priceCur=KRW&los=1&isMap=true"
        f"&selectedproperty=0"
    )

    ctx = await br.new_context()
    captured_properties = []

    async def on_resp(resp):
        if "agoda.com" in resp.url and resp.status == 200:
            ct = resp.headers.get("content-type", "")
            if "json" in ct:
                try:
                    body = await resp.json()
                    t = json.dumps(body, ensure_ascii=False)
                    if (
                        ("propertyId" in t or "hotelId" in t or "PropertyId" in t)
                        and len(t) > 1000
                    ):
                        captured_properties.append({"url": resp.url, "body": body})
                except Exception:
                    pass

    listings = []
    try:
        page = await ctx.new_page()
        page.on("response", on_resp)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(6000)
        except Exception:
            pass

        # 스크롤로 lazy loading
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, 500)")
            await page.wait_for_timeout(1000)

        # 캡처된 API 응답 파싱
        for cap in captured_properties:
            props = _extract_properties(cap["body"])
            for p in props:
                item = _parse_property(p, lat, lng, radius_km)
                if item:
                    listings.append(item)
            if listings:
                break

        # fallback: DOM 파싱
        if not listings:
            content = await page.content()
            listings = _extract_from_content(content)

    finally:
        await ctx.close()

    return listings


def _extract_properties(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in (
            "propertyResultList", "resultList", "properties",
            "hotels", "data", "Results", "HotelList",
        ):
            if key in data:
                found = _extract_properties(data[key])
                if found:
                    return found
    return []


def _parse_property(p: dict, center_lat: float, center_lng: float, radius_km: float) -> Optional[dict]:
    try:
        prop_id = str(
            p.get("propertyId") or p.get("hotelId")
            or p.get("PropertyId") or p.get("id") or ""
        )
        name = (
            p.get("propertyName") or p.get("hotelName")
            or p.get("PropertyName") or p.get("name") or ""
        )
        if not name:
            return None

        lat = (
            p.get("latitude") or p.get("Latitude")
            or (p.get("property") or {}).get("latitude")
            or (p.get("location") or {}).get("latitude")
        )
        lng = (
            p.get("longitude") or p.get("Longitude")
            or (p.get("property") or {}).get("longitude")
            or (p.get("location") or {}).get("longitude")
        )

        # 반경 내 필터
        if lat and lng:
            dlat = abs(float(lat) - center_lat)
            dlng = abs(float(lng) - center_lng)
            if dlat > radius_km / 111.0 * 1.5 or dlng > radius_km / 88.0 * 1.5:
                return None

        price_info = p.get("pricing") or p.get("price") or {}
        price_raw = (
            price_info.get("dailyRate") or price_info.get("totalRate")
            or p.get("minPrice") or p.get("displayPrice")
            or p.get("Price")
        )

        rating = (
            p.get("reviewScore") or p.get("Rating")
            or (p.get("reviews") or {}).get("cumulative", {}).get("score")
        )
        review_count = (
            p.get("reviewCount") or p.get("ReviewCount")
            or (p.get("reviews") or {}).get("cumulative", {}).get("reviewCount", 0)
        )
        star = p.get("starRating") or p.get("star") or p.get("StarRating", 0)

        images = p.get("images") or (p.get("property") or {}).get("images") or []
        image = None
        if images:
            first = images[0]
            image = (
                first if isinstance(first, str)
                else (first.get("url") or first.get("highResUrl") or first.get("imageUrl"))
            )

        return {
            "id": prop_id,
            "platform": "agoda",
            "name": name,
            "lat": float(lat) if lat else None,
            "lng": float(lng) if lng else None,
            "price": int(float(str(price_raw).replace(",", ""))) if price_raw else None,
            "rating": float(rating) if rating else None,
            "review_count": int(review_count) if review_count else 0,
            "room_type": f"★{star}" if star else "",
            "url": f"https://www.agoda.com/hotel/{prop_id}" if prop_id else None,
            "image": image,
            "occupancy_rate": None,
        }
    except Exception:
        return None


def _extract_from_content(content: str) -> list[dict]:
    listings = []
    for pattern in [
        r'"propertyResultList"\s*:\s*(\[.*?\])',
        r'"resultList"\s*:\s*(\[.*?\])',
        r'"HotelList"\s*:\s*(\[.*?\])',
    ]:
        m = re.search(pattern, content, re.DOTALL)
        if m:
            try:
                props = json.loads(m.group(1))
                for p in props[:30]:
                    item = _parse_property(p, 0, 0, 999)
                    if item:
                        listings.append(item)
                if listings:
                    break
            except Exception:
                pass
    return listings


async def get_availability(property_id: str, checkin: date, checkout: date) -> dict:
    return {"is_available": None}
