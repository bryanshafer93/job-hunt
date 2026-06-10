import json
import sqlite3
from datetime import datetime
from generate_resume_for_job import generate_resume_for_job

DB_NAME = "jobs.db"


def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def load_approved_jobs():

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM approved_jobs
        WHERE resume_used = 0
    """)

    jobs = cursor.fetchall()

    conn.close()

    return jobs


def load_work_experience():

    with open("workExperience.json", "r") as f:
        return json.load(f)


def store_resume(job_id, resume_text):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE approved_jobs
        SET
            resume_text = ?,
            resume_created_at = ?
        WHERE job_id = ?
    """, (
        resume_text,
        datetime.utcnow().isoformat(),
        job_id
    ))

    conn.commit()
    conn.close()


def mark_job_used(job_id):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE approved_jobs
        SET resume_used = 1
        WHERE job_id = ?
    """, (job_id,))

    conn.commit()
    conn.close()


def main():

    jobs = load_approved_jobs()

    if not jobs:
        print("[INFO] No pending jobs")
        return

    work_experience = load_work_experience()

    print(f"[INFO] Processing {len(jobs)} jobs")

    for job in jobs:
        job_dict = dict(job)
        try:

            resume = generate_resume_for_job(
                job_dict,
                work_experience
            )

            store_resume(job_dict["job_id"], resume)

            mark_job_used(job_dict["job_id"])

            print(f"[DONE] {job_dict['job_id']}")

        except Exception as e:

            print(
                f"[ERROR] Failed processing "
                f"{job_dict['job_id']}: {e}"
            )


if __name__ == "__main__":
    main()