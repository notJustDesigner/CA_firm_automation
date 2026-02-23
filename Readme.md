# CA Firm SaaS — Local Automation Platform

An internal SaaS tool built for a single CA firm to automate compliance workflows:
GST tracking, ITR drafting, document parsing, due-date reminders, and email drafting —
all powered by local LLMs via Ollama.

> **Privacy guarantee:** All data stays 100% local. No client information is ever
> sent to external APIs, cloud services, or third-party LLM providers.

---

## Features

- JWT-authenticated single-user portal (username + password from `.env`)
- Client management with PAN / GSTIN tracking
- Compliance due-date tracker with email reminders
- Document upload, storage, and AI-assisted extraction
- Task result logging for every AI operation
- Pluggable agent + tool architecture ready for new automations

---

## Prerequisites

Make sure the following services are installed and running before starting:

### 1. PostgreSQL (≥ 14)
```bash
# Ubuntu / Debian
sudo apt install postgresql postgresql-contrib
sudo systemctl start postgresql

# macOS (Homebrew)
brew install postgresql@14 && brew services start postgresql@14
```

Create the database:
```bash
psql -U postgres -c "CREATE DATABASE ca_saas;"
psql -U postgres -c "CREATE USER ca_user WITH PASSWORD 'yourpassword';"
psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE ca_saas TO ca_user;"
```

### 2. Redis (≥ 7)
```bash
# Ubuntu / Debian
sudo apt install redis-server
sudo systemctl start redis

# macOS
brew install redis && brew services start redis
```

### 3. Ollama (local LLM runtime)
```bash
# Linux
curl -fsSL https://ollama.com/install.sh | sh

# macOS
brew install ollama
ollama serve
```

---

## Hardware Requirements

| RAM         | Recommended Models                  | Notes                              |
|-------------|-------------------------------------|------------------------------------|
| 8 GB        | `phi3:mini`, `mistral:7b`           | Minimum — slower inference          |
| 16 GB       | `llama3.1:8b`                       | Recommended for daily use           |
| 32 GB / GPU | `llama3.1:70b`                      | Best quality, near-GPT-4 reasoning  |

---

## Pull Ollama Models

Run these once before starting the app:

```bash
ollama pull llama3.1:8b
ollama pull mistral:7b
ollama pull phi3:mini
```

For the 70B model (requires 32 GB RAM or NVIDIA GPU with ≥ 40 GB VRAM):
```bash
ollama pull llama3.1:70b
```

---

## Setup

### 1. Clone and enter the project
```bash
git clone <your-repo-url>
cd ca_saas
```

### 2. Create a virtual environment
```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure environment variables
```bash
cp .env.example .env
```

Edit `.env` and fill in real values:
- `DATABASE_URL` — your PostgreSQL connection string
- `JWT_SECRET` — generate a strong random string: `openssl rand -hex 32`
- `CA_USERNAME` / `CA_PASSWORD` — your login credentials
- `SMTP_USER` / `SMTP_PASS` — Gmail app password for sending reminders

### 5. Run database migrations
```bash
# Create the initial migration (first time)
alembic revision --autogenerate -m "initial schema"

# Apply migrations
alembic upgrade head
```

### 6. Start the server
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The API is now available at:
- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:**       http://localhost:8000/redoc
- **Health:**      http://localhost:8000/health

---

## Quick API Tour

### Login
```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "ca_firm", "password": "securepass"}'
```

### Use the token
```bash
export TOKEN="<access_token from above>"

curl http://localhost:8000/api/v1/ping \
  -H "Authorization: Bearer $TOKEN"
```

---

## Project Structure

```
ca_saas/
├── app/
│   ├── main.py          # FastAPI app, CORS, /health endpoint
│   ├── config.py        # All settings via pydantic-settings
│   ├── database.py      # Async SQLAlchemy engine + session
│   ├── models/          # ORM models (Client, TaskResult, DueDate, Document)
│   ├── api/             # Route handlers (auth.py + future routers)
│   ├── agents/          # AI agents (plug in new ones here)
│   ├── tools/           # Utility tools (Ollama, email, PDF, Redis…)
│   └── schemas/         # Pydantic request/response schemas
├── alembic/             # DB migrations
├── uploads/             # Local file storage (excluded from git)
├── templates/           # HTML templates / static frontend
├── .env.example         # Environment variable template
└── requirements.txt     # Pinned Python dependencies
```

---

## LLM Task Routing

The platform automatically routes tasks to the most suitable local model:

| Task              | Default Model  | Why                                  |
|-------------------|----------------|--------------------------------------|
| `reasoning`       | llama3.1:8b    | Strong multi-step reasoning           |
| `json_extraction` | mistral:7b     | Fast structured output                |
| `email_drafting`  | llama3.1:8b    | Fluent professional writing           |
| `summarization`   | phi3:mini      | Lightweight, very fast                |

Override models per-task in `config.py → OLLAMA_MODELS`.

---

## Security Notes

- Credentials are loaded exclusively from `.env` — never hardcoded.
- `.env` is in `.gitignore` and must never be committed.
- JWT tokens expire after `JWT_EXPIRE_MINUTES` (default: 480 min / 8 hrs).
- All uploaded files are stored in `./uploads/` on the local machine only.