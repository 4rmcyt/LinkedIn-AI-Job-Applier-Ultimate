# LinkedIn AI Job Applier Ultimate - CLAUDE.md

## Project Overview

AI-powered job application bot that automates job search, resume generation, and form filling using Playwright browser automation and LLM integration. Supports **LinkedIn** and **Indeed**. Active fork of [Jobs_Applier_AI_Agent_AIHawk](https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk).

## Running Commands

Always use `uv run` to execute Python files within the project environment:

```bash
uv run python main.py
uv run python <python_file_name>
uv run pytest tests/
```

## Architecture

### Entry Point
- [main.py](main.py) — single entry point; initializes Playwright, authenticates, and coordinates all components

### Core Components

| Layer | Path | Purpose |
|---|---|---|
| Job Management | `src/job_manager/` | Job automation core (LinkedIn + Indeed) |
| AI Integration | `src/llm/` | Multi-model LLM interface |
| Resume Builder | `src/resume_builder/` | HTML/PDF resume generation |
| Pydantic Models | `src/pydantic_models/` | Data validation |
| Utilities | `src/utils/` | Shared helpers, browser utils |
| Telegram | `src/telegram/` | Notifications and error reporting |

### Job Site Implementations

`src/job_manager/` contains abstract base classes and platform-specific subpackages selected at runtime via `JOB_SITE` in `app_config.py`:

**Base classes (shared):**
- `src/job_manager/job_manager.py` — `BaseJobManager` ABC: output helpers, YAML I/O, blacklist, dedup
- `src/job_manager/easy_applier.py` — `BaseEasyApplier` ABC: `_process_form_section`, `_save_questions`, `_load_questions`, `NoInfoException`

**Platform-specific implementations:**

| File | LinkedIn | Indeed |
|---|---|---|
| `authenticator.py` | Session-file based; detects feed content | No session file; detects sign-in button absence |
| `job_manager.py` | `LinkedInJobManager(BaseJobManager)` | `IndeedJobManager(BaseJobManager)` |
| `search_customizer.py` | Navigates LinkedIn UI to set filters | Builds parameterized URLs directly |
| `easy_applier.py` | `LinkedInEasyApplier(BaseEasyApplier)`; PDF generation | `IndeedEasyApplier(BaseEasyApplier)`; modal-based flow; no PDF |

Both easy applier implementations cache question answers in `data/output/answers.yaml` and consult the cache before calling the LLM.

`main.py` selects the implementation at startup:
```python
if JOB_SITE == "indeed":
    from src.job_manager.indeed.authenticator import IndeedAuthenticator as Authenticator
    ...
else:  # "linkedin"
    from src.job_manager.linkedin.authenticator import LinkedInAuthenticator as Authenticator
    ...
```

## File Structure

```
src/job_manager/
├── bot_facade.py         # Unified facade for both platforms
├── job_manager.py        # BaseJobManager ABC (shared output helpers, YAML I/O)
├── easy_applier.py       # BaseEasyApplier ABC (form dispatch, question cache)
├── resume_anonymizer.py  # Strips PII from resume before LLM calls
├── linkedin/
│   ├── authenticator.py  # LinkedIn session management
│   ├── job_manager.py    # LinkedInJobManager
│   ├── easy_applier.py   # LinkedInEasyApplier (PDF generation, complex forms)
│   └── search_customizer.py # LinkedIn UI-based filter navigation
└── indeed/
    ├── authenticator.py  # Indeed session management
    ├── job_manager.py    # IndeedJobManager
    ├── easy_applier.py   # IndeedEasyApplier (modal-based flow)
    └── search_customizer.py # Indeed URL-based search builder

config/
├── app_config.py         # Runtime behavior settings
├── constants.py          # File paths, pricing, dummy data
├── logger_config.py      # Logging configuration
└── search_config.yaml    # Job search parameters (used by both platforms)

data/
├── resumes/              # resume_text.txt, structured_resume.yaml, PDFs
│   └── generated_resumes/
├── output/               # success.yaml, failed.yaml, skipped.yaml, etc.
└── cover_letters/

browser_session/
└── browser_state.json    # Persisted Playwright auth state (shared by all sites)

logs/
└── llm_api_calls.yaml    # LLM token usage and cost tracking
└── app.log               # Logs all application messages
└── error_log.log         # Logs all error messages
└── internal_logger.log   # Logs all errors that appear before loguru is imported
```

## Project rules

See `.claude/rules/` directory for detailed rules.
