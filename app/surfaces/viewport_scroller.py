"""
Viewport Scroller — scrolls a page (or a specific scrollable container) from
top to bottom and captures one screenshot per viewport height increment.

Usage:
    scroller = ViewportScroller(page, io_logger, run_id="SURF-03_AS")
    paths = await scroller.scroll_and_capture(step_id="AS-02", label="aia_tab")
"""

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class ViewportScroller:
    """
    Captures screenshots at every viewport-height increment while scrolling
    the page or a target container from top to bottom.
    """

    def __init__(self, page, io_logger=None, run_id: str = ""):
        self.page = page
        self.io_logger = io_logger
        self.run_id = run_id
        self._ss_dir = os.path.join("screenshots", "surfaces", run_id)
        os.makedirs(self._ss_dir, exist_ok=True)

    # ── Viewport dimensions ───────────────────────────────────────────────────

    async def _get_viewport(self) -> tuple[int, int]:
        try:
            dims = await self.page.evaluate(
                "() => ({ w: window.innerWidth, h: window.innerHeight })"
            )
            return dims["w"], dims["h"]
        except Exception:
            return 1280, 720

    async def _get_scroll_height(self, selector: Optional[str] = None) -> int:
        js = (
            f"() => document.querySelector('{selector}').scrollHeight"
            if selector
            else "() => document.body.scrollHeight"
        )
        try:
            return int(await self.page.evaluate(js)) or 720
        except Exception:
            return 720

    # ── Single screenshot helper ──────────────────────────────────────────────

    async def _screenshot(self, filename: str) -> str:
        path = os.path.join(self._ss_dir, filename)
        try:
            await self.page.screenshot(path=path, full_page=False)
        except Exception as e:
            logger.warning("[ViewportScroller] Screenshot failed (%s): %s", filename, e)
        return path

    # ── Public: scroll a scrollable container ────────────────────────────────

    async def scroll_and_capture(
        self,
        step_id: str,
        label: str = "scroll",
        selector: Optional[str] = None,
        pause_between_captures_ms: int = 400,
    ) -> list[str]:
        """
        Scroll from top to bottom, capturing one screenshot per viewport increment.
        Returns list of screenshot paths.

        Args:
            step_id:  the step ID used for file naming
            label:    short label appended to filenames
            selector: CSS selector for the scrollable container (default: window)
            pause_between_captures_ms: wait time between scroll + screenshot
        """
        _vw, vh = await self._get_viewport()
        scroll_h = await self._get_scroll_height(selector)

        logger.info("[Scroller] Scrolling '%s' — viewport_h=%d scroll_h=%d",
                    label, vh, scroll_h)

        # Scroll back to top first
        await self._scroll_to(0, selector)
        await asyncio.sleep(0.3)

        paths: list[str] = []
        idx = 0
        y = 0

        while y <= scroll_h + vh:
            filename = f"{step_id}_{label}_{idx:03d}_y{y}.png"
            p = await self._screenshot(filename)
            paths.append(p)
            if self.io_logger:
                self.io_logger.add_viewport_screenshot(p)
            logger.debug("[Scroller] Captured at y=%d: %s", y, filename)

            next_y = y + vh
            if next_y > scroll_h:
                # Capture bottom of page if we haven't yet
                if y < scroll_h:
                    await self._scroll_to(scroll_h, selector)
                    await asyncio.sleep(pause_between_captures_ms / 1000)
                    filename = f"{step_id}_{label}_{idx + 1:03d}_bottom.png"
                    p = await self._screenshot(filename)
                    paths.append(p)
                    if self.io_logger:
                        self.io_logger.add_viewport_screenshot(p)
                break

            await self._scroll_to(next_y, selector)
            await asyncio.sleep(pause_between_captures_ms / 1000)
            y = next_y
            idx += 1

        # Scroll back to top
        await self._scroll_to(0, selector)
        logger.info("[Scroller] Done — %d screenshots for '%s'", len(paths), label)
        return paths

    async def _scroll_to(self, y: int, selector: Optional[str] = None) -> None:
        try:
            if selector:
                await self.page.evaluate(
                    f"() => {{ const el = document.querySelector('{selector}'); "
                    f"if (el) el.scrollTop = {y}; }}"
                )
            else:
                await self.page.evaluate(f"() => window.scrollTo(0, {y})")
        except Exception as e:
            logger.debug("[Scroller] scrollTo(%d) error: %s", y, e)

    # ── Public: single full-page screenshot (no scroll) ───────────────────────

    async def capture_full_page(self, step_id: str, label: str = "full") -> str:
        filename = f"{step_id}_{label}_fullpage.png"
        path = os.path.join(self._ss_dir, filename)
        try:
            await self.page.screenshot(path=path, full_page=True)
            if self.io_logger:
                self.io_logger.add_viewport_screenshot(path)
            logger.info("[Scroller] Full-page screenshot: %s", path)
        except Exception as e:
            logger.warning("[Scroller] Full-page screenshot failed: %s", e)
        return path

    # ── Public: capture a specific DOM region ─────────────────────────────────

    async def capture_region(self, step_id: str, label: str, selectors: list[str]) -> Optional[str]:
        """
        Scroll the first matching element into view and screenshot just the
        viewport at that position.
        """
        for sel in selectors:
            try:
                el = self.page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.scroll_into_view_if_needed()
                    await asyncio.sleep(0.4)
                    filename = f"{step_id}_{label}_region.png"
                    p = await self._screenshot(filename)
                    if self.io_logger:
                        self.io_logger.add_viewport_screenshot(p)
                    return p
            except Exception:
                continue
        # Fallback: current viewport
        filename = f"{step_id}_{label}_viewport.png"
        return await self._screenshot(filename)
