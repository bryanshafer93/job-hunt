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
"""

import argparse
import sys
import atexit
import random
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, BrowserContext, Page


playwright_instance = None
persistent_context = None
persistent_page = None
_scrape_count = 0
_max_scrapes_per_session = random.randint(5, 8)


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
# Browser setup
# ---------------------------------------------------------------------------

def _launch_context(playwright, headless: bool) -> BrowserContext:
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
        args=["--disable-blink-features=AutomationControlled"],
        ignore_default_args=["--enable-automation"],
    )


def _initialize_persistent_browser():
    global playwright_instance, persistent_context, persistent_page

    if persistent_context is not None:
        return

    playwright_instance = sync_playwright().start()
    persistent_context = _launch_context(playwright_instance, headless=False)
    persistent_page = persistent_context.new_page()

    print("[LinkedIn] Persistent browser session initialized")


def _is_auth_blocked(page: Page) -> bool:
    url = page.url.lower()
    if any(pattern in url for pattern in AUTH_BLOCKED_URLS):
        return True
    return page.locator("input[name='session_key']").count() > 0


# ---------------------------------------------------------------------------
# Human simulation
# ---------------------------------------------------------------------------

def _human_pause(min_seconds=4.0, max_seconds=12.0):
    time.sleep(random.uniform(min_seconds, max_seconds))


def _human_mouse_move(page):
    page.mouse.move(
        random.randint(100, 800),
        random.randint(100, 700),
        steps=random.randint(10, 30)
    )


def _human_scroll(page):
    for _ in range(random.randint(2, 5)):
        _human_mouse_move(page)
        page.mouse.wheel(0, random.randint(300, 1200))
        page.wait_for_timeout(random.randint(1000, 3500))


def _human_interact(page):
    if random.random() < 0.4:
        page.mouse.wheel(0, -random.randint(100, 400))
        page.wait_for_timeout(random.randint(800, 2000))
    if random.random() < 0.3:
        page.mouse.move(
            random.randint(200, 900),
            random.randint(200, 600),
            steps=random.randint(20, 50)
        )
        page.wait_for_timeout(random.randint(500, 1500))


def _simulate_reading(page):
    try:
        text_length = len(page.inner_text("body"))
    except Exception:
        text_length = 2000

    estimated_seconds = min(max(text_length / 120, 4), 20)
    jitter = random.uniform(0.8, 1.4)
    page.wait_for_timeout(int(estimated_seconds * jitter * 1000))


def _occasionally_visit_feed(page):
    r = random.random()
    if r < 0.25:
        print("[LinkedIn] Visiting feed page...")
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        page.wait_for_timeout(random.randint(6000, 14000))
        _human_scroll(page)
    elif r < 0.40:
        print("[LinkedIn] Visiting jobs page...")
        page.goto("https://www.linkedin.com/jobs/", wait_until="domcontentloaded")
        page.wait_for_timeout(random.randint(5000, 10000))
        _human_scroll(page)


def _rotate_session_if_needed():
    global persistent_context, persistent_page, playwright_instance
    global _scrape_count, _max_scrapes_per_session

    if _scrape_count < _max_scrapes_per_session:
        return

    print(f"[LinkedIn] Rotating session after {_scrape_count} scrapes — cooling down...")
    shutdown_browser()
    persistent_context = None
    persistent_page = None
    playwright_instance = None
    _scrape_count = 0
    _max_scrapes_per_session = random.randint(5, 8)

    cooldown = random.uniform(30, 90)
    print(f"[LinkedIn] Waiting {cooldown:.0f}s before new session...")
    time.sleep(cooldown)


# ---------------------------------------------------------------------------
# Structured field extraction
# ---------------------------------------------------------------------------

def extract_title(page: Page):
    try:
        return page.locator("h1").first.inner_text().strip()
    except Exception:
        return None


def extract_company(page: Page):
    selectors = [
        "div.jobs-unified-top-card__company-name a",
        "div.jobs-unified-top-card a[href*='/company/']",
        "a[href*='/company/'][href*='/life']",
    ]
    for sel in selectors:
        loc = page.locator(sel)
        if loc.count() > 0:
            try:
                return loc.first.inner_text().strip()
            except Exception:
                continue
    return None


def extract_description(page: Page):
    # Expand truncated description if "See more" button is present
    try:
        see_more = page.locator("button.jobs-description__footer-button")
        if see_more.count() > 0:
            see_more.first.click()
            page.wait_for_timeout(1000)
    except Exception:
        pass

    selectors = [
        "div.jobs-description__content",
        "div.show-more-less-html__markup",
        "div.jobs-description",
    ]
    for sel in selectors:
        loc = page.locator(sel)
        if loc.count() > 0:
            try:
                text = loc.first.inner_text().strip()
                if len(text) > 100:
                    return text
            except Exception:
                continue
    return None


def extract_location(page: Page):
    try:
        return page.locator(
            "div.jobs-unified-top-card__bullet"
        ).first.inner_text().strip()
    except Exception:
        return None

def extract_from_body(raw_text):
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

# ---------------------------------------------------------------------------
# Core scrape — reuses persistent_page, never closes the tab
# ---------------------------------------------------------------------------

def _scrape_with_context(url: str) -> dict:
    global persistent_page, _scrape_count

    _rotate_session_if_needed()
    _initialize_persistent_browser()

    page = persistent_page

    _occasionally_visit_feed(page)
    _human_pause(3, 8)

    page.goto(url, timeout=NAVIGATION_TIMEOUT_MS, wait_until="domcontentloaded")


    _human_pause(5, 14)
    _human_mouse_move(page)
    _human_scroll(page)
    _human_interact(page)
    _simulate_reading(page)
    page.wait_for_timeout(PAGE_SETTLE_MS)



    if _is_auth_blocked(page):
        raise Exception("LINKEDIN_AUTH_EXPIRED")

    raw_text = page.inner_text("body").strip()

    title, company, location = extract_from_body(raw_text)

    if len(raw_text) < 200:
        raise Exception(
            f"LINKEDIN_SCRAPE_FAILED — page content too short ({len(raw_text)} chars)"
        )

    # Extract structured fields — fall back gracefully if selectors miss
    job_data = {
    "url": url,
    "title": title,
    "company": company,
    "location": location,
    "description": extract_description(page),
    "raw_text": raw_text,
    }



    _scrape_count += 1
    return job_data



# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scrape_linkedin_job(url: str) -> dict:
    """
    Scrape a LinkedIn job posting using the saved persistent profile.
    Returns a structured dict. Triggers re-login automatically if session expired.
    """
    try:
        return _scrape_with_context(url)
    except Exception as e:
        if "LINKEDIN_AUTH_EXPIRED" not in str(e):
            raise
        run_login_flow()
        return _scrape_with_context(url)


# ---------------------------------------------------------------------------
# Login flow
# ---------------------------------------------------------------------------

def run_login_flow():
    global persistent_page

    _initialize_persistent_browser()
    page = persistent_page

    print("[AUTH] Session expired — navigating to LinkedIn login...")
    print("[AUTH] Enter your credentials in the existing browser window.")

    page.goto("https://www.linkedin.com/login", timeout=NAVIGATION_TIMEOUT_MS)

    page.wait_for_url(
        lambda url: "linkedin.com/login" not in url,
        timeout=120_000,
    )
    page.wait_for_timeout(2000)

    if _is_auth_blocked(page):
        raise RuntimeError(
            "[AUTH] Still seeing a login page after redirect — login may have failed."
        )

    print(f"[AUTH] Session refreshed in '{PROFILE_DIR}/'")


# ---------------------------------------------------------------------------
# Auth verification
# ---------------------------------------------------------------------------

def verify_auth(test_url: str = "https://www.linkedin.com/feed/") -> bool:
    print(f"[AUTH CHECK] Verifying session against: {test_url}")
    with sync_playwright() as pw:
        context = _launch_context(pw, headless=False)
        page = context.new_page()
        try:
            page.goto(test_url, timeout=NAVIGATION_TIMEOUT_MS,
                      wait_until="domcontentloaded")
            page.wait_for_timeout(PAGE_SETTLE_MS)

            blocked = _is_auth_blocked(page)
            final_url = page.url

            if blocked:
                print(f"[AUTH CHECK] Auth FAILED — redirected to: {final_url}")
                print("[AUTH CHECK] Run: python linkedin_scraper.py --login")
                return False
            else:
                print(f"[AUTH CHECK] Auth OK — session valid (landed on: {final_url})")
                return True
        finally:
            page.close()
            context.close()


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------

def shutdown_browser():
    global persistent_context, playwright_instance
    try:
        if persistent_context:
            persistent_context.close()
        if playwright_instance:
            playwright_instance.stop()
    except Exception as e:
        print(f"[LinkedIn] Shutdown warning: {e}")


atexit.register(shutdown_browser)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LinkedIn scraper auth utilities")
    group = parser.add_mutually_exclusive_group(required=True)
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