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
    # __NEXT_DATA__ 시도
    m = re.search(
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html, re.DOTALL,
    )
    if m:
        try:
            data = json.loads(m.group(1))
            # 구조 디버깅용 키 로그
            pp = data.get("props", {}).get("pageProps", {})
            logger.info(f"[airbnb] pageProps keys: {list(pp.keys())[:10]}")

            # 가능한 경로들 순서대로 시도
            candidates = [
                ["props", "pageProps", "staysSearch", "results", "searchResults"],
                ["props", "pageProps", "initialData", "staysSearch", "results", "searchResults"],
                ["props", "pageProps", "data", "staysSearch", "results", "searchResults"],
                ["props", "pageProps", "bootstrapData", "reduxData", "homePDP", "listing"],
            ]
            for path in candidates:
                results = _dig(data, path)
                if results and isinstance(results, list) and len(results) > 0:
                    logger.info(f"[airbnb] 경로 {path[-2:]}에서 {len(results)}개 발견")
                    return [r for r in (_parse_result(x) for x in results) if r]

            # niobeMinimalClientData 패턴 (최신 Airbnb)
            niobe_m = re.search(r'data-deferred-state[^>]*>(.*?)</script>', html, re.DOTALL)
            if niobe_m:
                nd = json.loads(niobe_m.group(1))
                results = _find_search_results(nd)
                if results:
                    logger.info(f"[airbnb] deferred-state에서 {len(results)}개 발견")
                    return [r for r in (_parse_result(x) for x in results) if r]

        except Exception as e:
            logger.warning(f"[airbnb] __NEXT_DATA__ 파싱 실패: {e}")

    # 정규식 fallback - 여러 키 패턴 시도
    for key in ["searchResults", "staySearchResults", "resultSections", "listings"]:
        m2 = re.search(rf'"{key}"\s*:\s*(\[.*?\])\s*[,}}]', html, re.DOTALL)
        if m2:
            try:
                results = json.loads(m2.group(1))
                parsed = [r for r in (_parse_result(x) for x in results[:40]) if r]
                if parsed:
                    logger.info(f"[airbnb] 정규식({key})에서 {len(parsed)}개 발견")
                    return parsed
            except Exception:
                pass

    if len(html) < 10000:
        logger.warning(f"[airbnb] 페이지 짧음({len(html)}자) - 봇 차단 가능성")
    else:
        # 진단: 실제 키 찾기
        keys_found = re.findall(r'"([a-zA-Z]{5,20}Search[a-zA-Z]*)"\s*:', html[:500000])
        logger.warning(f"[airbnb] 파싱 실패. HTML에서 발견된 Search 관련 키: {list(set(keys_found))[:10]}")

    return []


def _find_search_results(data, depth=0) -> list:
    """재귀적으로 searchResults 배열을 찾습니다."""
    if depth > 6:
        return []
    if isinstance(data, list) and len(data) > 2:
        first = data[0] if data else {}
        if isinstance(first, dict) and ("listing" in first or "id" in first):
            return data
    if isinstance(data, dict):
        for key in ["searchResults", "staySearchResults", "listings", "results"]:
            if key in data:
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
        listing = item.get("listing") or {}
        pricing = item.get("pricingQuote") or item.get("pricing_quote") or {}

        lid = str(listing.get("id") or item.get("id") or "")
        name = (
            listing.get("name") or listing.get("title")
            or item.get("name") or ""
        )
        if not lid or not name:
            return None

        coord = listing.get("coordinate") or {}
        lat = coord.get("latitude") or listing.get("lat")
        lng = coord.get("longitude") or listing.get("lng")

        price_raw = (
            _dig(pricing, ["structuredStayDisplayPrice", "primaryLine", "price"])
            or _dig(pricing, ["rate", "amount"])
            or _dig(pricing, ["price", "total", "amount"])
        )
        price = _extract_price(price_raw)

        rating_raw = (
            listing.get("avgRatingLocalized")
            or listing.get("avgRating")
            or listing.get("avg_rating")
        )
        if isinstance(rating_raw, str):
            rm = re.search(r"[\d.]+", rating_raw)
            rating = float(rm.group()) if rm else None
        else:
            rating = float(rating_raw) if rating_raw else None

        review_count = listing.get("reviewsCount") or listing.get("reviews_count") or 0
        pics = listing.get("contextualPictures") or []
        pic = pics[0].get("picture") if pics else listing.get("picture_url")
        room_type = listing.get("roomTypeCategory") or listing.get("room_type_category") or ""

        return {
            "id": lid,
            "platform": "airbnb",
            "name": name,
            "lat": float(lat) if lat else None,
            "lng": float(lng) if lng else None,
            "price": price,
            "rating": rating,
            "review_count": int(review_count) if review_count else 0,
            "room_type": room_type,
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
