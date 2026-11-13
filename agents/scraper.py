from graph.dependencies import Scraper


class PlaywrightScraper(Scraper):
    """Reusable-browser scraper implementing the Scraper protocol."""

    def __init__(self, headless: bool = True, nav_timeout_ms: int = 30_000):
        self.headless = headless
        self.nav_timeout_ms = nav_timeout_ms
        self._pw = None
        self._browser = None

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def fetch(self, url: str) -> dict:
        context = await self._browser.new_context(locale="en-US")
        try:
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout_ms)
            try:
                await page.wait_for_load_state("networkidle", timeout=5_000)
            except Exception:
                pass  # Long-polling pages may never become idle.
            text = await page.evaluate("() => document.body ? document.body.innerText : ''")
            return {"final_url": page.url, "text": text}
        finally:
            await context.close()