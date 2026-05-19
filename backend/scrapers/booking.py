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

    delta_lat = radius_km / 111.0
    delta_lng = radius_km / 88.0

    url = (
        f"https://www.booking.com/searchresults.ko.html"
        f"?latitude={lat}&longitude={lng}&radius={int(radius_km)}"
        f"&checkin={checkin}&checkout={checkout}"
        f"&group_adults=2&no_rooms=1&search_type=latlong"
        f"&ne_lat={lat+delta_lat}&ne_lng={lng+delta_lng}"
        f"&sw_lat={lat-delta_lat}&sw_lng={lng-delta_lng}"
        f"&rows=40"
    )

    ctx = await br.new_context()
    listings = []
    try:
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(4000)
        except Exception:
            pass

        content = await page.content()
        title = await page.title()
        logger.info(f"[booking] 페이지 제목: {title}, 내용 길이: {len(content)}")

        coord_map = _extract_coord_map(content)

        cards = await page.query_selector_all('[data-testid="property-card"]')
        logger.info(f"[booking] 카드 수: {len(cards)}")
        for card in cards:
            item = await _parse_card(card, coord_map)
            if item:
                listings.append(item)

    finally:
        await ctx.close()

    return listings


def _extract_coord_map(content: str) -> dict:
    """slug(pageName) → (lat, lng) 매핑 추출.
    Booking.com 페이지 패턴:
      "pageName":"<slug>","location":{"latitude":<lat>,"longitude":<lng>}
    """
    coord_map = {}

    # 실제 구조: "latitude":<lat>,"longitude":<lng>,...,"pageName":"<slug>"
    # latitude가 pageName보다 먼저 등장함
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
        img_el = await card.query_selector("img")
        score_el = await card.query_selector('[data-testid="review-score"]')
        price_el = await card.query_selector('[data-testid="price-and-discounted-price"]')
        addr_el = await card.query_selector('[data-testid="address"]')

        name = await name_el.inner_text() if name_el else ""
        href = await link_el.get_attribute("href") if link_el else ""

        # slug 추출 (href의 hotel/kr/<slug>.html 패턴)
        slug_match = re.search(r"/hotel/[a-z]+/([^.?/]+)", href or "")
        hotel_id = slug_match.group(1) if slug_match else None

        # 좌표: slug → coord_map 조회
        coords = coord_map.get(hotel_id) if hotel_id else None

        lat, lng = coords if coords else (None, None)

        # 가격
        price_text = await price_el.inner_text() if price_el else ""
        price_match = re.search(r"([\d,]+)", price_text.replace("₩", "").replace("KRW", ""))
        price = int(price_match.group(1).replace(",", "")) if price_match else None

        # 평점
        score_text = await score_el.inner_text() if score_el else ""
        score_match = re.search(r"(\d+[.,]\d+)", score_text)
        rating = float(score_match.group(1).replace(",", ".")) if score_match else None
        reviews_match = re.search(r"([\d,]+)\s*(?:개|reviews|후기)", score_text)
        review_count = int(reviews_match.group(1).replace(",", "")) if reviews_match else 0

        img_src = await img_el.get_attribute("src") if img_el else None
        addr = await addr_el.inner_text() if addr_el else ""

        return {
            "id": hotel_id,
            "platform": "booking",
            "name": name.strip(),
            "lat": lat,
            "lng": lng,
            "price": price,
            "rating": rating,
            "review_count": review_count,
            "room_type": addr.strip(),
            "url": href if href and href.startswith("http") else f"https://www.booking.com{href}",
            "image": img_src,
            "occupancy_rate": None,
        }
    except Exception:
        return None


async def get_availability(property_id: str, checkin: date, checkout: date) -> dict:
    return {"is_available": None, "total_room_types": 0, "available_room_types": 0}
