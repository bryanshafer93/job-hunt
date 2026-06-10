import sqlite3
import json
import subprocess
import sys
import random
import time
from config import DB_NAME
from job_scraper import scrape_job
from job_filter import evaluate_job


# ----------------------------
# LOAD RESUME FROM DB
# ----------------------------
def load_resume():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT company, role, text FROM experience_chunks")
    rows = cursor.fetchall()
    conn.close()

    resume_text = ""
    for company, role, text in rows:
        resume_text += f"""
Company: {company}
Role: {role}
Experience:
{text}

------------------------
"""
    return resume_text


# ----------------------------
# ENSURE skill_mentions TABLE
# ----------------------------
def create_skill_mentions_table():
    conn = sqlite3.connect(DB_NAME)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS skill_mentions (
            skill TEXT,
            job_id TEXT,
            mentioned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (skill, job_id)
        )
    """)
    conn.commit()
    conn.close()


# ----------------------------
# STORE SKILL MENTIONS
# ----------------------------
def store_skill_mentions(job_id, skills):
    if not skills:
        return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    for skill in skills:
        skill_clean = skill.strip()
        if not skill_clean:
            continue
        cursor.execute("""
            INSERT OR IGNORE INTO skill_mentions (skill, job_id)
            VALUES (?, ?)
        """, (skill_clean, job_id))

    conn.commit()
    conn.close()
    print(f"[SKILLS] Stored {len(skills)} skill mention(s) for job {job_id}")


# ----------------------------
# STORE RESULT IN DB
# ----------------------------
def store_job_evaluation(job_id, job_data, evaluation):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    decision = evaluation.get("is_approved")

    if decision == "APPROVED":
        cursor.execute("""
            INSERT OR REPLACE INTO approved_jobs (
                job_id, title, company, description,
                score, reasoning, approved
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            job_id,
            job_data.get("title"),
            job_data.get("company"),
            job_data.get("clean_text"),
            evaluation.get("score"),
            evaluation.get("reasoning"),
            1
        ))
        print(f"[APPROVED] Stored job {job_id} in approved_jobs")

    elif decision == "DENIED":
        cursor.execute("""
            INSERT OR REPLACE INTO denied_jobs (
                job_id, title, company, description,
                score, reasoning
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            job_id,
            job_data.get("title"),
            job_data.get("company"),
            job_data.get("clean_text"),
            evaluation.get("score"),
            evaluation.get("reasoning")
        ))
        print(f"[DENIED] Stored job {job_id} in denied_jobs")

    else:
        conn.close()
        raise ValueError(f"Invalid is_approved value: {decision}")

    conn.commit()
    conn.close()


# ----------------------------
# MAIN PIPELINE
# ----------------------------
def match_job(url):
    create_skill_mentions_table()

    print("[1] Scraping job...")
    job = scrape_job(url)

    if not job:
        print("Failed to extract job")
        return

    job_text = job["clean_text"]

    if len(job_text) < 200:
        print("Job too short")
        return

    print("[2] Loading resume...")
    resume_text = load_resume()

    print("[3] Running job filter (LM scoring)...")
    result = evaluate_job(resume_text, job_text)

    if result["is_approved"] not in {"APPROVED", "DENIED"}:
        raise ValueError("Invalid LLM output")

    parsed = result
    job_id = job["url"]

    print("[4] Storing result in DB...")
    store_job_evaluation(job_id=job_id, job_data=job, evaluation=parsed)

    print("[4b] Storing skill mentions...")
    store_skill_mentions(job_id=job_id, skills=parsed.get("skills", []))

    if parsed.get("is_approved") == "APPROVED":
        print("[5] Job approved → triggering resume generation...")
        subprocess.run([
            sys.executable,
            "resume_job_selector.py",
            json.dumps(job),
            json.dumps(resume_text)
        ])
    else:
        print("[5] Job denied → skipping resume generation")

    sleep_time = random.uniform(12, 35)
    print(f"[LinkedIn] Waiting {sleep_time:.1f}s before next scrape...")
    time.sleep(sleep_time)

    print("\n=== MATCH RESULT ===")
    print(json.dumps(parsed, indent=2))


if __name__ == "__main__":
    url = input("Enter job URL: ").strip()
    match_job(url)