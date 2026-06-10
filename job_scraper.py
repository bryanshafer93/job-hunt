import requests
import trafilatura
import time
from linkedin_scraper import scrape_linkedin_job

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; JobPipelineBot/1.0)",
    "Accept-Language": "en-US,en;q=0.9"
}


def fetch_html(url, timeout=15):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.text
    except Exception:
        return None


def extract_text(html):
    if not html:
        return None
    return trafilatura.extract(html)


def scrape_job(url):
    time.sleep(2)

    if "linkedin.com" in url:
        print("[SCRAPER] Using Playwright for LinkedIn")
        job_data = scrape_linkedin_job(url)

        # Prefer the focused description field if extracted, fall back to full
        # page raw_text so the LM always gets something useful.
        clean_text = job_data.get("description") or job_data.get("raw_text", "")

        return {
            "url":        url,
            "title":      job_data.get("title"),
            "company":    job_data.get("company"),
            "location":   job_data.get("location"),
            "clean_text": clean_text,
        }

    # Generic websites
    html = fetch_html(url)
    text = extract_text(html)

    if not text:
        return None

    return {
        "url":        url,
        "title":      None,
        "company":    None,
        "location":   None,
        "clean_text": text,
    }