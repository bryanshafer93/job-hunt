import json
import sqlite3
from sentence_transformers import SentenceTransformer
from config import DB_NAME

#this is a one time use script that will pull experience from workExperience.json and store it in the jobs.db.

DB_NAME = "jobs.db"
RESUME_FILE = "master_resume.json"

# Load embedding model
model = SentenceTransformer("all-MiniLM-L6-v2")

# Connect to SQLite
conn = sqlite3.connect(DB_NAME)
cursor = conn.cursor()
cursor.execute("DELETE FROM experience_chunks")

# Load resume JSON
with open('workExperience.json', "r") as f:
    data = json.load(f)

experience = data["experience"]

# Iterate through experience entries
for exp in experience:

    company = exp["company"]
    role = exp["role"]
    start_date = exp["start_date"]
    end_date = exp["end_date"]
    skills = exp["skills"]

    # Iterate through bullets
    for idx, bullet in enumerate(exp["bullets"]):

        chunk_id = f"{exp['id']}_BULLET_{idx}"

        print(f"Embedding: {chunk_id}")

        # Generate embedding
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

        # Convert embedding to JSON string
        embedding_json = json.dumps(embedding.tolist())

        # Insert into database
        cursor.execute("""
        INSERT OR REPLACE INTO experience_chunks (
            chunk_id,
            company,
            role,
            start_date,
            end_date,
            chunk_type,
            text,
            embedding
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            chunk_id,
            company,
            role,
            start_date,
            end_date,
            "bullet",
            bullet,
            embedding_json
        ))

# Save changes
conn.commit()

print("Resume ingestion complete.")

conn.close()