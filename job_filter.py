import requests
import json

LM_STUDIO_URL = "http://192.168.1.25:1234/v1/chat/completions"


def evaluate_job(resume_text, job_text):
    messages = [
        {
            "role": "system",
            "content": """
You are a strict job matching evaluator.

You will be given:
1. A candidate resume
2. A job description

Your task:
- Compare the resume to the job requirements
Extract the following from the job posting:

- Required qualifications
- Preferred qualifications
- Required technologies
- Responsibilities
- Clearance requirements
- Years of experience
- Technical skills

Ignore:
- culture
- benefits
- diversity statements
- company marketing
- legal disclaimers
- work/life balance content
- compensation information

Reasonable technical equivalence and inference are allowed.

The evaluator should map related infrastructure experience to equivalent concepts when supported by the resume.
Automatically deny any job listing from Lendistry

Examples:
- Cloud infrastructure operations may satisfy cloud services experience.
- Hypervisor, VM fleet, and distributed compute operations may satisfy distributed systems experience.
- OCI, AWS, or Azure infrastructure experience may satisfy general cloud infrastructure requirements.
- Incident response, telemetry analysis, and log analysis may satisfy operational excellence requirements.

Do NOT invent experience that is unsupported.
Do NOT assume expertise levels beyond the evidence provided.

Anything below 65 is "DENIED"

Return JSON in this exact format:

{
  "is_approved": "APPROVED" | "DENIED",
  "score": 0-100,
  "match_level": "low | medium | high",
  "strengths": ["key matches"],
  "gaps": ["missing areas"],
  "reasoning": "short explanation",
  "skills": ["skill1", "skill2"]
}

skills: extract every concrete technical skill, tool, language, platform, framework,
or certification mentioned anywhere in the job description — regardless of whether
the candidate has them. Target 10-25 items. Normalise casing (e.g. "Python", "AWS",
"Terraform"). Omit soft skills, culture language, benefits, and vague terms like
"strong communication skills".

Return ONLY valid JSON.
Do not include trailing commas.
Do not include markdown.
Validate before responding.
"""
        },
        {
            "role": "user",
            "content": f"""
=== RESUME ===
{resume_text}

=== JOB DESCRIPTION ===
{job_text}
"""
        }
    ]

    response = requests.post(
        LM_STUDIO_URL,
        json={
            "model": "qwen",
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 4096
        }
    )

    raw = response.json()

    try:
        content = raw["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        return {
            "approved": False,
            "score": 0,
            "match_level": "low",
            "strengths": [],
            "gaps": [],
            "reason": f"Parsing error: {str(e)}",
            "skills": [],
            "raw": raw
        }