# K Recruit

Internal HR tool for Kforce. Parses, scores, and optimizes candidate resumes against job requirements using AI.

---

## Quick Start (New Machine Setup)

### 1. Install Python
Download Python 3.11+ from https://python.org and make sure to check **"Add Python to PATH"** during install.

### 2. Clone the repository
```
git clone https://github.com/YOUR_ORG/cms-resume-analyzer.git
cd cms-resume-analyzer
```

### 3. Install dependencies
```
cd backend
pip install -r requirements.txt
```

### 4. Configure AI provider
Copy the example config and fill in your API key:
```
copy ai_config.example.json ai_config.json
```
Then open `backend/ai_config.json` and replace the placeholder values with your actual API keys.

> **For development**: Use Groq (free) — get a key at https://console.groq.com  
> **For production**: Use Azure OpenAI only — fill in `azure_endpoint`, `azure_api_key`, `azure_deployment`, and set `"provider": "azure"`

### 5. Start the server
```
cd backend
uvicorn main:app --reload --port 8000
```

### 6. Open the app
Go to: http://localhost:8000

Default login:
- Username: `admin`
- Password: `cms@2024`

> Change these in `backend/ai_config.json` (`login_username` / `login_password`).

---

## Project Structure

```
cms-resume-analyzer/
├── backend/
│   ├── main.py                   # FastAPI app entry point
│   ├── requirements.txt          # Python dependencies
│   ├── ai_config.example.json    # Config template (copy to ai_config.json)
│   ├── database.py               # SQLite (dev) / PostgreSQL (prod)
│   ├── routers/                  # API route handlers
│   ├── services/                 # AI, PDF, DOCX, semantic services
│   ├── models/                   # SQLAlchemy ORM models
│   ├── schemas/                  # Pydantic schemas
│   └── static/                   # Frontend HTML (index.html, preview.html)
├── docker-compose.yml            # Docker setup
├── Dockerfile
└── k8s/                          # Kubernetes manifests
```

---

## Security Notes

- `backend/ai_config.json` is **git-ignored** — never commit it (contains API keys)
- `backend/uploads/` is **git-ignored** — contains candidate PII (resumes)
- `backend/cms_resume.db` is **git-ignored** — local database
- In production, set `"provider": "azure"` — no open-source API keys used

---

## Production Setup (Azure OpenAI)

In `backend/ai_config.json` set:
```json
{
  "provider": "azure",
  "azure_endpoint": "https://YOUR_RESOURCE.openai.azure.com",
  "azure_api_key": "YOUR_KEY",
  "azure_deployment": "gpt-4o",
  "azure_api_version": "2024-08-01-preview"
}
```
