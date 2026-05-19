"""공유 Playwright 브라우저 인스턴스 관리"""
from playwright.async_api import async_playwright, Browser, BrowserContext
from typing import Optional

_playwright = None
_browser: Optional[Browser] = None


UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


async def get_browser() -> Browser:
    global _playwright, _browser
    if _browser is None or not _browser.is_connected():
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-web-security",
                f"--user-agent={UA}",
            ],
        )
    return _browser


async def new_context() -> BrowserContext:
    browser = await get_browser()
    ctx = await browser.new_context(
        user_agent=UA,
        locale="ko-KR",
        viewport={"width": 1280, "height": 800},
        # Client Hints 숨기기
        extra_http_headers={
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
        },
    )
    # webdriver 플래그 숨기기
    await ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
        Object.defineProperty(navigator, 'languages', {get: () => ['ko-KR', 'ko', 'en-US']});
        window.chrome = {runtime: {}};
    """)
    return ctx


async def shutdown():
    global _browser, _playwright
    if _browser:
        await _browser.close()
        _browser = None
    if _playwright:
        await _playwright.stop()
        _playwright = None
