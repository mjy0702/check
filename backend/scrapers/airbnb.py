import re
import json
import base64
import asyncio
import logging
import httpx
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

API_KEY = "d306zoyjsyarp7ifhu67rjxn52tv0t20"

SEARCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

CAL_HEADERS = {
    "User-Agent": SEARCH_HEADERS["User-Agent"],
    "Accept": "application/json",
    "X-Airbnb-API-Key": API_KEY,
}


def _make_cursor(items_offset: int) -> str:
    data = {"section_offset": 0, "items_offset": items_offset, "version": 1}
    return base64.b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()


async def search_nearby(lat: float, lng: float, radius_km: float = 2.0) -> list[dict]:
    delta_lat = radius_km / 111.0
    delta_lng = radius_km / 88.0

    # 날짜 없이 검색 → 조회일 기준 전후 1달 내 가용 숙소 모두 수집
    # 검색 URL에는 날짜 미포함 (모든 숙소 노출)
    base_url = (
        "https://www.airbnb.com/s/Seoul--South-Korea/homes"
        f"?ne_lat={lat+delta_lat:.6f}&ne_lng={lng+delta_lng:.6f}"
        f"&sw_lat={lat-delta_lat:.6f}&sw_lng={lng-delta_lng:.6f}"
        f"&search_by_map=true&items_per_grid=40&tab_id=home_tab"
    )

    all_listings: list[dict] = []
    seen_ids: set[str] = set()
    page = 0

    async with httpx.AsyncClient(timeout=30, headers=SEARCH_HEADERS, follow_redirects=True) as client:
        while True:
            url = base_url if page == 0 else f"{base_url}&cursor={_make_cursor(page * 18)}"
            try:
                r = await client.get(url)
                logger.info(f"[airbnb] 페이지{page+1} status={r.status_code}, size={len(r.text)}")
                if r.status_code != 200:
                    break
                listings = _parse_html(r.text)
            except Exception as e:
                logger.error(f"[airbnb] 페이지{page+1} 요청 실패: {e}")
                break

            if not listings:
                break

            new = 0
            for l in listings:
                if l["id"] not in seen_ids:
                    seen_ids.add(l["id"])
                    all_listings.append(l)
                    new += 1

            logger.info(f"[airbnb] 페이지{page+1}: {len(listings)}개, 신규 {new}개 (누적 {len(all_listings)}개)")

            if new == 0 or len(listings) < 18:
                break

            page += 1
            await asyncio.sleep(0.5)  # 과부하 방지

    logger.info(f"[airbnb] 총 {len(all_listings)}개 수집")
    return all_listings


def _parse_html(html: str) -> list[dict]:
    results = _extract_stays_search(html)
    if results:
        return results

    m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            found = _find_search_results(data)
            if found:
                return [r for r in (_parse_result(x) for x in found) if r]
        except Exception as e:
            logger.warning(f"[airbnb] __NEXT_DATA__ 파싱 실패: {e}")

    if len(html) < 10000:
        logger.warning(f"[airbnb] 페이지 짧음({len(html)}자) - 봇 차단 가능성")
    return []


def _extract_stays_search(html: str) -> list[dict]:
    idx = html.find('"staysSearch"')
    if idx == -1:
        return []
    window = html[idx: idx + 2000000]
    sr_idx = window.find('"searchResults"')
    if sr_idx == -1:
        return []
    arr_start = window.find('[', sr_idx + len('"searchResults"'))
    if arr_start == -1:
        return []
    array_str = _extract_json_array(window, arr_start)
    if not array_str:
        return []
    try:
        results = json.loads(array_str)
        return [r for r in (_parse_result(x) for x in results) if r]
    except Exception as e:
        logger.warning(f"[airbnb] searchResults JSON 파싱 실패: {e}")
        return []


def _extract_json_array(text: str, start: int) -> Optional[str]:
    if start >= len(text) or text[start] != '[':
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                return text[start: i + 1]
    return None


def _find_search_results(data, depth=0) -> list:
    if depth > 8:
        return []
    if isinstance(data, list) and len(data) > 0:
        first = data[0]
        if isinstance(first, dict) and ("listing" in first or "id" in first or "demandStayListing" in first):
            return data
    if isinstance(data, dict):
        for key in ["searchResults", "staySearchResults", "listings"]:
            if key in data and isinstance(data[key], list) and data[key]:
                found = _find_search_results(data[key], depth + 1)
                if found:
                    return found
        for v in data.values():
            if isinstance(v, (dict, list)):
                found = _find_search_results(v, depth + 1)
                if found:
                    return found
    return []


def _parse_result(item: dict) -> Optional[dict]:
    try:
        demand = item.get("demandStayListing") or {}

        b64_id = demand.get("id", "")
        if b64_id:
            decoded = base64.b64decode(b64_id).decode("utf-8", errors="ignore")
            lid = decoded.split(":")[-1].strip()
        else:
            lid = str(item.get("id") or "")
        if not lid:
            return None

        name_raw = item.get("nameLocalized") or item.get("title") or ""
        if isinstance(name_raw, dict):
            name = name_raw.get("localizedStringWithTranslationPreference") or name_raw.get("string") or ""
        else:
            name = str(name_raw)
        if not name:
            return None

        coord = _dig(demand, ["location", "coordinate"]) or {}
        lat = coord.get("latitude")
        lng = coord.get("longitude")

        price_str = (
            _dig(item, ["structuredDisplayPrice", "primaryLine", "discountedPrice"])
            or _dig(item, ["structuredDisplayPrice", "primaryLine", "price"])
        )
        price = _extract_price(price_str)

        rating_raw = item.get("avgRatingLocalized") or item.get("avgRating") or ""
        if isinstance(rating_raw, str):
            rm = re.search(r"[\d.]+", rating_raw)
            rating = float(rm.group()) if rm else None
        else:
            rating = float(rating_raw) if rating_raw else None

        pics = item.get("contextualPictures") or []
        pic = pics[0].get("picture") if pics else None

        review_text = item.get("avgRatingA11yLabel") or ""
        rv = re.search(r"후기\s*([\d,]+)개", review_text)
        review_count = int(rv.group(1).replace(",", "")) if rv else 0

        return {
            "id": lid,
            "platform": "airbnb",
            "name": name,
            "lat": float(lat) if lat else None,
            "lng": float(lng) if lng else None,
            "price": price,
            "rating": rating,
            "review_count": review_count,
            "room_type": "",
            "url": f"https://www.airbnb.com/rooms/{lid}",
            "image": pic,
            "occupancy_rate": None,
        }
    except Exception:
        return None


def _dig(data, keys):
    for k in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(k)
        if data is None:
            return None
    return data


def _extract_price(raw) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str):
        m = re.search(r"[\d,]+", raw.replace("₩", "").replace("KRW", "").strip())
        return int(m.group().replace(",", "")) if m else None
    return None


async def get_occupancy_rate(listing_id: str, months: int = 3) -> dict:
    """조회일 기준 전월 포함 총 2개월 예약율 계산"""
    today = date.today()
    # 전달 1일부터 시작
    if today.month == 1:
        start_month, start_year = 12, today.year - 1
    else:
        start_month, start_year = today.month - 1, today.year

    url = "https://www.airbnb.com/api/v3/PdpAvailabilityCalendar"
    variables = {
        "request": {
            "count": 3,  # 전달 + 이번달 + 다음달
            "listingId": str(listing_id),
            "month": start_month,
            "year": start_year,
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

    # 조회 기간: 오늘 -30일 ~ +30일
    window_start = today - timedelta(days=30)
    window_end = today + timedelta(days=30)

    total_days = booked_days = 0
    try:
        async with httpx.AsyncClient(timeout=15, headers=CAL_HEADERS) as client:
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
                        if window_start <= day_date <= window_end:
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
