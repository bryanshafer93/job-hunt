# job-hunt
Project related to automatic job filtering and AI recommendations.

This is a readme in progress 

Some notes about this program - Linkedin has a pretty robust anti bot detection system. For now this gets around it by simulating human movements on an open browser window. That being said, to use this, it will be popping up a browser window to keep authentication alive, so it must be run on a machine with a UI that can open linkedin. Also 2FA will likely impede functionality. 

Install all dependencies into a virtual environment + install all modules needed for running the code (run from inside project folder):

python -m venv venv; .\venv\Scripts\Activate.ps1; pip install -r requirements.txt

In addition to that, this tool requires a local LM running to be able to use. I use LM studio and have Qwen3.5-27b-claude-4.6-opus-reasoning-distilled but you cna use whatever works for you. You just have to ensure that the local server is running and you will have to change the ip in the code to either localhost or whatever your network address is for your LM. 

How to use this tool:
1. Store resume in “workExperience.json using the following format:
  {“experience”: [ {“id”: “”, “company”:””, “role”:””, “start_date”:”” “end_date”:””, “skills”: [listofskills], “bullets”: [listofbullets]} (one new json object per job)
2. Edit profile.Config.json to add keywords that you want the tool to weigh more heavily (optional, if you don’t want just delete the keywords).
3. Generate a Gemini api key here: https://aistudio.google.com/api-keys
4. Create a .env file and add it in this format: GEMINI_API_KEY=[your key]
5. Run init_db.py to create the db used to store the master resume
6. Run ingest_resume.py to break down each job’s bullets in workExperience.json in a storable way and upload them to the db. 
7. From there, run gmail_watcher.py to start the whole process.

The program currently only works off of linkedin emails. It detects unread emails in your inbox (highly recommend using a dedicated email just for this) and scans each of the job listings while ignoring all of the other links in the email. Once its done, you either have to query the database for your resume or use the dashboard.py to start a webserver to visualize the contents of the DB and pull it from there.
