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

## Benchmarks & Evaluation
- [Cloud vs. Local LLM Benchmark (Requirement R14)](file:///docs/BENCHMARK_CLOUD_VS_LOCAL.md): Empirical latency, accuracy, cost, and privacy evaluation of local offline LLMs (Qwen 2.5 3B, Gemma 3 1B) vs. cloud models across 91 standardized pharmacy POS queries.

## Backend tests

Run `run_tests.bat` from the project root, or use PowerShell:

```powershell
./scripts/test-backend.ps1
./scripts/test-backend.ps1 -TestArgs @('tests/test_local_llm_inference.py', 'tests/test_llm_regressions.py', '-q')
```

The runner checks interpreter and application imports before testing. It reuses a working dedicated or portable runtime, bypassing broken Windows Store virtual environments. If none works, it creates `.venv-tests` using an installed Python and installs backend requirements plus HTTPX. To choose an interpreter, pass `-Python 'C:\path\to\python.exe'`. First-time setup requires package-index access; subsequent runs use the installed dependencies. The application environment is preserved. Test temporary directories are unique, avoiding stale cache permissions.

Module 6.1 unit/API tests mock Ollama and isolate saved model preferences. Real-engine checks require Ollama to be running with downloaded models; they are separate from offline regression tests.

To verify loading, real inference, and unloading with an idle downloaded model:

```powershell
$env:KONNECT_TEST_OLLAMA_LIVE = '1'
$env:KONNECT_TEST_MODEL = 'qwen2.5:1.5b'
./scripts/test-backend.ps1 -TestArgs @('tests/test_ollama_live.py', '-q')
Remove-Item Env:KONNECT_TEST_OLLAMA_LIVE
Remove-Item Env:KONNECT_TEST_MODEL
```

This opt-in check does not change the saved selection or download models. It loads and then unloads the specified model, and skips a model already loaded to avoid evicting an existing session.
