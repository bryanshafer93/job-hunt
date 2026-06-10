"""
setup.py — first-run setup for job-hunt pipeline

Steps automated:
  1. Validate required files exist (.env, credentials.json, resume.txt)
  2. Initialize the database (idempotent)
  3. If experience_chunks is empty:
       a. Send resume.txt + workExperienceTemplate.json to Gemini
       b. Validate and save output as workExperience.json
       c. Run resume ingestion (embeddings -> experience_chunks)
  4. Print next steps

Usage:
  python setup.py
"""

import json
import os
import sqlite3
import sys
import textwrap

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DB_NAME    = os.path.join(BASE_DIR, "jobs.db")
ENV_PATH   = os.path.join(BASE_DIR, ".env")
CREDS_PATH = os.path.join(BASE_DIR, "credentials.json")
RESUME_TXT = os.path.join(BASE_DIR, "resume.txt")
TEMPLATE   = os.path.join(BASE_DIR, "workExperienceTemplate.json")
WE_JSON    = os.path.join(BASE_DIR, "workExperience.json")


# ---------------------------------------------------------------------------
# Helpers
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

    missing = []

    # .env + GEMINI_API_KEY
    if not os.path.exists(ENV_PATH):
        fail(
            ".env file not found.\n\n"
            "  Create it by copying .env.example:\n"
            "    copy .env.example .env\n\n"
            "  Then add your Gemini API key from:\n"
            "    https://aistudio.google.com/apikey"
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

    # credentials.json
    if not os.path.exists(CREDS_PATH):
        fail(
            "credentials.json not found.\n\n"
            "  Download it from Google Cloud Console:\n"
            "    1. Go to https://console.cloud.google.com/\n"
            "    2. APIs & Services → Credentials\n"
            "    3. Create OAuth 2.0 Client ID (Desktop app)\n"
            "    4. Download JSON and save as credentials.json in this folder"
        )
    ok("credentials.json found")

    # resume.txt
    if not os.path.exists(RESUME_TXT):
        fail(
            "resume.txt not found.\n\n"
            "  Paste your resume as plain text into a file named resume.txt\n"
            "  and place it in the project root folder.\n\n"
            "  Tip: copy/paste from your Word doc or LinkedIn profile —\n"
            "  formatting doesn't matter, just the content."
        )
    if os.path.getsize(RESUME_TXT) < 100:
        fail("resume.txt appears to be empty or too short. Please add your resume content.")
    ok("resume.txt found")

    # workExperienceTemplate.json
    if not os.path.exists(TEMPLATE):
        fail("workExperienceTemplate.json not found. It should be included in the repo.")
    ok("workExperienceTemplate.json found")


# ---------------------------------------------------------------------------
# Step 2 — Initialize database
# ---------------------------------------------------------------------------

def init_db():
    section("Step 2 — Initializing database")
    conn = sqlite3.connect(DB_NAME)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS processed_urls (
            url TEXT PRIMARY KEY,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS approved_jobs (
            job_id       TEXT PRIMARY KEY,
            title        TEXT,
            company      TEXT,
            score        REAL,
            reasoning    TEXT,
            resume_text  TEXT,
            approved_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            applied      INTEGER DEFAULT 0,
            job_status   TEXT,
            notes        TEXT,
            reviewed_at  TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS denied_jobs (
            job_id    TEXT PRIMARY KEY,
            title     TEXT,
            company   TEXT,
            score     REAL,
            reasoning TEXT,
            denied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS experience_chunks (
            chunk_id   TEXT PRIMARY KEY,
            company    TEXT,
            role       TEXT,
            start_date TEXT,
            end_date   TEXT,
            chunk_type TEXT,
            text       TEXT,
            embedding  TEXT
        );

        CREATE TABLE IF NOT EXISTS skill_mentions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            skill      TEXT,
            job_id     TEXT,
            mention_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()
    ok("Database ready")


# ---------------------------------------------------------------------------
# Step 3 — Check if ingestion is needed
# ---------------------------------------------------------------------------

def needs_ingestion():
    conn = sqlite3.connect(DB_NAME)
    count = conn.execute("SELECT COUNT(*) FROM experience_chunks").fetchone()[0]
    conn.close()
    return count == 0


# ---------------------------------------------------------------------------
# Step 4 — Generate workExperience.json via Gemini
# ---------------------------------------------------------------------------

def generate_work_experience():
    section("Step 3 — Generating workExperience.json from resume.txt")

    try:
        import google.generativeai as genai
    except ImportError:
        fail(
            "google-generativeai is not installed.\n"
            "  Run: pip install -r requirements.txt"
        )

    with open(RESUME_TXT, "r", encoding="utf-8") as f:
        resume_text = f.read()

    with open(TEMPLATE, "r", encoding="utf-8") as f:
        template = f.read()

    info("Sending resume to Gemini for formatting...")

    prompt = textwrap.dedent(f"""
        You are a resume parser. Convert the raw resume text below into a
        JSON object that exactly matches the provided template schema.

        Rules:
        - Return ONLY valid JSON. No markdown, no backticks, no explanation.
        - Include ALL jobs from the resume as separate entries in the
          "experience" array.
        - Each entry needs a unique "id" (snake_case of company+role,
          e.g. "microsoft_senior_engineer").
        - "skills" should be a list of technical skills visible in that
          role's context (infer from bullets if not explicit).
        - "bullets" should be the actual resume bullet points for that role,
          cleaned up but not rewritten. Include as many bullets as exist,
          not just 5.
        - Dates in "YYYY-MM" format. Use "Present" for current roles.
        - Do not invent experience that is not in the resume.

        TEMPLATE SCHEMA:
        {template}

        RAW RESUME:
        {resume_text}
    """)

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel("gemini-1.5-flash")

    try:
        response = model.generate_content(prompt)
        raw = response.text.strip()
    except Exception as e:
        fail(f"Gemini API call failed: {e}")

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0].strip()

    # Validate JSON shape
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        fail(
            f"Gemini returned invalid JSON: {e}\n\n"
            f"Raw output (first 500 chars):\n{raw[:500]}"
        )

    if "experience" not in parsed or not isinstance(parsed["experience"], list):
        fail(
            "Gemini output is missing the 'experience' array.\n"
            f"Got keys: {list(parsed.keys())}"
        )

    if len(parsed["experience"]) == 0:
        fail("Gemini returned an empty experience array. Check that resume.txt has content.")

    required_fields = {"id", "company", "role", "start_date", "end_date", "skills", "bullets"}
    for i, entry in enumerate(parsed["experience"]):
        missing = required_fields - set(entry.keys())
        if missing:
            fail(
                f"Experience entry #{i+1} is missing fields: {missing}\n"
                f"Entry: {json.dumps(entry, indent=2)[:300]}"
            )
        if not entry.get("bullets"):
            fail(f"Experience entry #{i+1} ({entry.get('company')}) has no bullets.")

    # Save
    with open(WE_JSON, "w", encoding="utf-8") as f:
        json.dump(parsed, f, indent=2)

    entries = len(parsed["experience"])
    bullets = sum(len(e["bullets"]) for e in parsed["experience"])
    ok(f"workExperience.json written ({entries} roles, {bullets} bullets)")

    return parsed


# ---------------------------------------------------------------------------
# Step 5 — Ingest resume into experience_chunks
# ---------------------------------------------------------------------------

def ingest_resume():
    section("Step 4 — Ingesting resume into database")

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        fail(
            "sentence-transformers is not installed.\n"
            "  Run: pip install -r requirements.txt"
        )

    info("Loading embedding model (all-MiniLM-L6-v2)...")
    model = SentenceTransformer("all-MiniLM-L6-v2")

    with open(WE_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM experience_chunks")

    total = 0
    for exp in data["experience"]:
        company    = exp["company"]
        role       = exp["role"]
        start_date = exp["start_date"]
        end_date   = exp["end_date"]
        skills     = exp["skills"]

        for idx, bullet in enumerate(exp["bullets"]):
            chunk_id = f"{exp['id']}_BULLET_{idx}"
            print(f"    Embedding: {chunk_id}")

            embedding_text = f"""
            Role: {role}
            Company: {company}
            Timeframe: {start_date} to {end_date}

            Systems Context:
            Cloud infrastructure, distributed systems, Linux environments, production operations

            Skills (from resume metadata):
            {", ".join(skills)}

            Work Item:
            {bullet}
            """

            embedding = model.encode(embedding_text)
            embedding_json = json.dumps(embedding.tolist())

            cursor.execute("""
                INSERT OR REPLACE INTO experience_chunks (
                    chunk_id, company, role, start_date, end_date,
                    chunk_type, text, embedding
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                chunk_id, company, role, start_date, end_date,
                "bullet", bullet, embedding_json
            ))
            total += 1

    conn.commit()
    conn.close()
    ok(f"Ingestion complete — {total} chunks embedded")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("\n🎯 Job Hunt — First-Run Setup")

    validate_files()
    init_db()

    if not needs_ingestion():
        section("Step 3 — Resume ingestion")
        info("experience_chunks already populated — skipping ingestion")
        info("To re-ingest, delete experience_chunks rows and re-run setup.py")
    else:
        # workExperience.json may already exist (e.g. user wrote it manually)
        if os.path.exists(WE_JSON):
            info("workExperience.json already exists — skipping Gemini generation")
            info("Proceeding directly to ingestion")
        else:
            generate_work_experience()

        ingest_resume()

    section("✅ Setup complete")
    print(textwrap.dedent("""
        You're ready to run the pipeline:

          1. Start LM Studio and load your model
          2. Run the watcher:
               python gmail_watcher.py
          3. Open the dashboard:
               streamlit run dashboard.py

        Gmail OAuth: the first time you run gmail_watcher.py a browser
        window will open for you to authorize access. credentials.json
        is read-only during this — token.json will be written afterward.
    """))


if __name__ == "__main__":
    main()