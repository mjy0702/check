import re
import json
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


async def search_nearby(lat: float, lng: float, radius_km: float = 2.0) -> list[dict]:
    delta_lat = radius_km / 111.0
    delta_lng = radius_km / 88.0
    checkin = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")
    checkout = (date.today() + timedelta(days=8)).strftime("%Y-%m-%d")

    url = (
        "https://www.airbnb.com/s/Seoul--South-Korea/homes"
        f"?ne_lat={lat+delta_lat:.6f}&ne_lng={lng+delta_lng:.6f}"
        f"&sw_lat={lat-delta_lat:.6f}&sw_lng={lng-delta_lng:.6f}"
        f"&search_by_map=true&checkin={checkin}&checkout={checkout}"
        f"&adults=2&items_per_grid=40&tab_id=home_tab"
    )

    try:
        async with httpx.AsyncClient(
            timeout=25, headers=SEARCH_HEADERS, follow_redirects=True
        ) as client:
            r = await client.get(url)
            logger.info(f"[airbnb] HTML status={r.status_code}, size={len(r.text)}")
            if r.status_code != 200:
                logger.warning(f"[airbnb] 비정상 응답: {r.status_code}")
                return []

            listings = _parse_html(r.text)
            logger.info(f"[airbnb] {len(listings)}개 파싱")
            return listings
    except Exception as e:
        logger.error(f"[airbnb] fetch 실패: {e}")
        return []


def _parse_html(html: str) -> list[dict]:
    # staysSearch → searchResults 직접 추출 (균형 브래킷 파서)
    results = _extract_stays_search(html)
    if results:
        logger.info(f"[airbnb] staysSearch에서 {len(results)}개 발견")
        return results

    # __NEXT_DATA__ fallback
    m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        m = re.search(r'<script[^>]*type="application/json"[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            found = _find_search_results(data)
            if found:
                logger.info(f"[airbnb] __NEXT_DATA__에서 {len(found)}개 발견")
                return [r for r in (_parse_result(x) for x in found) if r]
        except Exception as e:
            logger.warning(f"[airbnb] __NEXT_DATA__ 파싱 실패: {e}")

    if len(html) < 10000:
        logger.warning(f"[airbnb] 페이지 짧음({len(html)}자) - 봇 차단 가능성")
    else:
        logger.warning(f"[airbnb] 파싱 실패 (HTML {len(html)}자)")
    return []


def _extract_stays_search(html: str) -> list[dict]:
    """staysSearch 키에서 searchResults 배열을 직접 추출."""
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
    """start 위치에서 시작하는 JSON 배열을 균형 브래킷으로 추출."""
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
        if isinstance(first, dict) and ("listing" in first or "id" in first):
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


def _dig(data, keys):
    for k in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(k)
        if data is None:
            return None
    return data


def _parse_result(item: dict) -> Optional[dict]:
    try:
        # 최신 Airbnb 구조: 플랫 객체, listing 키 없음
        demand = item.get("demandStayListing") or {}

        # ID: demandStayListing.id 는 base64 → 숫자 추출
        import base64 as _b64
        b64_id = demand.get("id", "")
        if b64_id:
            decoded = _b64.b64decode(b64_id).decode("utf-8", errors="ignore")
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

        # 좌표
        coord = _dig(demand, ["location", "coordinate"]) or {}
        lat = coord.get("latitude")
        lng = coord.get("longitude")

        # 가격: structuredDisplayPrice.primaryLine.discountedPrice 또는 price
        price_str = (
            _dig(item, ["structuredDisplayPrice", "primaryLine", "discountedPrice"])
            or _dig(item, ["structuredDisplayPrice", "primaryLine", "price"])
        )
        price = _extract_price(price_str)

        # 평점
        rating_raw = item.get("avgRatingLocalized") or item.get("avgRating") or ""
        if isinstance(rating_raw, str):
            rm = re.search(r"[\d.]+", rating_raw)
            rating = float(rm.group()) if rm else None
        else:
            rating = float(rating_raw) if rating_raw else None

        # 이미지
        pics = item.get("contextualPictures") or []
        pic = pics[0].get("picture") if pics else None

        # 리뷰 수 (avgRatingA11yLabel에서 추출)
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
