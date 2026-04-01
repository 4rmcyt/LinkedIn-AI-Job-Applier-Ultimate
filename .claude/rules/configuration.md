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
- `DEBUG_MODE` — saves screenshots + page HTML to `data/debug/` on every selector failure; also enables Playwright tracing (saved to `data/debug/trace.zip` on exit, viewable at `trace.playwright.dev`)

### Search Config (`config/search_config.yaml`)
Copy from `examples/config/search_config.yaml`. Defines positions, locations, remote/hybrid/onsite, experience levels, company blacklists.

## Output Files (`data/output/`)

- `answers.yaml` — cached question answers (reused across runs)
- `success.yaml` / `failed.yaml` / `skipped.yaml` — application results
- `interesting_jobs.yaml` — LLM-flagged interesting jobs with scores
- `skill_stat.yaml` — aggregated skill frequency statistics
- `last_run.yaml` — scheduling cache (delete to force immediate run)
- `resume_recommendations.txt` — AI resume improvement suggestions
