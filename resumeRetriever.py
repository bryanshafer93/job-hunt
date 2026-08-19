import sqlite3
import json
import numpy as np
from config import DB_NAME
from huggingface_hub import snapshot_download
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

class ResumeRetriever:

    def __init__(self):
        local_path = snapshot_download(MODEL_NAME, local_files_only=True)
        self.model = SentenceTransformer(local_path)

    def load_chunks(self):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT chunk_id, company, role, text, embedding
            FROM experience_chunks
        """)
        rows = cursor.fetchall()
        conn.close()
        return rows

    def embed(self, text):
        return self.model.encode(text)

    def retrieve(self, job_description, top_k=5):
        job_embedding = self.embed(job_description)
        rows = self.load_chunks()

        results = []
        for chunk_id, company, role, text, embedding_json in rows:
            chunk_embedding = np.array(json.loads(embedding_json))
            score = cosine_similarity(
                [job_embedding],
                [chunk_embedding]
            )[0][0]
            results.append((score, {
                "chunk_id": chunk_id,
                "company": company,
                "role": role,
                "text": text
            }))

        results.sort(key=lambda x: x[0], reverse=True)
        return results[:top_k]