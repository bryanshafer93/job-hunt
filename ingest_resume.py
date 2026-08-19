import json
import os
import sqlite3
from config import DB_NAME
from sentence_transformers import SentenceTransformer
from huggingface_hub import snapshot_download




BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WE_JSON  = os.path.join(BASE_DIR, "workExperience.json")
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def run():
    local_path = snapshot_download(MODEL_NAME, local_files_only=True)
    model = SentenceTransformer(local_path)

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM experience_chunks")

    with open(WE_JSON, "r") as f:
        data = json.load(f)

    for exp in data["experience"]:
        company    = exp["company"]
        role       = exp["role"]
        start_date = exp["start_date"]
        end_date   = exp["end_date"]
        skills     = exp["skills"]

        for idx, bullet in enumerate(exp["bullets"]):
            chunk_id = f"{exp['id']}_BULLET_{idx}"
            print(f"Embedding: {chunk_id}")

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

    conn.commit()
    conn.close()
    print("Resume ingestion complete.")


if __name__ == "__main__":
    run()