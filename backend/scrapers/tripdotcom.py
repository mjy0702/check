"""
Trip.com: Playwright로 페이지 로드 후 fetchRecommendList API 응답 가로채기
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
        f"https://kr.trip.com/hotels/list?city=294217"
        f"&checkin={checkin.strftime('%Y%m%d')}"
        f"&checkout={checkout.strftime('%Y%m%d')}"
        f"&adult=2&curr=KRW"
    )

    ctx = await br.new_context()
    captured = []

    async def on_resp(resp):
        if "fetchRecommendList" in resp.url and resp.status == 200:
            try:
                body = await resp.json()
                captured.append(body)
            except Exception:
                pass

    listings = []
    try:
        page = await ctx.new_page()
        page.on("response", on_resp)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        except Exception:
            pass
        # 스크롤로 lazy loading 트리거
        for _ in range(4):
            await page.evaluate("window.scrollBy(0, 600)")
            await page.wait_for_timeout(1200)

        if captured:
            hotel_list = (
                captured[0].get("data", {})
                .get("recommendHotel", {})
                .get("hotelList", [])
            )
            for h in hotel_list:
                item = _parse_recommend_hotel(h, lat, lng, radius_km)
                if item:
                    listings.append(item)
        else:
            # fallback: DOM 카드에서 추출
            content = await page.content()
            listings = _extract_from_page_json(content)

    finally:
        await ctx.close()

    return listings


def _parse_recommend_hotel(h: dict, center_lat: float, center_lng: float, radius_km: float) -> Optional[dict]:
    try:
        info = h.get("hotelInfo", {})
        summary = info.get("summary", {})
        hotel_id = str(summary.get("hotelId") or summary.get("masterHotelId") or "")
        name = (
            info.get("hotelBasicInfo", {}).get("hotelName")
            or summary.get("hotelName")
            or ""
        )
        pos = info.get("positionInfo", {})
        lat = pos.get("latitude") or pos.get("lat")
        lng = pos.get("longitude") or pos.get("lng")

        # 반경 내 필터
        if lat and lng:
            dlat = abs(float(lat) - center_lat)
            dlng = abs(float(lng) - center_lng)
            if dlat > radius_km / 111.0 * 1.5 or dlng > radius_km / 88.0 * 1.5:
                return None

        price_info = info.get("priceInfo", {}) or {}
        price_raw = (
            price_info.get("price")
            or price_info.get("originalPrice")
            or price_info.get("displayPrice")
        )

        star = info.get("hotelBasicInfo", {}).get("starLevel", 0)
        comment = info.get("commentInfo", {}) or {}
        rating = comment.get("commentScore") or comment.get("score")
        review_count = comment.get("commentNum") or comment.get("count", 0)

        images = info.get("imageInfo", {}).get("hotelImages") or []
        image = images[0].get("url") if images and isinstance(images[0], dict) else (images[0] if images else None)

        return {
            "id": hotel_id,
            "platform": "tripdotcom",
            "name": name,
            "lat": float(lat) if lat else None,
            "lng": float(lng) if lng else None,
            "price": int(float(str(price_raw).replace(",", ""))) if price_raw else None,
            "rating": float(rating) if rating else None,
            "review_count": int(review_count) if review_count else 0,
            "room_type": f"★{star}" if star else "",
            "url": f"https://kr.trip.com/hotels/detail/?hotelId={hotel_id}&curr=KRW" if hotel_id else None,
            "image": image,
            "occupancy_rate": None,
        }
    except Exception:
        return None


def _extract_from_page_json(content: str) -> list[dict]:
    listings = []
    for pattern in [
        r'"hotelList"\s*:\s*(\[.*?\])\s*[,}]',
        r'"hotels"\s*:\s*(\[.*?\])',
    ]:
        m = re.search(pattern, content, re.DOTALL)
        if m:
            try:
                hotels = json.loads(m.group(1))
                for h in hotels[:30]:
                    item = _parse_simple_hotel(h)
                    if item:
                        listings.append(item)
                if listings:
                    break
            except Exception:
                pass
    return listings


def _parse_simple_hotel(h: dict) -> Optional[dict]:
    try:
        hotel_id = str(h.get("hotelId") or h.get("id") or "")
        name = h.get("hotelName") or h.get("name") or ""
        if not name:
            return None
        pos = h.get("position") or h.get("coordinate") or {}
        lat = pos.get("lat") or pos.get("latitude") or h.get("lat")
        lng = pos.get("lng") or pos.get("longitude") or h.get("lng")
        price_raw = h.get("minPrice") or h.get("avgPrice") or (h.get("displayPrice") or {}).get("price")
        rating = h.get("travellerRating") or h.get("score")
        return {
            "id": hotel_id,
            "platform": "tripdotcom",
            "name": name,
            "lat": float(lat) if lat else None,
            "lng": float(lng) if lng else None,
            "price": int(float(str(price_raw).replace(",", ""))) if price_raw else None,
            "rating": float(rating) if rating else None,
            "review_count": h.get("reviewCount", 0),
            "room_type": "",
            "url": f"https://kr.trip.com/hotels/detail/?hotelId={hotel_id}&curr=KRW" if hotel_id else None,
            "image": None,
            "occupancy_rate": None,
        }
    except Exception:
        return None


async def get_availability(hotel_id: str, checkin: date, checkout: date) -> dict:
    return {"is_available": None}
