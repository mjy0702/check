import re
import json
import asyncio
from datetime import date, timedelta
from typing import Optional
from . import browser as br

import httpx

HTTPX_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "X-Airbnb-API-Key": "d306zoyjsyarp7ifhu67rjxn52tv0t20",
}


async def search_nearby(lat: float, lng: float, radius_km: float = 2.0) -> list[dict]:
    checkin = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")
    checkout = (date.today() + timedelta(days=8)).strftime("%Y-%m-%d")

    # 반경을 위경도 델타로 변환 (1도 ≈ 111km)
    delta_lat = radius_km / 111.0
    delta_lng = radius_km / 88.0  # 서울 위도 기준

    url = (
        f"https://www.airbnb.com/s/Seoul--South-Korea/homes"
        f"?ne_lat={lat+delta_lat}&ne_lng={lng+delta_lng}"
        f"&sw_lat={lat-delta_lat}&sw_lng={lng-delta_lng}"
        f"&search_by_map=true&checkin={checkin}&checkout={checkout}"
        f"&adults=2&items_per_grid=40"
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

        # 카드 목록 추출
        cards = await page.query_selector_all('[data-testid="card-container"]')
        for card in cards:
            item = await _parse_card(card)
            if item:
                listings.append(item)

        # 좌표가 없는 항목은 페이지 JSON에서 보충
        await _enrich_coords(listings, content)

    finally:
        await ctx.close()

    return listings


async def _parse_card(card) -> Optional[dict]:
    try:
        name_el = await card.query_selector('[data-testid="listing-card-title"]')
        link_el = await card.query_selector("a[href*='/rooms/']")
        img_el = await card.query_selector("img")
        rating_el = await card.query_selector('[aria-label*="점"], [class*="t1a9j9y7"]')

        href = await link_el.get_attribute("href") if link_el else ""
        listing_id_match = re.search(r"/rooms/(\d+)", href or "")
        listing_id = listing_id_match.group(1) if listing_id_match else None

        name = await name_el.inner_text() if name_el else ""

        # 가격: ₩ 패턴
        html = await card.inner_html()
        price_matches = re.findall(r"₩([\d,]+)", html)
        price = int(price_matches[0].replace(",", "")) if price_matches else None

        # 평점
        rating_text = await rating_el.inner_text() if rating_el else ""
        rating_match = re.search(r"(\d+\.\d+)", rating_text)
        rating = float(rating_match.group(1)) if rating_match else None

        img_src = await img_el.get_attribute("src") if img_el else None

        return {
            "id": listing_id,
            "platform": "airbnb",
            "name": name.strip(),
            "lat": None,
            "lng": None,
            "price": price,
            "rating": rating,
            "review_count": 0,
            "room_type": "",
            "url": f"https://www.airbnb.com/rooms/{listing_id}" if listing_id else None,
            "image": img_src,
            "occupancy_rate": None,
        }
    except Exception:
        return None


async def _enrich_coords(listings: list[dict], content: str):
    """페이지 소스에서 좌표 추출해 리스팅에 매핑

    에어비앤비는 base64 인코딩된 ID로 좌표를 저장함.
    예: "id":"RGVtYW5kU3RheUxpc3Rpbmc6MTIzNDU2" → DemandStayListing:123456
    """
    import base64

    coord_map: dict[str, tuple[float, float]] = {}

    # 패턴: base64 ID + coordinate
    # {20,}: 짧은 "id" 필드(이미지 ID 등)와 혼동 방지
    pattern = re.compile(
        r'"id"\s*:\s*"([A-Za-z0-9+/=]{20,})".*?"latitude"\s*:\s*([\d.]+)\s*,\s*"longitude"\s*:\s*([\d.]+)',
        re.DOTALL,
    )
    for m in pattern.finditer(content):
        b64_id, lat_s, lng_s = m.group(1), m.group(2), m.group(3)
        try:
            decoded = base64.b64decode(b64_id).decode("utf-8", errors="ignore")
            # "DemandStayListing:1234567890" 형태
            numeric_id = decoded.split(":")[-1].strip()
            if numeric_id.isdigit():
                coord_map[numeric_id] = (float(lat_s), float(lng_s))
        except Exception:
            pass

    # fallback: latitude/longitude 순서대로 할당
    if not coord_map:
        all_coords = re.findall(r'"latitude"\s*:\s*([\d.]+)\s*,\s*"longitude"\s*:\s*([\d.]+)', content)
        for i, listing in enumerate(listings):
            if i < len(all_coords):
                listing["lat"] = float(all_coords[i][0])
                listing["lng"] = float(all_coords[i][1])
        return

    for listing in listings:
        lid = listing.get("id")
        if lid and lid in coord_map:
            listing["lat"], listing["lng"] = coord_map[lid]


async def get_occupancy_rate(listing_id: str, months: int = 3) -> dict:
    url = "https://www.airbnb.com/api/v3/PdpAvailabilityCalendar"
    today = date.today()
    variables = {
        "request": {
            "count": months,
            "listingId": str(listing_id),
            "month": today.month,
            "year": today.year,
        }
    }
    extensions = {
        "persistedQuery": {
            "version": 1,
            "sha256Hash": "8f08e03c7bd16fcad3c92a3592c19a8b559a0d0855a84028d1163d4733ed9ade",
        }
    }
    params = {
        "operationName": "PdpAvailabilityCalendar",
        "locale": "ko",
        "currency": "KRW",
        "variables": json.dumps(variables),
        "extensions": json.dumps(extensions),
    }

    total_days = booked_days = 0
    try:
        async with httpx.AsyncClient(timeout=15, headers=HTTPX_HEADERS) as client:
            r = await client.get(url, params=params)
            if r.status_code == 200:
                data = r.json()
                months_data = (
                    data.get("data", {})
                    .get("merlin", {})
                    .get("pdpAvailabilityCalendar", {})
                    .get("calendarMonths", [])
                )
                for month_data in months_data:
                    for day in month_data.get("days", []):
                        day_date = date.fromisoformat(day["calendarDate"])
                        if day_date >= today:
                            total_days += 1
                            if not day["available"]:
                                booked_days += 1
    except Exception:
        pass

    if total_days == 0:
        return {"occupancy_rate": None, "booked": 0, "available": 0, "total": 0}

    rate = round(booked_days / total_days * 100, 1)
    return {
        "occupancy_rate": rate,
        "booked": booked_days,
        "available": total_days - booked_days,
        "total": total_days,
    }


async def enrich_with_occupancy(listings: list[dict]) -> list[dict]:
    async def fetch_one(listing: dict) -> dict:
        if listing.get("id"):
            cal = await get_occupancy_rate(listing["id"])
            listing["occupancy_rate"] = cal.get("occupancy_rate")
            listing["calendar"] = cal
        return listing

    return list(await asyncio.gather(*[fetch_one(l) for l in listings]))
