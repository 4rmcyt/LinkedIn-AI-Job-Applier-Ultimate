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

`src/job_manager/` contains platform-specific subpackages selected at runtime via `JOB_SITE` in `app_config.py`:

| File | LinkedIn | Indeed |
|---|---|---|
| `authenticator.py` | Session-file based; detects feed content | No session file; detects sign-in button absence |
| `job_manager.py` | `JobApplier` | `IndeedJobApplier` |
| `search_customizer.py` | Navigates LinkedIn UI to set filters | Builds parameterized URLs directly |
| `easy_applier.py` | Complex forms; PDF generation; question caching | Simpler modal-based flow; no PDF generation |

`main.py` selects the implementation at startup:
```python
if JOB_SITE == "indeed":
    from src.job_manager.indeed.authenticator import IndeedAuthenticator as Authenticator
    ...
else:  # "linkedin"
    from src.job_manager.linkedin.authenticator import LinkedInAuthenticator as Authenticator
    ...
```

### Key Files

- [src/job_manager/bot_facade.py](src/job_manager/bot_facade.py) — Facade pattern; unified interface for both platforms
- [src/job_manager/linkedin/authenticator.py](src/job_manager/linkedin/authenticator.py) — LinkedIn session management
- [src/job_manager/linkedin/job_manager.py](src/job_manager/linkedin/job_manager.py) — LinkedIn job search orchestrator
- [src/job_manager/linkedin/easy_applier.py](src/job_manager/linkedin/easy_applier.py) — LinkedIn Easy Apply automation
- [src/job_manager/linkedin/search_customizer.py](src/job_manager/linkedin/search_customizer.py) — LinkedIn UI-based filter navigation
- [src/job_manager/indeed/authenticator.py](src/job_manager/indeed/authenticator.py) — Indeed session management
- [src/job_manager/indeed/job_manager.py](src/job_manager/indeed/job_manager.py) — Indeed job search orchestrator
- [src/job_manager/indeed/easy_applier.py](src/job_manager/indeed/easy_applier.py) — Indeed Easy Apply modal automation
- [src/job_manager/indeed/search_customizer.py](src/job_manager/indeed/search_customizer.py) — Indeed URL-based search parameter builder
- [src/llm/llm_manager.py](src/llm/llm_manager.py) — Multi-model LLM interface (`GPTAnswerer` class)
- [src/llm/prompts.py](src/llm/prompts.py) — All prompts centralized here
- [src/llm/apply_agent.py](src/llm/apply_agent.py) — browser-use AI agent for Non-Easy Apply (experimental, LinkedIn only)
- [src/utils/browser_utils.py](src/utils/browser_utils.py) — Playwright browser creation and session management
- [config/app_config.py](config/app_config.py) — Runtime behavior flags and LLM settings
- [config/constants.py](config/constants.py) — File paths, pricing, dummy data constants
- [config/logger_config.py](config/logger_config.py) — Centralized logger

## Configuration

### Secrets (`.env`)
```env
# LinkedIn credentials (when JOB_SITE="linkedin")
linkedin_email="..."
linkedin_password="..."

# Indeed credentials (when JOB_SITE="indeed")
# indeed_email="..."
# indeed_password="..."

llm_api_key="..."
llm_proxy="..."         # optional
tg_token="..."          # optional
tg_chat_id="..."        # optional
```

### Runtime Flags (`config/app_config.py`)
- `JOB_SITE` — target platform: `"linkedin"` (default) or `"indeed"`
- `MAX_APPLIES_NUM` — max applications per run
- `HEADLESS_MODE` — headless browser (required for Docker)
- `MONKEY_MODE` — skip LLM job filtering, apply to all
- `TEST_MODE` — generate resumes/cover letters without submitting
- `COLLECT_INFO_MODE` — gather stats only, no applications
- `EASY_APPLY_ONLY_MODE` — skip non-Easy Apply jobs (LinkedIn only; Indeed always uses Easy Apply flow)
- `RESTART_EVERY_DAY` — auto-restart every 24h (LinkedIn only)
- `LLM_MODEL_TYPE` — provider: `"gemini"`, `"openai"`, `"openrouter"`, `"claude"`, `"ollama"`
- `EASY_APPLY_MODEL` — model name for Easy Apply
- `APPLY_AGENT_MODEL` — model name for Non-Easy Apply agent (LinkedIn only)
- `JOB_IS_INTERESTING_THRESH` — LLM interest score threshold (1-100)
- `MINIMUM_WAIT_TIME_SEC` — minimum seconds per application (rate limiting)
- `FREE_TIER` / `FREE_TIER_RPM_LIMIT` — RPM throttling for free-tier LLMs

### Search Config (`config/search_config.yaml`)
Copy from `examples/config/search_config.yaml`. Defines positions, locations, remote/hybrid/onsite, experience levels, company blacklists.

## File Structure

```
src/job_manager/
├── bot_facade.py         # Unified facade for both platforms
├── linkedin/
│   ├── authenticator.py  # LinkedIn session management
│   ├── job_manager.py    # LinkedIn job search orchestrator
│   ├── easy_applier.py   # LinkedIn Easy Apply automation
│   └── search_customizer.py # LinkedIn UI-based filter navigation
└── indeed/
    ├── authenticator.py  # Indeed session management
    ├── job_manager.py    # Indeed job search orchestrator
    ├── easy_applier.py   # Indeed Easy Apply modal automation
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
├── linkedin_state.json   # Persisted LinkedIn Playwright auth state
└── indeed_state.json     # Persisted Indeed Playwright auth state

logs/
└── llm_api_calls.yaml    # LLM token usage and cost tracking
```

## Code Standards

### Logging
```python
from config.logger_config import logger

logger.info("...")
logger.debug("...")
logger.warning("...")
logger.error("...", exc_info=True)
```

### Imports Order
1. Standard library
2. Third-party
3. Local (grouped by module)

### Error Handling
- Catch specific exceptions, never bare `except:`
- Log context (job URL, form field, etc.)
- Continue processing other jobs on single-job failures
- Use `src/telegram/telegram_error_handler.py` for critical errors

### Pydantic Validation
All config and data loaded through models in `src/pydantic_models/`:
- `Secrets`, `SearchConfig` — configuration
- `Job`, `JobManagerCache`, `Question` — job application data
- `ResumeStructure` — resume parsing
- `LLMCall` — LLM cost tracking

## Browser Automation (Playwright)

```python
from src.utils.browser_utils import create_playwright_browser, save_browser_session
from config.constants import BROWSER_STORAGE_STATE

page, context, browser, playwright_instance = await create_playwright_browser(
    storage_state=BROWSER_STORAGE_STATE
)
```

- Use `src/utils/utils.py` `pause()` for human-like random delays
- Prefer CSS selectors, then aria-labels, then XPath
- Always close browser/context/playwright in `finally` blocks
- Session state persisted in `browser_session/linkedin_state.json` (LinkedIn) or `browser_session/indeed_state.json` (Indeed)

## LLM Integration

```python
from src.llm.llm_manager import GPTAnswerer
from config.app_config import LLM_MODEL_TYPE, EASY_APPLY_MODEL

gpt_answerer = GPTAnswerer(api_key=api_key, model_type=LLM_MODEL_TYPE, model=EASY_APPLY_MODEL)
gpt_answerer.set_resume(structured_resume, resume_text)
```

- All prompts in [src/llm/prompts.py](src/llm/prompts.py) — never inline prompts elsewhere
- Validate all LLM responses before use
- Track costs via `LLMCall` model → `logs/llm_api_calls.yaml`

## Testing

```bash
uv run pytest tests/test_authenticator.py
uv run pytest tests/test_resume_anonymizer.py
uv run pytest tests/test_utils.py
uv run pytest  # run all tests
```

- Test files in `tests/` with `test_*.py` naming
- Mock LinkedIn, LLM APIs, Telegram, Playwright for unit tests
- Use `TEST_MODE=True` in app_config for integration testing without real applications

## Resume Generation

Test resume generation independently:
```bash
uv run python src/resume_builder/resume_manager.py
```
Output: `test_generated_resume.pdf` in root directory.

Resume styles: `FAANGPath`, `Cloyola Grey`, `Modern Blue`, `Modern Grey`, `Default`, `Clean Blue`

## Output Files (`data/output/`)

- `answers.yaml` — cached question answers (reused across runs)
- `success.yaml` / `failed.yaml` / `skipped.yaml` — application results
- `interesting_jobs.yaml` — LLM-flagged interesting jobs with scores
- `skill_stat.yaml` — aggregated skill frequency statistics
- `last_run.yaml` — scheduling cache (delete to force immediate run)
- `resume_recommendations.txt` — AI resume improvement suggestions

## Privacy & Security

- Never log passwords, API keys, or personal information
- Resume anonymization replaces personal data before LLM calls (except resume parsing and Non-Easy Apply)
- All secrets in `.env` (never committed); use `.env_example` as template
- Only validate at system boundaries (user input, external APIs)
