import base64
import time
import os
import re
import sqlite3
import logging
import sys
from logging.handlers import RotatingFileHandler
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from match_job import match_job
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from urllib.parse import urlparse, urlunsplit
from config import DB_NAME


SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(BASE_DIR, "token.json")
CREDS_PATH = os.path.join(BASE_DIR, "credentials.json")
LOG_PATH = os.path.join(BASE_DIR, "watcher.log")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logger() -> logging.Logger:
    logger = logging.getLogger("watcher")
    logger.setLevel(logging.DEBUG)

    # Prevent duplicate handlers on restart
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)

    file_handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=5 * 1024 * 1024,
        backupCount=7,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    logger.addHandler(console)
    logger.addHandler(file_handler)

    return logger


log = setup_logger()


# ---------------------------------------------------------------------------
# DB — tracks processed URLs
# ---------------------------------------------------------------------------

def init_db():
    conn = sqlite3.connect(DB_NAME)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_urls (
            url TEXT PRIMARY KEY,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()

    log.debug("DB ready")


def is_url_processed(url: str) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT 1 FROM processed_urls WHERE url = ?",
        (url,)
    )

    found = cursor.fetchone() is not None

    conn.close()

    return found


def mark_url_processed(url: str):
    conn = sqlite3.connect(DB_NAME)

    conn.execute(
        "INSERT OR IGNORE INTO processed_urls (url) VALUES (?)",
        (url,)
    )

    conn.commit()
    conn.close()

    log.debug(f"URL marked processed: {url}")


# ---------------------------------------------------------------------------
# Gmail auth
# ---------------------------------------------------------------------------

def get_gmail_service():
    creds = None

    if os.path.exists(TOKEN_PATH):
        try:
            creds = Credentials.from_authorized_user_file(
                TOKEN_PATH,
                SCOPES
            )
        except Exception:
            log.warning("Corrupt token.json — forcing re-auth")
            creds = None

    if creds:
        try:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                log.debug("Gmail token refreshed")
        except Exception:
            log.warning("Token refresh failed — forcing re-auth")
            creds = None

    if not creds or not creds.valid:
        log.info("Starting Gmail OAuth login flow...")

        flow = InstalledAppFlow.from_client_secrets_file(
            CREDS_PATH,
            SCOPES
        )

        creds = flow.run_local_server(port=0)

        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

        log.info("Gmail auth complete — token saved")

    return build("gmail", "v1", credentials=creds)


# ---------------------------------------------------------------------------
# Gmail helpers
# ---------------------------------------------------------------------------

def extract_urls(payload):
    urls = []

    if "parts" in payload:
        for part in payload["parts"]:
            urls += extract_urls(part)

    body_data = payload.get("body", {}).get("data")

    if body_data:
        try:
            data = base64.urlsafe_b64decode(body_data).decode("utf-8")

            urls += re.findall(
                r'https?://[^\s<>"\']+',
                data
            )

        except Exception as e:
            log.debug(f"Base64 decode failed on payload part: {e}")

    return urls


def fetch_unread_messages(service):
    """
    Use Gmail search query instead of labelIds.
    Gmail label filtering behaves unexpectedly.
    """

    results = service.users().messages().list(
        userId="me",
        q="is:unread in:inbox"
    ).execute()

    messages = results.get("messages", [])

    log.info(f"Fetched {len(messages)} unread inbox message(s)")

    return messages


def get_message(service, msg_id):
    return service.users().messages().get(
        userId="me",
        id=msg_id,
        format="full"
    ).execute()


def mark_read(service, msg_id):
    log.info(f"Attempting to mark Gmail message as read: {msg_id}")

    service.users().messages().modify(
        userId="me",
        id=msg_id,
        body={"removeLabelIds": ["UNREAD"]}
    ).execute()

    log.info(f"Successfully marked Gmail message as read: {msg_id}")


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

BAD_PATTERNS = [
    "/comm/jobs/alerts",
    "manage_alerts",
    "/help/",
    "/learning/",
]


def get_path(url):
    return urlparse(url).path.lower()


def normalize_url(url: str) -> str:
    parsed = urlparse(url)

    clean_path = parsed.path.rstrip("/") + "/"

    clean_path = clean_path.replace(
        "/comm/jobs/view/",
        "/jobs/view/"
    )

    return urlunsplit((
        parsed.scheme,
        parsed.netloc,
        clean_path,
        "",
        ""
    ))


def is_valid_job_url(url: str) -> bool:
    path = get_path(url)

    is_good = (
        "/jobs/view/" in path
        or "/comm/jobs/view/" in path
        or "greenhouse.io" in url
        or "lever.co" in url
    )

    is_bad = any(b in url for b in BAD_PATTERNS)

    return is_good and not is_bad


# ---------------------------------------------------------------------------
# Main watcher loop
# ---------------------------------------------------------------------------

def run_watcher():
    init_db()

    service = get_gmail_service()

    log.info(
        f"Watcher started — polling every 30s. "
        f"Logging to: {LOG_PATH}"
    )

    while True:
        try:
            messages = fetch_unread_messages(service)

            log.info(
                f"Poll: {len(messages)} unread message(s) found"
            )

            for msg in messages:
                msg_id = msg["id"]

                log.info("=" * 70)
                log.info(f"PROCESSING EMAIL: {msg_id}")

                try:
                    full = get_message(service, msg_id)

                    labels = full.get("labelIds", [])

                    log.info(f"Current Gmail labels: {labels}")

                    payload = full["payload"]

                    all_urls = extract_urls(payload)

                    log.info(
                        f"Extracted {len(all_urls)} raw URL(s)"
                    )

                    seen_in_email = set()
                    valid_urls = []

                    for url in all_urls:
                        url = url.strip()

                        if is_valid_job_url(url):

                            canonical = normalize_url(url)

                            if canonical in seen_in_email:
                                log.debug(
                                    f"SKIP duplicate in email: {canonical}"
                                )

                            elif is_url_processed(canonical):
                                log.info(
                                    f"SKIP already processed: {canonical}"
                                )

                            else:
                                log.info(
                                    f"QUEUED new URL: {canonical}"
                                )

                                seen_in_email.add(canonical)
                                valid_urls.append(canonical)

                        else:
                            log.debug(f"FILTERED URL: {url}")

                    if not valid_urls:
                        log.info(
                            f"No NEW valid job URLs in email {msg_id}"
                        )

                        mark_read(service, msg_id)

                        continue

                    log.info(
                        f"Processing {len(valid_urls)} new URL(s)"
                    )

                    all_succeeded = True

                    for url in valid_urls:
                        log.info(f"Running match_job(): {url}")

                        try:
                            match_job(url)

                            mark_url_processed(url)

                            log.info(
                                f"SUCCESS processing URL: {url}"
                            )

                        except Exception as e:
                            all_succeeded = False

                            log.error(
                                f"FAILED processing URL: {url} — {e}",
                                exc_info=True
                            )

                    if all_succeeded:
                        mark_read(service, msg_id)

                        log.info(
                            f"Email {msg_id} fully processed"
                        )

                    else:
                        log.warning(
                            f"Email {msg_id} had failures — "
                            f"leaving unread for retry"
                        )

                except Exception as e:
                    log.error(
                        f"Fatal error processing email {msg_id}: {e}",
                        exc_info=True
                    )

            log.info("Poll complete — sleeping 30s")
            time.sleep(30)

        except Exception as e:
            log.error(
                f"Watcher loop failure: {e}",
                exc_info=True
            )

            log.info("Sleeping 30s before retry")
            time.sleep(30)


if __name__ == "__main__":
    run_watcher()