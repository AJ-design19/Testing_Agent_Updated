"""
CanvasZoomer — canvas panel screenshots, text extraction, and zoom.

Zoom is applied by dispatching a synthetic WheelEvent with ctrlKey=true
directly on the canvas DOM element via page.evaluate().  This fires only
the element's own JavaScript event listeners (the canvas zoom widget) and
never reaches the browser's native Ctrl+scroll page-zoom handler, so the
rest of the page is completely unaffected.
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

CANVAS_PANEL_SELECTORS = [
    '[data-testid="canvas-panel"]',
    '[data-testid="flow-view"]',
    '[class*="canvas-panel"]',
    '[class*="CanvasPanel"]',
    '[class*="right-panel"]',
    '[class*="RightPanel"]',
    '[class*="flow-view"]',
    '[class*="FlowView"]',
    'aside',
]


class CanvasZoomer:
    """
    Async context manager for canvas panel screenshots, text extraction, and zoom.
    Zoom uses Ctrl+WheelUp mouse events scoped to the canvas element only.
    """

    def __init__(self, page, dpr: float = 1.0):
        self.page = page

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def screenshot_panel(self, path: str) -> Optional[str]:
        selector = await self._best_canvas_selector()
        if selector:
            try:
                el = await self.page.query_selector(selector)
                if el and await el.is_visible():
                    await el.screenshot(path=path)
                    return path
            except Exception as e:
                logger.debug("[CanvasZoomer] Element screenshot failed: %s", e)
        try:
            await self.page.screenshot(path=path, full_page=False)
            return path
        except Exception:
            return None

    async def screenshot_element(self, selector: str, path: str) -> Optional[str]:
        try:
            el = await self.page.query_selector(selector)
            if el and await el.is_visible():
                await el.screenshot(path=path)
                return path
        except Exception as e:
            logger.debug("[CanvasZoomer] Element screenshot (%s): %s", selector, e)
        return None

    async def read_text(self, selector: Optional[str] = None) -> str:
        sel = selector or await self._best_canvas_selector()
        if not sel:
            return ""
        try:
            return (await self.page.inner_text(sel) or "").strip()
        except Exception:
            return ""

    async def screenshot_clip(self, bbox: dict, path: str) -> Optional[str]:
        try:
            await self.page.screenshot(
                path=path,
                clip={
                    "x":      max(0, bbox.get("x", 0)),
                    "y":      max(0, bbox.get("y", 0)),
                    "width":  bbox.get("width", 200),
                    "height": bbox.get("height", 100),
                },
            )
            return path
        except Exception as e:
            logger.debug("[CanvasZoomer] Clip screenshot failed: %s", e)
            return None

    @staticmethod
    async def zoom_canvas(
        page, selector: Optional[str] = None, ticks: int = 3, smooth: bool = True
    ) -> bool:
        """Convenience static method: zoom the canvas panel with Ctrl+WheelUp."""
        async with CanvasZoomer(page) as cz:
            return await cz.zoom_into_canvas(selector=selector, ticks=ticks, smooth=smooth)

    @staticmethod
    async def screenshot_zoomed(page, selector: str, path: str, dpr: float = 1.0) -> Optional[str]:
        async with CanvasZoomer(page) as cz:
            return await cz.screenshot_element(selector, path)

    @staticmethod
    async def read_full_text(page, selector: str, dpr: float = 1.0) -> str:
        async with CanvasZoomer(page) as cz:
            return await cz.read_text(selector)

    @staticmethod
    async def screenshot_panel_zoomed(page, path: str, dpr: float = 1.0) -> Optional[str]:
        async with CanvasZoomer(page) as cz:
            return await cz.screenshot_panel(path)

    async def zoom_into_canvas(
        self,
        selector: Optional[str] = None,
        ticks: int = 3,
        smooth: bool = True,
    ) -> bool:
        """
        Zoom IN on the canvas panel using synthetic WheelEvents (deltaY < 0).

        Dispatching through page.evaluate() fires the event directly on the
        canvas DOM element's own listener chain.  The browser's native
        Ctrl+scroll page-zoom only triggers on real hardware input — synthetic
        events never reach it, so the rest of the page is unaffected.
        """
        return await self._dispatch_wheel(selector, ticks, delta_y=-120, smooth=smooth)

    async def zoom_out_canvas(
        self,
        selector: Optional[str] = None,
        ticks: int = 3,
        smooth: bool = True,
    ) -> bool:
        """Zoom OUT on the canvas panel (reverse of zoom_into_canvas)."""
        return await self._dispatch_wheel(selector, ticks, delta_y=120, smooth=smooth)

    async def screenshot_panel_after_zoom(
        self,
        path: str,
        selector: Optional[str] = None,
        zoom_ticks: int = 3,
    ) -> Optional[str]:
        """
        Full sequence:
          1. Click canvas panel to focus it
          2. Zoom in (Ctrl+scroll × zoom_ticks)
          3. Wait for render
          4. Screenshot the canvas panel element
          5. Zoom back out (same number of ticks)
        Returns saved path or None.
        """
        sel = selector or await self._best_canvas_selector()
        if not sel:
            logger.warning("[CanvasZoomer] screenshot_panel_after_zoom: no canvas selector")
            return await self.screenshot_panel(path)

        # 1. Click to focus the canvas panel
        try:
            el = self.page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.click()
                await asyncio.sleep(0.15)
        except Exception:
            pass

        # 2. Zoom in
        await self.zoom_into_canvas(selector=sel, ticks=zoom_ticks, smooth=True)
        await asyncio.sleep(0.4)   # wait for canvas widget to finish re-rendering

        # 3. Screenshot the element
        result = await self.screenshot_element(sel, path)
        if not result:
            result = await self.screenshot_panel(path)

        # 4. Zoom back out
        await self.zoom_out_canvas(selector=sel, ticks=zoom_ticks, smooth=True)
        await asyncio.sleep(0.2)

        if result:
            logger.info("[CanvasZoomer] Zoomed screenshot saved: %s", path)
        return result

    async def _dispatch_wheel(
        self,
        selector: Optional[str],
        ticks: int,
        delta_y: int,
        smooth: bool,
    ) -> bool:
        sel = selector or await self._best_canvas_selector()
        if not sel:
            return False
        try:
            el = await self.page.query_selector(sel)
            if not el or not await el.is_visible():
                return False
            bbox = await el.bounding_box()
            if not bbox:
                return False
            cx = bbox["x"] + bbox["width"]  / 2
            cy = bbox["y"] + bbox["height"] / 2

            for i in range(ticks):
                dispatched = await self.page.evaluate(
                    """([selector, clientX, clientY, deltaY]) => {
                        const el = document.querySelector(selector);
                        if (!el) return false;
                        el.dispatchEvent(new WheelEvent('wheel', {
                            bubbles: true, cancelable: true,
                            ctrlKey: true,
                            deltaY:  deltaY,
                            deltaMode: 0,
                            clientX: clientX,
                            clientY: clientY,
                        }));
                        return true;
                    }""",
                    [sel, cx, cy, delta_y],
                )
                if not dispatched:
                    return False
                if smooth and i < ticks - 1:
                    await asyncio.sleep(0.08)

            direction = "in" if delta_y < 0 else "out"
            logger.info("[CanvasZoomer] Zoomed %s (%s) × %d ticks", direction, sel, ticks)
            return True
        except Exception as e:
            logger.debug("[CanvasZoomer] _dispatch_wheel failed: %s", e)
            return False

    async def _best_canvas_selector(self) -> Optional[str]:
        for sel in CANVAS_PANEL_SELECTORS:
            try:
                el = await self.page.query_selector(sel)
                if el and await el.is_visible():
                    return sel
            except Exception:
                continue
        return None
