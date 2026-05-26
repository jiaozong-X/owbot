"""
renderer.py - Chrome screenshot with extra render budget and Pillow crop/validation.
"""

import asyncio
import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from config import CHROME_PATH, SCREENSHOT_WIDTH

SCREENSHOT_CONCURRENCY = 2
SCREENSHOT_HEIGHT = 12000
SCREENSHOT_TIMEOUT_SECONDS = 45
SCREENSHOT_VIRTUAL_TIME_BUDGET_MS = 5000
SCREENSHOT_RETRY_BACKOFF_SECONDS = (0.8, 1.6)

_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(SCREENSHOT_CONCURRENCY)
    return _semaphore


def _do_screenshot(file_url: str, tmp_path: str) -> None:
    """Synchronously invoke Chrome screenshot in a thread pool."""
    profile_dir = tempfile.mkdtemp(prefix="chrome_render_", dir=tempfile.gettempdir())
    try:
        subprocess.run(
            [
                CHROME_PATH,
                f"--user-data-dir={profile_dir}",
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-software-rasterizer",
                "--hide-scrollbars",
                "--disable-web-security",
                "--allow-file-access-from-files",
                "--run-all-compositor-stages-before-draw",
                "--disable-extensions",
                "--no-first-run",
                "--no-default-browser-check",
                f"--virtual-time-budget={SCREENSHOT_VIRTUAL_TIME_BUDGET_MS}",
                f"--window-size={SCREENSHOT_WIDTH},{SCREENSHOT_HEIGHT}",
                f"--screenshot={tmp_path}",
                file_url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=SCREENSHOT_TIMEOUT_SECONDS,
            check=True,
        )
        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            raise RuntimeError("Chrome did not produce a screenshot")
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)


def _crop_and_validate(data: bytes) -> bytes:
    """Trim bottom whitespace and reject obviously broken black screenshots."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    pixels = img.load()

    sample = [
        pixels[x, y]
        for x in range(0, w, max(1, w // 8))
        for y in range(0, h, max(1, h // 8))
    ]
    black = sum(1 for p in sample if p == (0, 0, 0))
    if black > len(sample) * 0.9:
        raise RuntimeError(f"截图疑似黑屏（{black}/{len(sample)} 采样点为全黑）")

    if h <= 1000:
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=False)
        return buf.getvalue()

    bg = pixels[0, 0]

    def is_bg(pixel):
        return all(abs(pixel[i] - bg[i]) <= 8 for i in range(3))

    crop_h = h
    for y in range(h - 1, 0, -1):
        row = [pixels[x, y] for x in range(0, w, max(1, w // 80))]
        if any(not is_bg(p) for p in row):
            crop_h = y + 1
            break

    crop_h = min(crop_h + 20, h)
    if crop_h < h:
        img = img.crop((0, 0, w, crop_h))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


async def html_to_image(html_path: str) -> bytes:
    abs_path = os.path.abspath(html_path)
    file_url = Path(abs_path).resolve().as_uri()
    sem = _get_semaphore()
    loop = asyncio.get_running_loop()

    for attempt in range(3):
        fd, tmp = tempfile.mkstemp(suffix=".png", dir=tempfile.gettempdir())
        os.close(fd)
        async with sem:
            try:
                await loop.run_in_executor(None, _do_screenshot, file_url, tmp)
                with open(tmp, "rb") as f:
                    raw = f.read()
                return await loop.run_in_executor(None, _crop_and_validate, raw)
            except Exception as e:
                print(f"[renderer] 截图失败（attempt {attempt + 1}）：{e}")
                if attempt == 2:
                    raise
                backoff = SCREENSHOT_RETRY_BACKOFF_SECONDS[
                    min(attempt, len(SCREENSHOT_RETRY_BACKOFF_SECONDS) - 1)
                ]
                await asyncio.sleep(backoff)
            finally:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
