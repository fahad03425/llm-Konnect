# LLM-Konnect

A secure, offline-first financial analytics and reporting platform powered by a locally running Large Language Model.

Code computes. The LLM narrates. A verifier checks.

## Setup

1. Create a virtual environment:
```bash
python -m venv .venv
.venv\Scripts\activate
```

2. Install dependencies:
```bash
cd backend
pip install -r requirements.txt
```

3. Install Ollama separately and pull the model:
```bash
ollama pull qwen2.5:3b-instruct-q4_K_M
```

4. Run the API:
```bash
uvicorn app.main:app --port 8756
```

5. Access the health endpoint: http://127.0.0.1:8756/api/health

## Folder Layout
- `backend/`: Python API and models
- `data/`: CSV/Excel ingestion
- `desktop/`: Tauri/Electron shell
- `docs/`: Project documentation and roadmap
- `scripts/`: Utility scripts
See `docs/ROADMAP.md` for build order.
