"""
linkedin_scraper.py

Uses a persistent Chromium profile to maintain LinkedIn auth across runs.

FIRST-TIME SETUP (run once):
    python linkedin_scraper.py --login

This opens a headed browser so you can log into LinkedIn manually.
Your session is saved to PROFILE_DIR and reused automatically on all
future runs. When the session expires, the pipeline re-authenticates
automatically without any manual intervention.

NORMAL USE:
    Imported by job_scraper.py — no direct invocation needed.

ARCHITECTURE:
    Uses Playwright's **async** API running in a dedicated background thread.
    All browser operations happen inside one continuous asyncio event loop,
    eliminating greenlet OTID mismatches entirely.  Sync callers (like
    job_scraper.py) enqueue work via an async queue and block on a
    threading.Event until the result is ready — no ThreadPoolExecutor needed.
"""


import argparse
import asyncio
import atexit
import random
import sys
import threading
from pathlib import Path

from playwright.async_api import (
    BrowserContext,
    Page,
    Playwright as AsyncPlaywright,  # type: ignore[attr-defined]
    async_playwright,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROFILE_DIR = "linkedin_profile"
PAGE_SETTLE_MS = 4000
NAVIGATION_TIMEOUT_MS = 30_000

AUTH_BLOCKED_URLS = [
    "/login",
    "/authwall",
    "/checkpoint/",
    "/challenge/",
    "/uas/login",
]


# ---------------------------------------------------------------------------
# Browser lifecycle — runs in a dedicated background thread.
# All Playwright interactions happen inside this single asyncio event loop,
# so there are zero greenlet transitions between calls.
# ---------------------------------------------------------------------------

_request_queue: asyncio.Queue | None = None
_browser_thread: threading.Thread | None = None
_browser_ready = threading.Event()


def _launch_context(playwright, headless):
    if Path(PROFILE_DIR).exists():
        for item in Path(PROFILE_DIR).iterdir():
            if item.is_file() and "Lock" in str(item.name):
                try:
                    item.unlink(missing_ok=True)
                except Exception:
                    pass

    Path(PROFILE_DIR).mkdir(exist_ok=True)
    return playwright.chromium.launch_persistent_context(
        user_data_dir=PROFILE_DIR,
        headless=headless,
        locale="en-US",
        timezone_id="America/Los_Angeles",
        viewport={"width": 1280, "height": 800},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
        ],
        ignore_default_args=["--enable-automation"],
    )


# module-level handles so shutdown_browser can actually reach the live objects
_active_context: BrowserContext | None = None  # type: ignore[valid-type]
_active_playwright = None
_active_loop: asyncio.AbstractEventLoop | None = None
_active_page: Page | None = None  # type: ignore[valid-type]
_init_lock_sync = threading.Lock()


def _start_browser_thread_sync():
    global _request_queue, _browser_thread

    print("[LinkedIn] Starting persistent browser session …")

    def _run():
        global _active_context, _active_playwright, _active_loop, _request_queue

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _active_loop = loop

        queue: asyncio.Queue = asyncio.Queue()
        _request_queue = queue

        try:
            async def _main():
                global _active_context, _active_playwright
                async with async_playwright() as pw_instance:
                    context = await _launch_context(pw_instance, headless=False)
                    _active_playwright = pw_instance
                    _active_context = context
                    _browser_ready.set()
                    await _browser_worker(context, queue)

            loop.run_until_complete(_main())
        finally:
            _active_context = None
            _active_playwright = None
            _active_loop = None
            loop.close()

    _browser_thread = threading.Thread(target=_run, daemon=True)
    _browser_thread.start()


def _ensure_browser_running_sync():
    """Start the background browser thread if it hasn't been started yet. Thread-safe, sync."""
    global _browser_thread

    with _init_lock_sync:
        if _browser_thread is not None:
            _browser_ready.wait()
            return
        _start_browser_thread_sync()
    _browser_ready.wait()

def shutdown_browser():
    """Best-effort cleanup on process exit.

    Note: the real context lives on the background thread's own event loop,
    so we can't just call .close() from this (main) thread synchronously —
    BrowserContext.close() is a coroutine. We schedule it onto that loop if
    it's still running; if the thread/loop is already gone, there's nothing
    to clean up here (the OS will reap the process, and _launch_context's
    stale-lock removal handles the next run regardless).
    """
    global _active_context, _active_loop

    if _active_context is None or _active_loop is None:
        return

    try:
        if _active_loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(_active_context.close(), _active_loop)
            fut.result(timeout=5)
    except Exception as e:
        print(f"[LinkedIn] Shutdown warning: {e}")


atexit.register(shutdown_browser)


def _get_init_lock() -> asyncio.Lock:  # type: ignore[return-value]
    global _init_lock
    if _init_lock is None:
        _init_lock = asyncio.Lock()
    return _init_lock


async def _browser_worker(context, queue):
    """Main loop — processes scrape / login requests from the queue."""
    try:
        while True:
            req = await queue.get()
            try:
                await req._process(context)
            except Exception as exc:
                if not req.done():
                    req.set_exception(exc)
            finally:
                queue.task_done()
    finally:
        await context.close()


# ---------------------------------------------------------------------------
# Request wrapper — bridges sync callers into the async queue.
# Each request carries a threading.Event so the caller can block until done.
# ---------------------------------------------------------------------------

class _Request:
    """Wraps an async operation so a synchronous caller can wait on it."""

    __slots__ = ("_result", "_exc", "_done_flag")

    def __init__(self):
        self._result = None  # type: ignore[assignment]
        self._exc = None     # type: ignore[assignment]
        self._done_flag = threading.Event()

    async def _process(self, context):  # type: ignore[no-untyped-def]
        """Override in a subclass."""
        raise NotImplementedError

    def done(self) -> bool:
        return self._done_flag.is_set()

    def result(self):
        if not self.done():
            self._done_flag.wait()
        if self._exc is not None:
            raise self._exc
        return self._result

    def set_result(self, value):
        self._result = value
        self._done_flag.set()

    def set_exception(self, exc):
        self._exc = exc
        self._done_flag.set()


# ---------------------------------------------------------------------------
# Session rotation — creates a fresh tab and waits to avoid detection.
# ---------------------------------------------------------------------------


async def _rotate_session_impl(page: Page):  # type: ignore[name-defined]
    global _scrape_count, _max_scrapes_per_session

    if _scrape_count < _max_scrapes_per_session:
        return

    print(f"[LinkedIn] Rotating session after {_scrape_count} scrapes — cooling down …")
    cooldown = random.uniform(30, 90)
    print(f"[LinkedIn] Waiting {cooldown:.0f}s before new tab …")
    await asyncio.sleep(cooldown)

    _scrape_count = 0
    _max_scrapes_per_session = random.randint(5, 8)

# ---------------------------------------------------------------------------
# Scrape request — navigates to a job URL and extracts structured data.
# ---------------------------------------------------------------------------

_scrape_count = 0
_max_scrapes_per_session = random.randint(5, 8)


class _ScrapeRequest(_Request):
    def __init__(self, url: str, rotate_session: bool = False):
        super().__init__()
        self.url = url
        self.rotate_session = rotate_session

    async def _process(self, context):  # type: ignore[override, no-untyped-def]
        global _scrape_count
        global _active_page
        if _active_page is None or _active_page.is_closed():
            _active_page = await context.new_page()
        else:
            try:
                await _active_page.goto("about:blank", timeout=10_000)
            except Exception:
                try:
                    await _active_page.close()
                except Exception:
                    pass
                _active_page = await context.new_page()
        page = _active_page
        if self.rotate_session:
            await _rotate_session_impl(page)

        await _occasionally_visit_feed_async(page)
        await asyncio.sleep(random.uniform(3.0, 8.0))

        await page.goto(self.url, timeout=NAVIGATION_TIMEOUT_MS, wait_until="domcontentloaded")

        await asyncio.sleep(random.uniform(5.0, 14.0))
        await _human_mouse_move_async(page)
        await _human_scroll_async(page)
        await _human_interact_async(page)
        await _simulate_reading_async(page)
        await asyncio.sleep(PAGE_SETTLE_MS / 1000.0)

        if await _is_auth_blocked(page):
            raise Exception("LINKEDIN_AUTH_EXPIRED")

        raw_text = (await page.inner_text("body")).strip()
        title, company, location = _extract_from_body(raw_text)

        if len(raw_text) < 200:
            raise Exception(
                f"LINKEDIN_SCRAPE_FAILED — page content too short ({len(raw_text)} chars)"
            )

        desc = await _extract_description_async(page)

        job_data = {
            "url": self.url,
            "title": title,
            "company": company,
            "location": location,
            "description": desc,
            "raw_text": raw_text,
        }
        self.set_result(job_data)




# ---------------------------------------------------------------------------
# Login request — navigates to the LinkedIn login page and waits for auth.
# ---------------------------------------------------------------------------

class _LoginRequest(_Request):
    async def _process(self, context):  # type: ignore[override, no-untyped-def]
        page = await context.new_page()

        print("[AUTH] Session expired — navigating to LinkedIn login …")
        print("[AUTH] Enter your credentials in the existing browser window.")

        await page.goto("https://www.linkedin.com/login", timeout=NAVIGATION_TIMEOUT_MS)
        await page.wait_for_url(
            lambda url: "linkedin.com/login" not in str(url),
            timeout=120_000,
        )
        await asyncio.sleep(2.0)

        if await _is_auth_blocked(page):
            raise RuntimeError(
                "[AUTH] Still seeing a login page after redirect — login may have failed."
            )

        self.set_result(True)



# ---------------------------------------------------------------------------
# Verify request — checks whether the saved session is still valid.
# ---------------------------------------------------------------------------

class _VerifyRequest(_Request):
    def __init__(self, test_url: str = "https://www.linkedin.com/feed/"):
        super().__init__()
        self.test_url = test_url

    async def _process(self, context):  # type: ignore[override, no-untyped-def]
        page = await context.new_page()

        print(f"[AUTH CHECK] Verifying session against: {self.test_url}")
        await page.goto(self.test_url, timeout=NAVIGATION_TIMEOUT_MS)
        await asyncio.sleep(PAGE_SETTLE_MS / 1000.0)

        blocked = await _is_auth_blocked(page)
        final_url = str(page.url)

        if blocked:
            print(f"[AUTH CHECK] Auth FAILED — redirected to: {final_url}")
            print("[AUTH CHECK] Run: python linkedin_scraper.py --login")
            self.set_result(False)
        else:
            print(f"[AUTH CHECK] Auth OK — session valid (landed on: {final_url})")
            self.set_result(True)



# ---------------------------------------------------------------------------
# Public API — synchronous entry points (safe from sync and async callers).
# Each call enqueues work to the background browser thread and blocks until done.
# ---------------------------------------------------------------------------

def scrape_linkedin_job(url: str, rotate_session: bool = False) -> dict:
    _ensure_browser_running_sync()  # see below

    req = _ScrapeRequest(url, rotate_session=rotate_session)
    fut = asyncio.run_coroutine_threadsafe(_request_queue.put(req), _active_loop)
    fut.result()  # wait for the put itself to complete
    return req.result()


def run_login_flow():
    """Refresh an expired LinkedIn session."""
    _ensure_browser_running_sync()

    req = _LoginRequest()
    fut = asyncio.run_coroutine_threadsafe(_request_queue.put(req), _active_loop)
    fut.result()
    return req.result()

def verify_auth(test_url: str = "https://www.linkedin.com/feed/") -> bool:
    """Check if the saved LinkedIn session is still valid."""
    _ensure_browser_running_sync()

    req = _VerifyRequest(test_url)
    fut = asyncio.run_coroutine_threadsafe(_request_queue.put(req), _active_loop)
    fut.result()
    return req.result()


# ---------------------------------------------------------------------------
# Auth check (shared between async and sync code paths)
# ---------------------------------------------------------------------------


async def _is_auth_blocked(page: Page) -> bool:  # type: ignore[name-defined]
    url = page.url.lower()
    if any(pattern in url for pattern in AUTH_BLOCKED_URLS):
        return True
    
    locator = page.locator("input[name='session_key']")
    count = await locator.count()  # <-- ADD AWAIT
    return count > 0


# ---------------------------------------------------------------------------
# Human simulation (async versions — all use await asyncio.sleep / await page.*)
# ---------------------------------------------------------------------------

async def _human_mouse_move_async(page: Page):  # type: ignore[name-defined]
    await page.mouse.move(  # <-- ADD AWAIT
        random.randint(100, 800),
        random.randint(100, 700),
        steps=random.randint(10, 30),
    )


async def _human_scroll_async(page: Page):  # type: ignore[name-defined]
    for _ in range(random.randint(2, 5)):
        await _human_mouse_move_async(page)
        await page.mouse.wheel(0, random.randint(300, 1200))  # <-- ADD AWAIT
        await asyncio.sleep(random.uniform(1.0, 3.5))


async def _human_interact_async(page: Page):  # type: ignore[name-defined]
    if random.random() < 0.4:
        await page.mouse.wheel(0, -random.randint(100, 400))  # <-- ADD AWAIT
        await asyncio.sleep(random.uniform(0.8, 2.0))
    if random.random() < 0.3:
        await page.mouse.move(  # <-- ADD AWAIT
            random.randint(200, 900),
            random.randint(200, 600),
            steps=random.randint(20, 50),
        )
        await asyncio.sleep(random.uniform(0.5, 1.5))


async def _simulate_reading_async(page: Page):  # type: ignore[name-defined]
    try:
        text_length = len(await page.inner_text("body"))
    except Exception:
        text_length = 2000

    estimated_seconds = min(max(text_length / 120, 4), 20)
    jitter = random.uniform(0.8, 1.4)
    await asyncio.sleep(estimated_seconds * jitter)


async def _occasionally_visit_feed_async(page: Page):  # type: ignore[name-defined]
    r = random.random()
    if r < 0.25:
        print("[LinkedIn] Visiting feed page …")
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(6.0, 14.0))
        await _human_scroll_async(page)
    elif r < 0.40:
        print("[LinkedIn] Visiting jobs page …")
        await page.goto("https://www.linkedin.com/jobs/", wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(5.0, 10.0))
        await _human_scroll_async(page)





# ---------------------------------------------------------------------------
# Structured field extraction
# ---------------------------------------------------------------------------


def _extract_from_body(raw_text: str):
    lines = [l.strip() for l in raw_text.split("\n") if l.strip()]

    title = None
    company = None
    location = None

    for i, line in enumerate(lines):
        # Anchor: company + location line
        if "•" in line:
            parts = line.split("•")

            if len(parts) >= 2:
                company = parts[0].strip()
                location = parts[1].strip()

                # title is usually 1–2 lines above this
                if i >= 1:
                    title = lines[i - 1].strip()
                break

    return title, company, location


async def _extract_description_async(page: Page) -> str | None:  # type: ignore[name-defined]
    # Expand truncated description if "See more" button is present
    try:
        see_more = page.locator("button.jobs-description__footer-button")
        if await see_more.count() > 0:
            await see_more.first.click()
            await asyncio.sleep(1.0)
    except Exception:
        pass

    selectors = [
        "div.jobs-description__content",
        "div.show-more-less-html__markup",
        "div.jobs-description",
    ]
    for sel in selectors:
        loc = page.locator(sel)
        if await loc.count() > 0:
            try:
                text = (await loc.first.inner_text()).strip()
                if len(text) > 100:
                    return text
            except Exception:
                continue
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LinkedIn scraper auth utilities")
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "--login",
        action="store_true",
        help="Open a browser to log into LinkedIn and save the session",
    )
    group.add_argument(
        "--verify",
        metavar="URL",
        nargs="?",
        const="https://www.linkedin.com/feed/",
        help="Verify the saved session is still valid (optionally provide a job URL)",
    )
    args = parser.parse_args()

    if args.login:
        run_login_flow()
    elif args.verify is not None:
        ok = verify_auth(args.verify)
        sys.exit(0 if ok else 1)