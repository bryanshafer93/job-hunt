An automated job hunting pipeline that monitors your email for job listings, scores them against your resume using AI, generates tailored resumes for strong matches, and tracks everything in a dashboard.

## How It Works

1. **Gmail Watcher** monitors a dedicated job alert email account for new listings
2. **Job Scorer** scrapes each listing and scores it against your resume using a local LLM
3. **Resume Generator** uses Gemini to tailor your resume for approved matches
4. **Dashboard** lets you track applications, statuses, and skill trends

---

## Requirements

- Python 3.10–3.12
- [LM Studio](https://lmstudio.ai) running on a machine accessible on your local network
- A dedicated Gmail account for job alert emails
- A [Gemini API key](https://aistudio.google.com/apikey) (free tier is sufficient)
- A [Google Cloud project](https://console.cloud.google.com) with the Gmail API enabled

---

## Setup

### 1. Clone the repo and create a virtual environment

```bash
git clone https://github.com/bryanshafer93/job-hunt.git
cd job-hunt


```

### 2. Configure LM Studio

On the machine that will run the LLM:

1. Download and install [LM Studio](https://lmstudio.ai)
2. Load a model — **Qwen3-8B** is confirmed working and runs comfortably on 16GB RAM
3. Go to the **Local Server** tab and start the server
4. Note the server IP and port (default: `http://localhost:1234`) — if running on a separate machine, use that machine's local IP

> **Recommended models:** Any instruction-tuned model 7B or larger. Qwen3-8B is the tested baseline.

### 3. Get a Gemini API key

1. Go to [https://aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. Create a new key
3. Keep it handy for the next step

### 4. Set up Gmail OAuth credentials

This allows the app to read emails from your dedicated job alert account.

1. Go to [https://console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (e.g. "job-hunt")
3. Go to **APIs & Services → Library** and enable the **Gmail API**
4. Go to **APIs & Services → Credentials**
5. Click **Create Credentials → OAuth 2.0 Client ID**
6. Choose **Desktop app** as the application type
7. Download the JSON file and save it as `credentials.json` in the project root

### 5. Configure environment variables

Copy the example env file:

```bash
# Windows
copy .env.example .env

# Mac/Linux
cp .env.example .env
```

Open `.env` and fill in your values:

```
GEMINI_API_KEY=your_gemini_key_here
LM_STUDIO_URL=http://192.168.1.x:1234    # your LM Studio machine's IP
```

### 6. Add your resume

Paste your resume as plain text into a file named `resume.txt` in the project root. Formatting doesn't matter — just the content. The setup script will use Gemini to parse it automatically.

### 7. Run setup

```bash
python setup.py
```

This will:
- Install all dependencies
- Initialize the database
- Download the embedding model (~90MB, one-time)
- Parse your resume into `workExperience.json` using Gemini
- Embed your experience into the database

> If `workExperience.json` already exists, the Gemini parsing step is skipped and your existing file is used directly.

---

## Running the Pipeline

### Start the watcher

```bash
python gmail_watcher.py
```

The first time you run this, a browser window will open asking you to authorize Gmail access. Log in with your dedicated job alert account and click Allow. A `token.json` file will be saved automatically — you won't need to do this again unless you revoke access.

The watcher polls for new emails every 30 seconds. Leave it running in the background.

### Start the dashboard

In a separate terminal:

```bash
streamlit run dashboard.py
```

Open [http://localhost:8501](http://localhost:8501) in your browser.

---

## Setting Up Job Alert Emails

The watcher looks for job listing URLs in your inbox. Currently confirmed working with **LinkedIn Job Alerts**.

To set up LinkedIn alerts:
1. Go to [LinkedIn Jobs](https://www.linkedin.com/jobs/)
2. Run a search with your target filters (title, location, experience level)
3. Click **Set alert** and choose your dedicated Gmail account as the delivery address
4. Set frequency to **Daily** or **Weekly**

The pipeline also supports Greenhouse and Lever job URLs out of the box.

---

## Re-ingesting Your Resume

If you update `workExperience.json` or `resume.txt` and want to re-embed:

```bash
# Clear existing chunks
python -c "import sqlite3; conn = sqlite3.connect('jobs.db'); conn.execute('DELETE FROM experience_chunks'); conn.commit()"

# Re-run setup
python setup.py
```

---

## Project Structure

```
job-hunt/
├── setup.py                    # First-run setup orchestrator
├── gmail_watcher.py            # Email monitor and job processing loop
├── match_job.py                # Job scraping and LLM scoring
├── generate_resume_for_job.py  # Gemini resume tailoring
├── resumeRetriever.py         # Semantic resume chunk retrieval
├── ingest_resume.py            # Resume embedding and DB storage
├── init_db.py                  # Database schema initialization
├── dashboard.py                # Streamlit dashboard
├── config.py                   # Shared configuration
├── workExperienceTemplate.json # Resume schema template
├── resume.txt                  # Your resume (you create this)
├── workExperience.json         # Parsed resume (auto-generated)
├── .env.example                # Environment variable template
└── jobs.db                     # SQLite database (auto-created)
```

---

## Files Not Included (You Provide These)

| File | How to get it |
|------|--------------|
| `.env` | Copy `.env.example` and fill in your values |
| `resume.txt` | Paste your resume as plain text |
| `credentials.json` | Download from Google Cloud Console (see Step 4) |
| `token.json` | Auto-generated on first run of `gmail_watcher.py` |
| `workExperience.json` | Auto-generated by `setup.py` from your `resume.txt` |

---

## Troubleshooting

**Setup fails at model download**
Make sure `HF_HUB_OFFLINE` is not set in your environment or `.env` file.

**`gmail_watcher.py` says token is invalid**
Delete `token.json` and restart — the OAuth flow will re-run.

**LLM scoring returns no resume match**
Check that `experience_chunks` is populated:
```bash
python -c "import sqlite3; conn = sqlite3.connect('jobs.db'); print(conn.execute('SELECT COUNT(*) FROM experience_chunks').fetchone()[0], 'chunks')"
```
If it returns 0, re-run `python setup.py`.

**LM Studio connection refused**
Confirm the Local Server is running in LM Studio and that `LM_STUDIO_URL` in `.env` matches the correct IP and port.

NOTE: If you want to run this without docker, you will need to create a virtual environment so that it doesn't conflict with system packages:
Create a virtual environment
bash# Windows
python -m venv .venv
.venv\Scripts\activate

# Mac/Linux
python -m venv .venv
source .venv/bin/activate