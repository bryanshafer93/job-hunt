import sqlite3

DB_NAME = "jobs.db"


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # ----------------------------
    # EXISTING TABLE: experience_chunks
    # ----------------------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS experience_chunks (
        chunk_id TEXT PRIMARY KEY,
        company TEXT,
        role TEXT,
        start_date TEXT,
        end_date TEXT,
        chunk_type TEXT,
        text TEXT,
        embedding TEXT
    )
    """)

    # ----------------------------
    # APPROVED jobs
    # ----------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS approved_jobs (
            job_id TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            location TEXT,
            description TEXT,
            score REAL,
            reasoning TEXT,
            approved INTEGER DEFAULT 1,
            approved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resume_used INTEGER DEFAULT 0,
            resume_text TEXT,
            resume_created_at TIMESTAMP,
            applied INTEGER DEFAULT 0,
            job_status TEXT DEFAULT 'resume_generated',
            notes TEXT,
            reviewed_at TIMESTAMP
        )
    """)

    # ----------------------------
    # DENIED jobs
    # ----------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS denied_jobs (
            job_id TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            description TEXT,
            score REAL,
            reasoning TEXT,
            denied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ----------------------------
    # Skill mentions
    # ----------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS skill_mentions (
            skill TEXT,
            job_id TEXT,
            mentioned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (skill, job_id)
        )
    """)

    # ----------------------------
    # Processed URLs
    # ----------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS processed_urls (
            url TEXT PRIMARY KEY,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()

    print("Database initialized successfully.")


if __name__ == "__main__":
    init_db()