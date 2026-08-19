"""
setup.py — first-run setup orchestrator for job-hunt pipeline

What it does:
  1. Validates required files (.env, credentials.json, resume.txt)
  2. Calls init_db.init_db() to create tables (idempotent)
  3. Downloads embedding model if not cached
  4. If experience_chunks is empty:
       a. Sends resume.txt + workExperienceTemplate.json to Gemini
       b. Validates and saves output as workExperience.json
       c. Runs ingest_resume.py as subprocess
  5. Prints next steps

Usage:
  python setup.py
"""

import json
import os
import sqlite3
import subprocess
import sys
import textwrap

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
RESUME_TXT = os.path.join(BASE_DIR, "resume.txt")
TEMPLATE   = os.path.join(BASE_DIR, "workExperienceTemplate.json")
WE_JSON    = os.path.join(BASE_DIR, "workExperience.json")
ENV_PATH   = os.path.join(BASE_DIR, ".env")
CREDS_PATH = os.path.join(BASE_DIR, "credentials.json")

MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-1.5-flash",
]
# ---------------------------------------------------------------------------
# Step 0 - installing requirements
# ---------------------------------------------------------------------------

def install_requirements():
    section("Step 0 — Installing dependencies")
    req_path = os.path.join(BASE_DIR, "requirements.txt")
    if not os.path.exists(req_path):
        fail("requirements.txt not found. Make sure you're running from the project root.")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", req_path],
        capture_output=False
    )
    if result.returncode != 0:
        fail("pip install failed. Check the error above.")
    ok("Dependencies installed")

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def ok(msg):   print(f"  ✅  {msg}")
def info(msg): print(f"  ℹ️   {msg}")
def warn(msg): print(f"  ⚠️   {msg}")
def fail(msg):
    print(f"\n  ❌  {msg}")
    sys.exit(1)

def section(title):
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


def load_env():
    """Parse .env into os.environ without requiring python-dotenv."""
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


# ---------------------------------------------------------------------------
# Step 1 — Validate required files
# ---------------------------------------------------------------------------

def validate_files():
    section("Step 1 — Checking required files")

    if not os.path.exists(ENV_PATH):
        fail(
            ".env file not found.\n\n"
            "  Copy .env.example and fill in your Gemini API key:\n"
            "    copy .env.example .env\n\n"
            "  Get a key at: https://aistudio.google.com/apikey"
        )
    load_env()
    if not os.environ.get("GEMINI_API_KEY"):
        fail(
            "GEMINI_API_KEY is missing or empty in .env.\n\n"
            "  Get a key at: https://aistudio.google.com/apikey\n"
            "  Then add to .env:\n"
            "    GEMINI_API_KEY=your_key_here"
        )
    ok(".env found with GEMINI_API_KEY")

    if not os.path.exists(CREDS_PATH):
        fail(
            "credentials.json not found.\n\n"
            "  Download it from Google Cloud Console:\n"
            "    1. Go to https://console.cloud.google.com/\n"
            "    2. APIs & Services → Credentials\n"
            "    3. Create OAuth 2.0 Client ID (Desktop app)\n"
            "    4. Download JSON → save as credentials.json here"
        )
    ok("credentials.json found")

    if not os.path.exists(RESUME_TXT):
        fail(
            "resume.txt not found.\n\n"
            "  Paste your resume as plain text into resume.txt\n"
            "  and place it in the project root folder.\n"
            "  Formatting doesn't matter — just the content."
        )
    if os.path.getsize(RESUME_TXT) < 100:
        fail("resume.txt looks empty. Please add your resume content.")
    ok("resume.txt found")

    if not os.path.exists(TEMPLATE):
        fail("workExperienceTemplate.json not found — it should be in the repo.")
    ok("workExperienceTemplate.json found")


# ---------------------------------------------------------------------------
# Step 2 — Init DB (delegates to init_db.py)
# ---------------------------------------------------------------------------

def run_init_db():
    section("Step 2 — Initializing database")
    try:
        from init_db import init_db
    except ImportError:
        fail("init_db.py not found. Make sure you're running from the project root.")
    init_db()
    ok("Database ready")


# ---------------------------------------------------------------------------
# Step 3 — Download embedding model
# ---------------------------------------------------------------------------

def download_model():
    section("Step 3 — Downloading embedding model")
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        fail("huggingface-hub is not installed. Run: pip install -r requirements.txt")
    info("Downloading all-MiniLM-L6-v2 (one-time, ~90MB)...")
    path = snapshot_download("sentence-transformers/all-MiniLM-L6-v2")
    ok(f"Model cached at: {path}")


# ---------------------------------------------------------------------------
# Step 4 — Check if ingestion is needed
# ---------------------------------------------------------------------------

def needs_ingestion():
    from config import DB_NAME
    conn = sqlite3.connect(DB_NAME)
    count = conn.execute("SELECT COUNT(*) FROM experience_chunks").fetchone()[0]
    conn.close()
    return count == 0


# ---------------------------------------------------------------------------
# Step 5 — Generate workExperience.json via Gemini
# ---------------------------------------------------------------------------

def generate_work_experience():
    section("Step 4a — Generating workExperience.json from resume.txt")

    try:
        from google import genai
    except ImportError:
        fail("google-genai is not installed. Run: pip install -r requirements.txt")

    with open(RESUME_TXT, "r", encoding="utf-8") as f:
        resume_text = f.read()

    with open(TEMPLATE, "r", encoding="utf-8") as f:
        template = f.read()

    prompt = textwrap.dedent(f"""
        You are a resume parser. Convert the raw resume text below into a
        JSON object that exactly matches the provided template schema.

        Rules:
        - Return ONLY valid JSON. No markdown, no backticks, no explanation.
        - Include ALL jobs from the resume as separate entries in the
          "experience" array.
        - Each entry needs a unique "id" (snake_case of company+role,
          e.g. "microsoft_senior_engineer").
        - "skills" should be technical skills visible in that role's context
          (infer from bullets if not explicit).
        - "bullets" should be the actual resume bullet points for that role,
          cleaned up but not rewritten. Include all bullets, not just 5.
        - Dates in "YYYY-MM" format. Use "Present" for current roles.
        - Do not invent experience that is not in the resume.

        TEMPLATE SCHEMA:
        {template}

        RAW RESUME:
        {resume_text}
    """)

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    raw = None

    for model_name in MODELS:
        try:
            info(f"Trying model: {model_name}")
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            raw = response.text.strip()
            ok(f"Got response from {model_name}")
            break
        except Exception as e:
            warn(f"{model_name} failed: {e}")

    if not raw:
        fail("All Gemini models failed. Check your API key and network connection.")

    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0].strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        fail(f"Gemini returned invalid JSON: {e}\n\nFirst 500 chars:\n{raw[:500]}")

    if "experience" not in parsed or not isinstance(parsed["experience"], list):
        fail(f"Gemini output missing 'experience' array. Got keys: {list(parsed.keys())}")

    if not parsed["experience"]:
        fail("Gemini returned an empty experience array. Check resume.txt has content.")

    required = {"id", "company", "role", "start_date", "end_date", "skills", "bullets"}
    for i, entry in enumerate(parsed["experience"]):
        missing = required - set(entry.keys())
        if missing:
            fail(f"Entry #{i+1} missing fields: {missing}\n{json.dumps(entry, indent=2)[:300]}")
        if not entry.get("bullets"):
            fail(f"Entry #{i+1} ({entry.get('company')}) has no bullets.")

    with open(WE_JSON, "w", encoding="utf-8") as f:
        json.dump(parsed, f, indent=2)

    roles   = len(parsed["experience"])
    bullets = sum(len(e["bullets"]) for e in parsed["experience"])
    ok(f"workExperience.json written ({roles} roles, {bullets} bullets)")


# ---------------------------------------------------------------------------
# Step 6 — Ingest resume (delegates to ingest_resume.py)
# ---------------------------------------------------------------------------

def run_ingest_resume():
    section("Step 4b — Ingesting resume into database")
    result = subprocess.run(
        [sys.executable, "ingest_resume.py"],
        cwd=BASE_DIR,
        capture_output=False
    )
    if result.returncode != 0:
        fail(f"ingest_resume.py exited with code {result.returncode}")
    ok("Ingestion complete")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("\n🎯 Job Hunt — First-Run Setup")

    install_requirements()  # add this as first call
    validate_files()
    run_init_db()
    download_model()

    if not needs_ingestion():
        section("Step 4 — Resume ingestion")
        info("experience_chunks already populated — skipping")
        info("To re-ingest: delete experience_chunks rows and re-run setup.py")
    else:
        if os.path.exists(WE_JSON):
            info("workExperience.json already exists — skipping Gemini generation")
        else:
            generate_work_experience()
        run_ingest_resume()

    section("✅ Setup complete — next steps")
    print(textwrap.dedent("""
        1. Start LM Studio and load your model
        2. Start the watcher:
             python gmail_watcher.py
        3. Open the dashboard in another terminal window:
             streamlit run dashboard.py

        Note: the first time you run gmail_watcher.py a browser window
        will open for Gmail OAuth. token.json will be written automatically.
    """))


if __name__ == "__main__":
    main()