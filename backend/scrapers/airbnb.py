import re
import json
import asyncio
import logging
import httpx
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

API_KEY = "d306zoyjsyarp7ifhu67rjxn52tv0t20"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
    "X-Airbnb-API-Key": API_KEY,
    "Referer": "https://www.airbnb.com/",
}


async def search_nearby(lat: float, lng: float, radius_km: float = 2.0) -> list[dict]:
    delta_lat = radius_km / 111.0
    delta_lng = radius_km / 88.0
    checkin = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")
    checkout = (date.today() + timedelta(days=8)).strftime("%Y-%m-%d")

    url = "https://www.airbnb.com/api/v2/explore_tabs"
    params = {
        "key": API_KEY,
        "currency": "KRW",
        "locale": "ko",
        "ne_lat": lat + delta_lat,
        "ne_lng": lng + delta_lng,
        "sw_lat": lat - delta_lat,
        "sw_lng": lng - delta_lng,
        "search_by_map": "true",
        "checkin": checkin,
        "checkout": checkout,
        "adults": 2,
        "items_per_grid": 40,
        "version": "1.8.6",
        "satori_version": "1.1.8",
        "screen_size": "large",
        "query_type": "filter_change",
        "tab_id": "home_tab",
        "search_type": "unknown",
    }

    listings = []
    try:
        async with httpx.AsyncClient(timeout=20, headers=HEADERS, follow_redirects=True) as client:
            r = await client.get(url, params=params)
            logger.info(f"[airbnb] API 응답: {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                tabs = data.get("explore_tabs", [])
                for tab in tabs:
                    for section in tab.get("sections", []):
                        for item in section.get("listings", []):
                            parsed = _parse_listing(
                                item.get("listing", {}),
                                item.get("pricing_quote", {}),
                            )
                            if parsed:
                                listings.append(parsed)
            else:
                logger.warning(f"[airbnb] API 오류: {r.status_code} {r.text[:200]}")
    except Exception as e:
        logger.error(f"[airbnb] 요청 실패: {e}")

    logger.info(f"[airbnb] {len(listings)}개 결과")
    return listings


def _parse_listing(listing: dict, pricing: dict) -> Optional[dict]:
    try:
        lid = str(listing.get("id", ""))
        name = listing.get("name", "")
        if not lid or not name:
            return None

        lat = listing.get("lat")
        lng = listing.get("lng")

        price_raw = (
            pricing.get("rate", {}).get("amount")
            or pricing.get("price", {}).get("total", {}).get("amount")
        )
        price = int(float(str(price_raw))) if price_raw else None

        rating = listing.get("avg_rating") or listing.get("star_rating")
        review_count = listing.get("reviews_count", 0)
        pic = listing.get("picture_url") or listing.get("xl_picture_url")
        room_type = listing.get("room_type_category", "")

        return {
            "id": lid,
            "platform": "airbnb",
            "name": name,
            "lat": float(lat) if lat else None,
            "lng": float(lng) if lng else None,
            "price": price,
            "rating": float(rating) if rating else None,
            "review_count": int(review_count) if review_count else 0,
            "room_type": room_type,
            "url": f"https://www.airbnb.com/rooms/{lid}",
            "image": pic,
            "occupancy_rate": None,
        }
    except Exception:
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
        async with httpx.AsyncClient(timeout=15, headers=HEADERS) as client:
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
