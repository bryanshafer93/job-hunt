import json
import os
import time
import random
from dotenv import load_dotenv
from google import genai
from google.api_core.exceptions import (
    ServiceUnavailable,
    InternalServerError,
    ResourceExhausted,
    DeadlineExceeded,
)


# Load .env file
load_dotenv()

# ----------------------------
# CONFIGURE GEMINI
# ----------------------------
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


MODELS = [
    "gemini-3.5-flash",       # primary — newest, most intelligent, free
    "gemini-2.5-flash",       # fallback 1 — solid hybrid reasoning model
    "gemini-2.5-flash-lite",  # fallback 2 — highest throughput, most requests/day
]

# Exception types that are transient and worth retrying
RETRYABLE = (ServiceUnavailable, InternalServerError, ResourceExhausted, DeadlineExceeded)


def call_model(model, prompt, retries=5):
    for i in range(retries):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt
            )
            return response.text

        except RETRYABLE as e:
            wait = (2 ** i) + random.uniform(0, 1)
            print(f"[WARN] {model} retryable error ({type(e).__name__}). Retry {i+1}/{retries} in {wait:.2f}s")
            time.sleep(wait)

        except Exception as e:
            # Catch-all for unexpected SDK errors — check if it looks like a
            # transient overload (503/429 in the message) before retrying.
            msg = str(e).lower()
            if any(code in msg for code in ("503", "429", "unavailable", "overloaded", "resource exhausted")):
                wait = (2 ** i) + random.uniform(0, 1)
                print(f"[WARN] {model} overload error (untyped). Retry {i+1}/{retries} in {wait:.2f}s — {e}")
                time.sleep(wait)
            else:
                # Non-retryable (bad prompt, auth error, etc.) — fail immediately
                print(f"[ERROR] {model} non-retryable error: {e}")
                return None

    print(f"[WARN] {model} exhausted all {retries} retries")
    return None


def generate_with_fallback(prompt):
    for model in MODELS:
        print(f"[INFO] Trying model: {model}")
        result = call_model(model, prompt)
        if result:
            return result
        print(f"[WARN] Model failed: {model}, switching...")

    raise Exception("All models failed after retries across all fallbacks")


# ----------------------------
# GENERATE RESUME FOR SINGLE JOB
# ----------------------------
def generate_resume_for_job(job, work_experience):

    prompt = f"""
You are helping an experienced infrastructure engineer tailor their resume for a specific role.

The candidate has real experience in:
- Site Reliability Engineering
- Linux systems administration
- Cloud infrastructure
- Networking and distributed systems
- Incident response and operational support

Your job is NOT to create the most polished corporate resume possible.

Your job is to create a resume that:
- sounds technically credible
- feels written by a real engineer
- remains ATS compatible
- aligns closely with the target role
- preserves the candidate's actual experience and voice

You will be given:
1. A candidate's work history (source of truth)
2. ONE target job description

Rules:
- Do NOT invent technologies, projects, or experience
- Do NOT invent metrics or percentages
- Only strengthen or reframe existing experience
- Preserve technical realism
- Keep wording grounded and specific
- Avoid exaggerated corporate phrasing
- Avoid generic resume buzzwords
- Avoid making every bullet sound like a strategic initiative
- Some bullets should sound hands-on and operational
- Vary sentence structure naturally
- Prefer concrete technologies, systems, protocols, and operational tasks over vague abstractions
- Keep bullets concise and readable
- Do not overuse words like:
  - leveraged
  - spearheaded
  - orchestrated
  - synergized
  - optimized
  - stakeholder
  - cross-functional
  - dynamic
  - strategic

Resume style guidance:
- Write like an experienced engineer updating their own resume
- Prioritize credibility over polish
- Prioritize specificity over keyword stuffing
- Include keywords from the job description naturally where relevant
- Preserve some natural variation in tone and bullet structure
- Avoid making every bullet the same length or format
- Occasionally use shorter, direct bullets instead of fully explanatory ones
- Some bullets can be simple operational statements
- Avoid turning every task into a business impact statement

If measurable metrics are not provided:
- DO NOT invent them
- Instead describe practical engineering outcomes such as:
  - restored service availability
  - reduced debugging time
  - improved deployment reliability
  - resolved infrastructure failures
  - supported production operations
  - maintained uptime
  - improved troubleshooting consistency

WORK EXPERIENCE:
{json.dumps(work_experience, indent=2)}

TARGET JOB:
{json.dumps(job, indent=2)}

OUTPUT FORMAT:
Return ONLY a Markdown resume containing:
- Summary
- Skills
- Experience
- Optional Certifications or Projects

Formatting rules:
- Keep formatting simple and readable
- Avoid excessive bolding
- Avoid decorative language
- Keep summaries under 5 lines
- Keep bullets focused and technical
"""

    return generate_with_fallback(prompt)