import asyncio
import logging
import subprocess
import sys
from datetime import date, timedelta
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 앱 시작 전에 현재 Python(venv)으로 Chromium 설치 보장
try:
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True, text=True, timeout=300
    )
    if result.returncode == 0:
        logger.info("Playwright Chromium 설치 완료")
    else:
        logger.warning(f"Playwright install 경고: {result.stderr}")
except Exception as e:
    logger.error(f"Playwright install 실패: {e}")

from scrapers.geocoder import address_to_coords
from scrapers import airbnb, yanolja, booking, tripdotcom, agoda
from scrapers import browser as shared_browser

PLATFORM_MODULES = {
    "airbnb": airbnb,
    "yanolja": yanolja,
    "booking": booking,
    "tripdotcom": tripdotcom,
    "agoda": agoda,
}

app = FastAPI(title="숙소 공실율 분석 시스템")

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR / "static")), name="static")


@app.on_event("shutdown")
async def shutdown_browser():
    await shared_browser.shutdown()


@app.get("/")
async def root():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


class SearchRequest(BaseModel):
    address: str
    radius_km: float = 2.0
    platforms: list[str] = ["airbnb", "yanolja", "booking", "tripdotcom", "agoda"]
    fetch_occupancy: bool = True


@app.post("/api/search")
async def search_listings(req: SearchRequest):
    """주소 기반 주변 숙소 검색 (5개 플랫폼 통합)"""
    try:
        coords = await address_to_coords(req.address)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.info(f"검색 시작: {req.address} → {coords}, 반경 {req.radius_km}km, 플랫폼 {req.platforms}")

    # 플랫폼별 순차 검색 (Playwright 브라우저 부하 분산)
    results = []
    for p in req.platforms:
        if p not in PLATFORM_MODULES:
            continue
        try:
            result = await PLATFORM_MODULES[p].search_nearby(
                coords["lat"], coords["lng"], req.radius_km
            )
            logger.info(f"[{p}] {len(result)}개 결과")
            results.append(result)
        except Exception as e:
            logger.error(f"[{p}] 오류: {e}", exc_info=True)
            results.append([])

    all_listings = []
    for r in results:
        if isinstance(r, list):
            all_listings.extend(r)

    # 예약율 수집 (에어비앤비만 캘린더 데이터 지원)
    if req.fetch_occupancy:
        airbnb_listings = [l for l in all_listings if l.get("platform") == "airbnb"]
        other_listings = [l for l in all_listings if l.get("platform") != "airbnb"]
        airbnb_listings = await airbnb.enrich_with_occupancy(airbnb_listings)
        all_listings = airbnb_listings + other_listings

    # 같은 숙소가 여러 플랫폼에 있으면 그룹으로 묶기
    all_listings = _group_by_property(all_listings)

    return {
        "center": coords,
        "radius_km": req.radius_km,
        "total": len(all_listings),
        "listings": all_listings,
        "stats": _compute_stats(all_listings),
    }


@app.get("/api/occupancy/{listing_id}")
async def get_occupancy(
    listing_id: str,
    platform: str = Query(default="airbnb"),
    months: int = Query(default=3, ge=1, le=6),
):
    """개별 숙소 예약율/가용성 조회"""
    if platform == "airbnb":
        return await airbnb.get_occupancy_rate(listing_id, months=months)

    # Booking / Trip.com / Agoda: 내일~모레 가용성 체크
    checkin = date.today() + timedelta(days=1)
    checkout = date.today() + timedelta(days=2)
    if platform == "booking":
        return await booking.get_availability(listing_id, checkin, checkout)
    if platform == "tripdotcom":
        return await tripdotcom.get_availability(listing_id, checkin, checkout)
    if platform == "agoda":
        return await agoda.get_availability(listing_id, checkin, checkout)
    raise HTTPException(status_code=400, detail="지원하지 않는 플랫폼입니다.")


@app.get("/api/geocode")
async def geocode(address: str = Query(...)):
    """주소 → 좌표 변환"""
    try:
        return await address_to_coords(address)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _haversine_m(lat1, lng1, lat2, lng2) -> float:
    """두 좌표 사이 거리(미터)"""
    import math
    R = 6371000
    p = math.pi / 180
    a = (
        0.5 - math.cos((lat2 - lat1) * p) / 2
        + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lng2 - lng1) * p)) / 2
    )
    return 2 * R * math.asin(math.sqrt(a))


def _group_by_property(listings: list[dict], threshold_m: float = 300) -> list[dict]:
    """좌표가 threshold_m 이내인 다른 플랫폼 숙소를 하나의 그룹으로 묶습니다.
    그룹화된 숙소는 'cross_platforms' 필드에 각 플랫폼 정보를 담습니다.
    """
    used = [False] * len(listings)
    grouped = []

    for i, base in enumerate(listings):
        if used[i]:
            continue
        used[i] = True

        if not base.get("lat") or not base.get("lng"):
            grouped.append(base)
            continue

        matches = []
        for j, other in enumerate(listings):
            if used[j] or i == j:
                continue
            if not other.get("lat") or not other.get("lng"):
                continue
            if other.get("platform") == base.get("platform"):
                continue
            dist = _haversine_m(base["lat"], base["lng"], other["lat"], other["lng"])
            if dist <= threshold_m:
                matches.append((j, other, dist))

        if not matches:
            grouped.append(base)
            continue

        # 여러 플랫폼 매칭 → 그룹 생성
        for j, _, _ in matches:
            used[j] = True

        all_entries = [base] + [m[1] for m in matches]

        # 대표 이름: 한국어 이름 우선, 없으면 가장 긴 이름
        def name_score(e):
            n = e.get("name", "")
            korean = sum(1 for c in n if "가" <= c <= "힣")
            return (korean, len(n))
        rep = max(all_entries, key=name_score)

        cross = [
            {
                "platform": e["platform"],
                "id": e.get("id"),
                "price": e.get("price"),
                "url": e.get("url"),
                "rating": e.get("rating"),
                "review_count": e.get("review_count", 0),
                "occupancy_rate": e.get("occupancy_rate"),
                "name": e.get("name", ""),
            }
            for e in all_entries
        ]

        merged = {
            **rep,
            "name": rep.get("name", ""),
            "platform": rep["platform"],
            "cross_platforms": cross,
            "is_multi_platform": True,
            "platform_count": len(all_entries),
        }
        grouped.append(merged)

    return grouped


def _compute_stats(listings: list[dict]) -> dict:
    rates = [l["occupancy_rate"] for l in listings if l.get("occupancy_rate") is not None]
    prices = [l["price"] for l in listings if l.get("price")]
    by_platform: dict[str, int] = {}
    for l in listings:
        # cross_platforms가 있으면 각 플랫폼에 카운트
        for cp in l.get("cross_platforms", [{"platform": l.get("platform", "unknown")}]):
            p = cp.get("platform", "unknown")
            by_platform[p] = by_platform.get(p, 0) + 1

    return {
        "avg_occupancy_rate": round(sum(rates) / len(rates), 1) if rates else None,
        "avg_vacancy_rate": round(100 - sum(rates) / len(rates), 1) if rates else None,
        "max_occupancy_rate": max(rates) if rates else None,
        "min_occupancy_rate": min(rates) if rates else None,
        "avg_price_krw": round(sum(prices) / len(prices)) if prices else None,
        "by_platform": by_platform,
        "total_with_occupancy_data": len(rates),
    }
