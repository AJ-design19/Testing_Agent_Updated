"""
Canvas Document Downloader.

After SAI completes generating a document in the canvas right-panel Output tab
(PRD, BRD, architecture doc, runbook, etc.), this module:
  1. Navigates to the Output sub-tab of the active agent.
  2. Waits for the "Download" button to appear (top-right of the Output tab,
     next to the "Version 1" chip — exactly as seen in the SAI screenshot).
  3. Clicks it via real Playwright mouse events and intercepts the file.
  4. Falls back to blob-URL capture if the file opens in a new tab.
  5. Returns the local saved path.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

ARTIFACTS_DIR = "artifacts"

# Selectors for the Download button in the canvas right-panel Output tab.
# Priority order: most specific → most general.
_DOWNLOAD_SELECTORS = [
    # The Download button sits next to "Version 1" in the Output tab toolbar
    'button:has-text("Download")',
    '[aria-label*="Download" i]',
    '[aria-label*="download" i]',
    '[title*="Download" i]',
    '[title*="download" i]',
    '[data-testid*="download"]',
    '[data-testid*="export"]',
    'button:has-text("Export")',
    '[class*="download"]:not(script)',
    '[class*="export"]:not(script)',
    'a[download]',
    'a[href$=".pdf"]',
    'a[href$=".docx"]',
]

# Right-panel canvas container — the Download button lives inside here
_CANVAS_PANEL_SELECTORS = [
    '[class*="right-panel"]',
    '[class*="RightPanel"]',
    '[class*="canvas-panel"]',
    '[class*="CanvasPanel"]',
    '[data-testid="canvas-panel"]',
    'aside',
]


class BRDDownloader:
    """
    Downloads a SAI-generated canvas document (PRD, BRD, architecture doc, etc.)
    by clicking the Download button in the canvas Output tab toolbar.
    """

    def __init__(self, page, run_id: str = "", screenshot_agent=None):
        self.page = page
        self.run_id = run_id
        self.ss = screenshot_agent
        os.makedirs(ARTIFACTS_DIR, exist_ok=True)

    async def download(self, timeout_seconds: int = 60) -> Optional[str]:
        """
        Full sequence:
          1. Click the Output sub-tab to make sure the document is visible.
          2. Wait for the Download button to appear in the toolbar.
          3. Screenshot the canvas before clicking (evidence).
          4. Click Download and intercept the file.
          5. Return the saved local path, or None on failure.
        """
        logger.info("[Downloader] Starting canvas document download…")

        # Step 1: ensure Output tab is active so Download button appears
        await self._ensure_output_tab_active()

        # Step 2: wait for Download button
        btn_info = await self._wait_for_download_button(timeout_seconds=30)
        if not btn_info:
            logger.warning("[Downloader] No Download button found within 30 s")
            return None

        logger.info(
            "[Downloader] Download button found: '%s' at (%.0f, %.0f)",
            btn_info.get("text", "?"), btn_info.get("x", 0), btn_info.get("y", 0),
        )

        # Step 3: screenshot before download
        if self.ss:
            await self.ss.capture(label="doc_download_button_found", event_type="download")

        # Step 4: click and intercept
        path = await self._click_and_intercept(btn_info, timeout_seconds=timeout_seconds)

        if path:
            logger.info("[Downloader] Document saved: %s", path)
            if self.ss:
                await self.ss.capture(label="doc_downloaded", event_type="download")
        else:
            logger.warning("[Downloader] Download interception failed")

        return path

    # ── Ensure Output tab is active ───────────────────────────────────────────

    async def _ensure_output_tab_active(self) -> None:
        """Click the Output sub-tab in the canvas panel so the Download button appears."""
        for sel in [
            'button:has-text("Output")',
            '[role="tab"]:has-text("Output")',
            '[class*="tab"]:has-text("Output")',
        ]:
            try:
                el = self.page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    logger.info("[Downloader] Clicked Output tab: %s", sel)
                    await asyncio.sleep(1.0)
                    return
            except Exception:
                pass
        logger.debug("[Downloader] Output tab not found — proceeding anyway")

    # ── Wait for Download button ──────────────────────────────────────────────

    async def _wait_for_download_button(self, timeout_seconds: int = 30) -> Optional[dict]:
        """
        Poll until the Download button is visible in the canvas right panel.
        Scopes the search to the canvas right panel first so we never
        accidentally click a different Download button on the page.
        """
        deadline = asyncio.get_event_loop().time() + timeout_seconds

        while asyncio.get_event_loop().time() < deadline:

            # Strategy 1: search inside canvas panel only
            try:
                info = await self.page.evaluate("""() => {
                    // Find the canvas right panel
                    const panelSels = [
                        '[class*="right-panel"]', '[class*="RightPanel"]',
                        '[class*="canvas-panel"]', '[class*="CanvasPanel"]',
                        '[data-testid="canvas-panel"]', 'aside',
                    ];
                    let panel = null;
                    for (const s of panelSels) {
                        const el = document.querySelector(s);
                        if (el && el.offsetParent) { panel = el; break; }
                    }
                    const root = panel || document.body;

                    const kw = ['download', 'export'];
                    const candidates = Array.from(root.querySelectorAll(
                        'button, a, [role="button"]'
                    )).filter(el => {
                        if (!el.offsetParent) return false;
                        const r = el.getBoundingClientRect();
                        if (r.width < 8 || r.height < 8) return false;
                        const t = (
                            el.innerText || el.getAttribute('aria-label') ||
                            el.getAttribute('title') || ''
                        ).toLowerCase();
                        return kw.some(k => t.includes(k));
                    });
                    if (!candidates.length) return null;

                    // Pick the topmost-rightmost (toolbar position)
                    candidates.sort((a, b) => {
                        const ra = a.getBoundingClientRect();
                        const rb = b.getBoundingClientRect();
                        // prefer rightmost, then topmost
                        if (Math.abs(ra.right - rb.right) > 10) return rb.right - ra.right;
                        return ra.top - rb.top;
                    });
                    const btn = candidates[0];
                    const r = btn.getBoundingClientRect();
                    return {
                        text: (btn.innerText || btn.getAttribute('aria-label') || 'Download').trim(),
                        x: r.left + r.width  / 2,
                        y: r.top  + r.height / 2,
                        top:  r.top,
                        left: r.left,
                        selector: null,
                    };
                }""")
                if info:
                    return info
            except Exception:
                pass

            # Strategy 2: Playwright locators scoped to canvas panel
            for panel_sel in _CANVAS_PANEL_SELECTORS:
                try:
                    panel = self.page.locator(panel_sel).first
                    if await panel.count() == 0 or not await panel.is_visible():
                        continue
                    for dl_sel in _DOWNLOAD_SELECTORS:
                        try:
                            el = panel.locator(dl_sel).first
                            if await el.count() > 0 and await el.is_visible():
                                bbox = await el.bounding_box()
                                if bbox:
                                    return {
                                        "selector": f"{panel_sel} {dl_sel}",
                                        "text": (await el.inner_text()).strip(),
                                        "x": bbox["x"] + bbox["width"]  / 2,
                                        "y": bbox["y"] + bbox["height"] / 2,
                                        "top":  bbox["y"],
                                        "left": bbox["x"],
                                    }
                        except Exception:
                            pass
                except Exception:
                    pass

            await asyncio.sleep(1.5)

        return None

    # ── Click and intercept download ──────────────────────────────────────────

    async def _click_and_intercept(
        self, btn_info: dict, timeout_seconds: int = 60
    ) -> Optional[str]:
        """
        Click the Download button and intercept the file.
        Handles two patterns:
          A. Direct download — browser fires a download event immediately.
          B. Dropdown — clicking opens a menu (PDF / DOCX / etc.); we click
             the first downloadable option in the dropdown.
        """
        ts        = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        dest_path = os.path.join(ARTIFACTS_DIR, f"{self.run_id}_doc_{ts}.pdf")

        # Attempt A: direct download interception
        try:
            async with self.page.expect_download(timeout=15_000) as dl_info:
                await self._do_click(btn_info)
            download = await dl_info.value
            suggested = download.suggested_filename or "document.pdf"
            # Preserve original extension
            ext = os.path.splitext(suggested)[1] or ".pdf"
            dest_path = dest_path.replace(".pdf", ext)
            await download.save_as(dest_path)
            logger.info("[Downloader] Direct download saved: %s", dest_path)
            return dest_path
        except Exception as e:
            logger.debug("[Downloader] Direct download did not fire (%s) — trying dropdown", e)

        # Attempt B: dropdown appeared after click — find a PDF/doc option and click it
        try:
            await self._do_click(btn_info)
            await asyncio.sleep(0.6)  # wait for dropdown to render

            dropdown_option = await self.page.evaluate("""() => {
                const kw = ['pdf', 'docx', 'word', 'download', 'export'];
                // Dropdown items are often in a popover/menu above the button
                const candidates = Array.from(document.querySelectorAll(
                    '[role="menuitem"], [role="option"], li, button, a'
                )).filter(el => {
                    if (!el.offsetParent) return false;
                    const t = (el.innerText || el.getAttribute('aria-label') || '').toLowerCase();
                    return kw.some(k => t.includes(k));
                });
                if (!candidates.length) return null;
                const r = candidates[0].getBoundingClientRect();
                return { x: r.left + r.width/2, y: r.top + r.height/2,
                         text: (candidates[0].innerText||'').trim() };
            }""")

            if dropdown_option:
                logger.info("[Downloader] Dropdown option found: '%s'", dropdown_option.get("text"))
                async with self.page.expect_download(timeout=timeout_seconds * 1000) as dl_info:
                    await self.page.mouse.click(
                        dropdown_option["x"], dropdown_option["y"]
                    )
                download = await dl_info.value
                suggested = download.suggested_filename or "document.pdf"
                ext = os.path.splitext(suggested)[1] or ".pdf"
                dest_path = dest_path.replace(".pdf", ext)
                await download.save_as(dest_path)
                logger.info("[Downloader] Dropdown download saved: %s", dest_path)
                return dest_path
        except Exception as e:
            logger.warning("[Downloader] Dropdown download failed: %s", e)

        # Fallback: blob / new-tab capture
        return await self._fallback_blob_capture(btn_info, dest_path)

    async def _do_click(self, btn_info: dict) -> None:
        """Click the Download button using real Playwright pointer events."""
        sel = btn_info.get("selector")
        x, y = btn_info.get("x", 0), btn_info.get("y", 0)

        if sel:
            try:
                el = self.page.locator(sel).first
                if await el.count() > 0:
                    await el.scroll_into_view_if_needed()
                    await el.hover()
                    await asyncio.sleep(0.15)
                    await el.click()
                    logger.info("[Downloader] Clicked via selector: %s", sel)
                    return
            except Exception:
                pass

        await self.page.mouse.move(x, y)
        await asyncio.sleep(0.1)
        await self.page.mouse.click(x, y)
        logger.info("[Downloader] Clicked via coordinates (%.0f, %.0f)", x, y)

    async def _fallback_blob_capture(
        self, btn_info: dict, dest_path: str
    ) -> Optional[str]:
        """
        Fallback: intercept blob: or data: URLs opened after clicking download.
        Captures PDF bytes via JS and writes them to disk.
        """
        logger.info("[BRDDownloader] Trying blob/data URL fallback…")

        # Listen for new pages (PDF opened in new tab)
        new_page_future: asyncio.Future = asyncio.get_event_loop().create_future()

        def _on_page(page):
            if not new_page_future.done():
                new_page_future.set_result(page)

        self.page.context.on("page", _on_page)

        try:
            await self._do_click(btn_info)
            try:
                new_page = await asyncio.wait_for(
                    asyncio.shield(new_page_future), timeout=15.0
                )
                await new_page.wait_for_load_state("domcontentloaded", timeout=15_000)
                url = new_page.url
                logger.info("[BRDDownloader] New page URL: %s", url)

                if url.startswith("blob:") or url.endswith(".pdf") or "pdf" in url.lower():
                    # Read PDF bytes from the new page via fetch
                    pdf_bytes_b64 = await new_page.evaluate("""async (url) => {
                        const r = await fetch(url);
                        const buf = await r.arrayBuffer();
                        const bytes = new Uint8Array(buf);
                        let bin = '';
                        for (const b of bytes) bin += String.fromCharCode(b);
                        return btoa(bin);
                    }""", url)
                    import base64
                    pdf_bytes = base64.b64decode(pdf_bytes_b64)
                    with open(dest_path, "wb") as f:
                        f.write(pdf_bytes)
                    logger.info("[BRDDownloader] Blob PDF captured: %s", dest_path)
                    await new_page.close()
                    return dest_path

                await new_page.close()
            except asyncio.TimeoutError:
                logger.warning("[BRDDownloader] No new page appeared within 15 s")
        finally:
            self.page.context.remove_listener("page", _on_page)

        return None
