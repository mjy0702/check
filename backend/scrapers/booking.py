import re
import json
import logging
from datetime import date, timedelta
from typing import Optional
from . import browser as br

logger = logging.getLogger(__name__)


async def search_nearby(lat: float, lng: float, radius_km: float = 2.0) -> list[dict]:
    checkin = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")
    checkout = (date.today() + timedelta(days=8)).strftime("%Y-%m-%d")

    url = (
        f"https://www.booking.com/searchresults.ko.html"
        f"?latitude={lat}&longitude={lng}&radius={int(radius_km)}"
        f"&checkin={checkin}&checkout={checkout}"
        f"&group_adults=2&no_rooms=1&search_type=latlong&rows=40"
    )

    ctx = await br.new_context()
    captured = []

    async def on_resp(resp):
        if resp.status != 200:
            return
        url_lower = resp.url.lower()
        ct = resp.headers.get("content-type", "")
        if "booking.com" not in resp.url:
            return
        if "json" in ct and len(resp.url) > 30:
            try:
                body = await resp.json()
                text = json.dumps(body, ensure_ascii=False)
                if ("hotel_id" in text or "hotelId" in text or "property_id" in text) and len(text) > 500:
                    captured.append({"url": resp.url, "body": body})
            except Exception:
                pass

    listings = []
    try:
        page = await ctx.new_page()
        page.on("response", on_resp)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)
        except Exception:
            pass

        title = await page.title()
        content = await page.content()
        logger.info(f"[booking] 제목: {title}, 크기: {len(content)}, 캡처 API: {len(captured)}개")

        # API 인터셉트 결과 파싱
        for cap in captured:
            props = _extract_properties(cap["body"])
            for p in props:
                item = _parse_property(p)
                if item:
                    listings.append(item)
            if listings:
                break

        # DOM fallback
        if not listings:
            cards = await page.query_selector_all('[data-testid="property-card"]')
            logger.info(f"[booking] DOM 카드: {len(cards)}개")
            coord_map = _extract_coord_map(content)
            for card in cards:
                item = await _parse_card(card, coord_map)
                if item:
                    listings.append(item)

        # HTML JSON fallback
        if not listings:
            listings = _extract_from_html(content)
            logger.info(f"[booking] HTML fallback: {len(listings)}개")

    finally:
        await ctx.close()

    return listings


def _extract_properties(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("hotels", "properties", "results", "data", "hotel_list", "hotelList"):
            if key in data:
                found = _extract_properties(data[key])
                if found:
                    return found
    return []


def _parse_property(p: dict) -> Optional[dict]:
    try:
        hotel_id = str(
            p.get("hotel_id") or p.get("hotelId") or p.get("property_id") or p.get("id") or ""
        )
        name = p.get("hotel_name") or p.get("name") or p.get("hotelName") or ""
        if not name:
            return None
        lat = p.get("latitude") or p.get("lat") or (p.get("location") or {}).get("latitude")
        lng = p.get("longitude") or p.get("lng") or (p.get("location") or {}).get("longitude")
        price_raw = (
            p.get("min_total_price") or p.get("price") or p.get("minPrice")
            or (p.get("composite_price_breakdown") or {}).get("gross_amount", {}).get("value")
        )
        rating = p.get("review_score") or p.get("rating") or p.get("reviewScore")
        review_count = p.get("review_nr") or p.get("reviewCount") or 0
        image = (p.get("main_photo_url") or p.get("image") or "")
        url = f"https://www.booking.com/hotel/kr/{hotel_id}.ko.html" if hotel_id else None

        return {
            "id": hotel_id,
            "platform": "booking",
            "name": name,
            "lat": float(lat) if lat else None,
            "lng": float(lng) if lng else None,
            "price": int(float(str(price_raw))) if price_raw else None,
            "rating": float(rating) if rating else None,
            "review_count": int(review_count) if review_count else 0,
            "room_type": "",
            "url": url,
            "image": image if image.startswith("http") else None,
            "occupancy_rate": None,
        }
    except Exception:
        return None


def _extract_coord_map(content: str) -> dict:
    coord_map = {}
    p1 = re.compile(
        r'"latitude"\s*:\s*([\d.]+)\s*,\s*"longitude"\s*:\s*([\d.]+).{0,200}"pageName"\s*:\s*"([^"]+)"',
        re.DOTALL,
    )
    for m in p1.finditer(content):
        coord_map[m.group(3)] = (float(m.group(1)), float(m.group(2)))
    return coord_map


async def _parse_card(card, coord_map: dict) -> Optional[dict]:
    try:
        name_el = await card.query_selector('[data-testid="title"]')
        link_el = await card.query_selector('a[data-testid="title-link"]')
        price_el = await card.query_selector('[data-testid="price-and-discounted-price"]')
        score_el = await card.query_selector('[data-testid="review-score"]')

        name = await name_el.inner_text() if name_el else ""
        href = await link_el.get_attribute("href") if link_el else ""
        slug_match = re.search(r"/hotel/[a-z]+/([^.?/]+)", href or "")
        hotel_id = slug_match.group(1) if slug_match else None
        coords = coord_map.get(hotel_id) if hotel_id else None
        lat, lng = coords if coords else (None, None)

        price_text = await price_el.inner_text() if price_el else ""
        price_match = re.search(r"([\d,]+)", price_text.replace("₩", "").replace("KRW", ""))
        price = int(price_match.group(1).replace(",", "")) if price_match else None

        score_text = await score_el.inner_text() if score_el else ""
        score_match = re.search(r"(\d+[.,]\d+)", score_text)
        rating = float(score_match.group(1).replace(",", ".")) if score_match else None

        return {
            "id": hotel_id,
            "platform": "booking",
            "name": name.strip(),
            "lat": lat, "lng": lng,
            "price": price,
            "rating": rating,
            "review_count": 0,
            "room_type": "",
            "url": href if href and href.startswith("http") else f"https://www.booking.com{href}",
            "image": None,
            "occupancy_rate": None,
        }
    except Exception:
        return None


def _extract_from_html(content: str) -> list[dict]:
    for pattern in [
        r'"hotels"\s*:\s*(\[.*?\])\s*[,}]',
        r'"properties"\s*:\s*(\[.*?\])\s*[,}]',
        r'"hotel_list"\s*:\s*(\[.*?\])\s*[,}]',
    ]:
        m = re.search(pattern, content, re.DOTALL)
        if m:
            try:
                props = json.loads(m.group(1))
                result = [p for p in (_parse_property(h) for h in props[:30]) if p]
                if result:
                    return result
            except Exception:
                pass
    return []


async def get_availability(property_id: str, checkin: date, checkout: date) -> dict:
    return {"is_available": None, "total_room_types": 0, "available_room_types": 0}
