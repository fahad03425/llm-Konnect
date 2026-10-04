# 6.1 Local LLM Inference Module

## Completion assessment

Reviewed on **2 October 2026**.

**The module is functionally complete for the stated Module 6.1 requirements:** managing local models through Ollama, pulling models, loading and unloading them, switching between models, and providing a shared internal interface for inference.

This assessment means that each required feature has an implementation and supporting verification. It does not mean every possible model, operating system, GPU, download failure, or concurrent deployment has been tested. The practical boundaries are explained below.

| Requirement | How the project meets it |
| --- | --- |
| Manage models through Ollama | One `LLMService` communicates with the configured Ollama server. |
| Pull models | It supports normal downloads and downloads with progress updates. |
| Load models | It sends an empty-prompt request to put a model into memory. |
| Unload models | It sends an empty-prompt request with `keep_alive=0`. |
| Switch models | It validates an installed model and saves the new selection. |
| Support different model families | The same inference methods accept installed completion models, including Phi, Qwen, Mistral, Llama, and Gemma. |
| Keep other modules model-agnostic | Chat, reporting, and anomaly explanations use the shared service rather than creating their own Ollama clients. |

## 1. Understand the four model states

These terms mean different things:

| Term | Plain meaning |
| --- | --- |
| Downloaded or installed | The model files are stored on disk by Ollama. |
| Selected or active | The application has chosen this model as its default. |
| Loaded or running | Ollama currently has the model in RAM or GPU memory. |
| Resolved model | The model the service chooses for a particular inference request. |

Downloading a model does not automatically select it. Selecting a model does not immediately load it. Unloading a model frees its memory without deleting its downloaded files.

For example, Qwen and Llama can both be downloaded, Qwen can be selected, and neither needs to be loaded until an inference request arrives.

## 2. Start the application and connect to Ollama

The backend creates one shared `LLMService` object called `llm` when the module is imported.

The service reads the previously saved selection from `model_settings.json` inside the configured storage directory. The service resolves the default relative path against the project root, giving `data/storage/model_settings.json`. If the file is absent, unreadable, or invalid, the service uses the configured default model: `qwen2.5:1.5b`.

The default Ollama address is `http://127.0.0.1:11434`. The address refers to a server on the same computer. The service creates an Ollama client when an operation needs one.

Ollama must be installed and running separately. The inference service itself does not install or start the Ollama executable. The Windows `run_desktop.bat` launcher attempts to start an installed Ollama server when its local port is unavailable.

The application uses **lazy loading**: starting the backend does not automatically put the selected language model into memory. The first inference request can load it, or the user can press **Load** beforehand.

## 3. Check the engine and installed models

The frontend requests the installed-model list from the backend. The backend asks Ollama for the models stored on disk.

The response includes:

- `models`: the installed model names.
- `active_model`: the saved application selection.
- `ollama.available`: whether the service can communicate with Ollama.
- `ollama.resolved_model`: the available default or fallback model, if one can be resolved.
- `ollama.error`: an explanation when the engine cannot be reached.

Ollama can be online with zero downloaded models. An online engine and an installed model are separate conditions.

If Ollama is offline, the model-list endpoints return HTTP 200 with an empty list and `available=false`. This lets the UI display **Ollama: Offline** without producing repeated HTTP 500 errors during status polling. The empty list is not a claim that all downloaded files have disappeared; discovery failed because the engine was unavailable.

Actual operations such as loading or inference still report failures when Ollama cannot perform them.

## 4. Inspect the computer and show suggestions

The service checks CPU cores and tries to discover GPU hardware using PyTorch, NVIDIA tools, Apple Metal availability, or Windows graphics-adapter information. It also reports RAM and available memory when the relevant readings are available.

The recommendation profiles are:

| Profile | Condition used by the code | Example suggestions |
| --- | --- | --- |
| CPU lightweight | No GPU detected | Phi-4-mini, Phi-3 mini, smaller Llama or Qwen models |
| Budget GPU | Reported VRAM below 5,500 MB | Qwen 2.5 3B, Phi-4-mini, Llama 3.2 3B |
| Standard GPU | Reported VRAM from 5,500 to below 12,000 MB | Qwen 2.5 7B, Mistral 7B, Llama 3.1 8B |
| Heavy GPU | Reported VRAM at least 12,000 MB | Qwen 2.5 14B and smaller alternatives |

These are suggestions. The module does not automatically download the recommended model, force CPU or GPU execution, or guarantee that a model will fit in memory. Ollama controls actual execution and memory allocation. Quantization, context length, other running programs, and GPU compatibility also affect resource use.

Hardware results are cached. `/api/models/hardware?refresh=true` requests a new scan. Windows adapter-memory readings can be capped, and some readings can be unavailable; the recommendations remain advisory.

## 5. Download a model

Open **Settings → Local AI model**, enter a model name, and press **Download**.

The frontend sends a request to `/api/models/pull`. The service passes that model name to Ollama, which handles downloading and storing the files.

There are two supported download modes:

1. **Normal mode:** wait for the operation to finish and receive its result.
2. **Streaming mode:** receive progress updates as the download proceeds.

Streaming updates include the status, downloaded bytes, total bytes, and percentage when a total is known. Each update is sent as one JSON line. The percentage refers to the transfer information Ollama supplies for that update; it is not necessarily a single overall percentage across all model layers.

The Settings screen checks for Ollama's final `success` status before saying the model was downloaded. A broken or unfinished stream is reported as a failure. Downloading does not change the active model.

A first download normally needs network access to the model registry. Subsequent local inference with already downloaded models does not require downloading those files again.

## 6. Select or switch the default model

Choose an installed model and press **Use model**, or select it from the chatbot's model dropdown.

Both selection APIs use the same `set_active_model()` method. It follows this sequence:

1. Trim the supplied name and reject an empty name.
2. Ask Ollama for installed models.
3. Match the requested model without substituting a different size.
4. Inspect the model's capabilities. If Ollama supplies capabilities and text completion is absent, reject it.
5. Write the selection to a temporary settings file.
6. Replace the saved settings file with the completed temporary file.
7. Update the shared service and application configuration.

The file replacement avoids leaving a partially written preference. A lock serializes selection writes within the service instance. Failed validation leaves the previous selection unchanged.

An untagged name is treated as its `:latest` alias. A size tag can match the same tag with an additional suffix, such as `qwen2.5:7b-q4_K_M`. A request for Qwen 7B does not substitute Qwen 14B.

Switching changes the default for subsequent requests. It does not restart an answer already being generated, pre-load the new model, or automatically unload the previous model. Use the separate memory controls when needed.

## 7. Load a model before inference

Press **Load** in Settings. The service sends an empty-prompt generation request to Ollama with a `keep_alive` duration.

This places the requested model into RAM or GPU memory so a later request can avoid the model-loading delay. It does not make the actual answer generation instantaneous.

The explicit loading default is **30 minutes**. This is a memory-retention request to Ollama. Later inference calls can supply a different retention duration.

Loading a particular model does not change the selected default model.

## 8. Resolve which model should answer

Before an inference request, the service determines the usable model:

1. Use a supplied per-request model or the saved default.
2. Prefer the matching installed model, respecting its tag.
3. If an explicit per-request model is missing, report an error rather than substitute an unrelated model.
4. If the saved default is missing, try installed models matching the hardware recommendation tags.
5. If no suitable recommendation is installed, explain that a model must be downloaded.

The fallback does not rewrite the saved preference. The model status exposes the available resolved model.

There is one language policy inside this module: Roman Urdu and Urdu-script chat prefer `llama3.2:3b` when an appropriate matching installation exists. Otherwise they use the normal selected-model resolution. Other modules pass the language to the service rather than containing that model-family policy themselves.

This means the saved default and the model used for an Urdu request can differ. Streaming chat includes the actual resolved choice in its model metadata when inference is used.

## 9. Generate the answer through the shared interface

Other backend modules import `llm` from `app.core.llm` and call these methods:

| Method | Used for |
| --- | --- |
| `generate(prompt, system_prompt=...)` | A single prompt, such as a report narrative |
| `chat(messages=...)` | A conversation returned as one complete answer |
| `chat_stream(messages=...)` | A conversation returned in small text pieces |
| `resolve_chat_model(language)` | Choosing the available chat model under the language policy |

All three inference methods currently use Ollama's chat interface internally. They resolve the model, prepare generation options, request the answer, and return text to the caller.

The default options include temperature `0.2`, an output-token limit of `256`, and a context length of `1536`. Individual callers can override options.

The RAG module prepares relevant records and messages. The reporting module prepares its narrative prompt. The anomaly explainer prepares its explanation request. Each uses the same service to perform inference. They do not need separate Ollama client implementations for Qwen, Phi, or Mistral.

Some responses do not require a language model. Greetings such as “hi”, deterministic analytics, and certain record lookups can return directly from the chat pipeline. Their streaming responses still send a valid text chunk to the frontend.

## 10. Clean and display the answer

For a complete answer, the service removes supported `<think>` reasoning sections and certain echoed debug content before returning text.

For a streaming answer, it keeps a small buffer for possible reasoning-tag prefixes. It can handle a tag split across several chunks, suppress text inside reasoning sections, and preserve answer text after a closing tag.

The chat layer packages text pieces as JSON lines. The frontend reads them and updates the assistant's message as content arrives.

## 11. Keep memory available, then release it

Normal generation and chat requests default to a **five-minute** `keep_alive` value. This asks Ollama to retain the model between requests and unload it after that idle period.

For immediate release, press **Unload**. The service sends an empty prompt with `keep_alive=0`.

Unloading:

- Releases the model's memory through Ollama.
- Preserves its downloaded files.
- Preserves the selected default.
- Allows a later inference request to load the model again.

To inspect memory state, `/api/models/running` asks Ollama which models are loaded and reports their sizes, GPU-memory use, and expiry information. Inspection errors are reported rather than silently presented as successful empty results.

Deleting a model is a different operation. The delete API removes its stored files through Ollama. It does not select a replacement or rewrite the saved preference; a later request follows the normal resolution and fallback rules.

## 12. Configuration and API reference

The principal settings in `backend/app/core/config.py` are:

| Setting | Default | Meaning |
| --- | --- | --- |
| `ollama_host` | `http://127.0.0.1:11434` | Ollama server address |
| `llm_model` | `qwen2.5:1.5b` | Default when no valid saved selection exists |
| `llm_keep_alive` | `5m` | Retention after normal generation |
| `llm_keep_alive_chat` | `5m` | Retention after chat inference |
| `llm_temperature` | `0.2` | Default generation randomness |
| `llm_num_predict` | `256` | Default output-token limit |

Settings support the `KONNECT_` environment prefix, for example `KONNECT_OLLAMA_HOST`. A valid saved model preference takes precedence over the configured default during service initialization. For local execution, keep the Ollama address pointing to the local server.

| API | Purpose |
| --- | --- |
| `GET /api/models` | Installed names, saved selection, hardware, engine status |
| `GET /api/models?detailed=true` | Installed model metadata including sizes |
| `GET /api/models/hardware` | Hardware profile and suggestions |
| `GET /api/models/running` | Loaded models and memory information |
| `GET /api/models/info/{model_name}` | Information about a specific model |
| `POST /api/models/pull` | Download a model |
| `POST /api/models/select` | Save the default model |
| `POST /api/models/load` | Pre-load a model |
| `POST /api/models/unload` | Release a model from memory |
| `DELETE /api/models/{model_name}` | Remove downloaded model files |
| `GET /api/chat/models` | Chat-compatible model-list/status API |
| `POST /api/chat/models/select` | Selection through the same shared service |

## 13. Verification and future tests

The current review ran **55 passing tests** across:

- `backend/tests/test_local_llm_inference.py`
- `backend/tests/test_llm_regressions.py`
- `backend/tests/test_chat_greeting_stream.py`

These cover hardware profiles, model resolution, selection and persistence, mocked pulling and progress errors, memory-management calls, inference, streaming cleanup, APIs, concurrent selection, offline status polling, and greeting-stream behavior.

The opt-in `backend/tests/test_ollama_live.py` verifies a real downloaded model by loading it, checking that it appears in running models, generating a nonempty answer, unloading it, and checking that it no longer appears. **This live test passed with `qwen2.5:1.5b` during this review.** It does not download models or change the saved selection. It skips a model that is already loaded to avoid evicting an existing session.

Use the permanent runner from the project root:

```powershell
./scripts/test-backend.ps1 -TestArgs @(
    'tests/test_local_llm_inference.py',
    'tests/test_llm_regressions.py',
    'tests/test_chat_greeting_stream.py',
    '-q'
)
```

For the live check, with Ollama running and the specified model downloaded:

```powershell
$env:KONNECT_TEST_OLLAMA_LIVE = '1'
$env:KONNECT_TEST_MODEL = 'qwen2.5:1.5b'
try {
    ./scripts/test-backend.ps1 -TestArgs @('tests/test_ollama_live.py', '-q')
} finally {
    Remove-Item Env:KONNECT_TEST_OLLAMA_LIVE
    Remove-Item Env:KONNECT_TEST_MODEL
}
```

The runner checks available runtimes and application imports before starting pytest. It can reuse the working project-local portable Python or provision a separate `.venv-tests` environment using a working installed Python. It avoids relying on the broken Windows Store virtual-environment launcher and gives each run its own temporary test directory.

Download behavior is verified with mocked Ollama responses; this review does not claim a fresh real download of every model family. Hardware variants are mostly checked through simulated readings. Real lifecycle verification on the available Qwen model does not establish performance or compatibility for all Phi/Mistral/GPU combinations.

This completion assessment covers Module 6.1. It does not declare every unrelated project module complete or every backend test passing.

## Main implementation files

- [Shared inference service](../backend/app/core/llm.py)
- [Configuration](../backend/app/core/config.py)
- [Model management APIs](../backend/app/api/models.py)
- [Chat APIs](../backend/app/api/chat.py)
- [RAG integration](../backend/app/rag/chat.py)
- [Settings controls](../desktop/src/components/settings/ModelSettings.tsx)
- [Permanent test runner](../scripts/test-backend.ps1)
