# LinkedIn AI Job Applier Ultimate - AI-Powered Automated LinkedIn Job Applier


<img src="assets/logo.png" alt="A sample image" width="40%" style="display: block; margin: 20 auto;">


This project is an AI-powered bot that automates the process of applying for jobs on LinkedIn. It intelligently parses your resume, customizes applications, answers questions using an LLM, gathers statistics of the most important for employers skills and sends you detailed reports, significantly streamlining your job search.

This is an active fork of the original [Jobs_Applier_AI_Agent_AIHawk](https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk) project, which is currently inactive. This version introduces numerous new features, bug fixes, and performance improvements.

---

## 📋 Table of Contents

- [✨ Features](#-features)
- [🚀 Getting Started](#-getting-started)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
- [🔧 Configuration](#-configuration)
- [▶️ Usage](#️-usage)
- [✅ Running Tests](#-running-tests)
- [🐞 Troubleshooting](#-troubleshooting)
- [📨 Telegram Instruction](#telegram-instruction)
- [🤝 Contributing](#-contributing)
- [📜 License](#-license)
- [🙏 Acknowledgements](#-acknowledgements)

---

## ✨ Features

This project enhances the original codebase with several powerful new features:

*   **🌐 Universal Job Application:** Applies to **ALL** job vacancies (not just Easy Apply) thanks to the [browser-use](https://github.com/browser-use/browser-use) library.
*   **🔒 Data Anonymization:** Protects your privacy by replacing personal data with mock information before sending it to the LLM provider, ensuring your sensitive information remains secure.
    *   *Note: Auto resume parsing and applying of non-Easy Apply vacancies don't use anonymization. Additionally, country, city, and birth date are not anonymized to maintain the quality of LLM responses.*
*   **🎭 Playwright Integration:** Now uses Playwright instead of Selenium for faster, more reliable and secure browser automation with better performance and modern web standards support.
*   **🖥️ Headless Mode:** Run the bot in headless mode if you want to use the bot in server environment or while working with your computer. This allows the bot to operate without a visible browser window while maintaining full functionality.
*   **📊 Skill Statistics:** Analyzes job descriptions to identify the most in-demand skills, helping you tailor your resume effectively.
*   **🧠 Intelligent Error Handling:** If LinkedIn's Easy Apply feature encounters errors (e.g., incorrectly filled fields), the bot will attempt to fix them automatically.
*   **☑️ Smart Checkbox Handling:** Automatically detects and answers checkbox questions in LinkedIn Easy Apply forms with intelligent context-aware responses.
*   **🔗 Contextual Question Processing:** Considers previous answers when responding to follow-up questions like "If yes/no, who/when/where?" for more accurate and relevant responses.
*   **🤖 AI-Powered Resume Parsing:** Automatically parses your resume from a text file into a structured format using an LLM (Large Language Model).
*   **📄 New Resume Style:** Includes the modern "FAANGPath" resume style for generating professional-looking resumes.
*   **📲 Telegram Integration:** Delivers comprehensive reports and error notifications directly to your Telegram chat after each run.
*   **💡 Resume Recommendations:** Provides AI-generated suggestions to improve your resume based on job market trends.
*   **🌐 Proxy Support:** Supports using proxies for both Gemini and OpenAI models.
*   **🏗️ Robust Configuration:** Uses Pydantic models for validating configuration, resume, and other data structures, reducing runtime errors.
*   **🚀 Improved LLM Logic:**
    *   Utilizes improved system instructions and prompts for higher-quality LLM responses.
    *   Employs Gemini as the default LLM, which is often more cost-effective (sometimes even free!) than OpenAI.
    *   Enhances question-answering logic to avoid LLM "hallucinations" by skipping questions where it lacks sufficient information.
*   **🔐 Secure Secrets Management:** Stores sensitive keys and credentials in a `.env` file for better security.
*   **🕒 Automated Scheduling:** A built-in timer allows the bot to run automatically every 24 hours.
*   **💻 Numerous improvements that simplify development and debugging:** advanced logging, pre-commit hooks, fast and easy installation using uv, etc


## 🚀 Getting Started

### Prerequisites

*   Python 3.12+
*   Git
*   [uv](https://github.com/astral-sh/uv) (optional, for faster installation)

### Installation

1.  **Clone repository and create virtual environment**

    ```bash
    # Clone the repository
    git clone https://github.com/beatwad/LinkedIn_AI_Job_Applier_Ultimate.git
    cd LinkedIn_Job_Applier_Ultimate

    # Create and activate a virtual environment
    python -m venv venv
    source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
    ```

2.  **Install dependencies**
    ```bash
    pip install -r requirements.txt
    ```

    or

    ```bash
    uv pip install -r requirements.txt
    ```

3.  **Install additional soft**
    ```bash
    # Install Chromium browser for Playwright
    playwright install chromium
    ```

## 🔧 Configuration

1.  **Secrets (`.env` file):**
    Create a `.env` file in the root directory by copying the example file:
    ```bash
    cp .env.example .env
    ```
    Now, fill in the required values in your `.env` file:
    ```env
    # Your LinkedIn credentials
    linkedin_email="your_linkedin_email@example.com"
    linkedin_password="your_linkedin_password"

    # Your LLM API Key (e.g., Gemini)
    llm_api_key="your_llm_api_key"

    # Optional proxy for LLM requests
    llm_proxy="http://your_proxy_url:port"

    # Your Telegram Bot Token for sending of error message and reports
    tg_token="your_telegram_bot_token"
    ```

2.  **Job Search Parameters (`config/search_config.yaml`):**
    Customize your job search by editing this file. You can define job titles, locations, experience levels, and more.
    Settings are the same as LinkedIn has. Example of search_config file can be found in `examples/config/search_config.yaml`

    ```yaml
    # Example: search for Mid-Senior level remote software engineer roles
    positions: # the only mandatory setting in search_config file
      - Software engineer

    remote: true
    hybrid: false
    onsite: false

    experience_level:
      mid_senior_level: true
    ```

3.  **Application Settings (`config/app_config.py`):**
    Fine-tune the bot's behavior in this file. Key settings include:
    *   `MAX_APPLIES_NUM`: The maximum number of jobs to apply for in a single run.
    *   `HEADLESS_MODE`: If this mode is activated - the browser will be launched in headless mode. Convinient if you plan to
    use your computer while bot is working + everything works faster.
    *   `MONKEY_MODE`: If `True`, applies to all jobs found. If `False`, the LLM selects only the most suitable jobs.
    *   `TEST_MODE`: If `True`, the bot generates resumes and cover letters but does not actually submit applications.
    *   `COLLECT_INFO_MODE`: If `True`, the bot doesn't apply to the jobs or create resumes and cover letters, only gathers information for interesting jobs and their skill statistics and saves them to the files data/output/interesting_jobs.yaml and data/output/skill_stat.yaml.
    *   `EASY_APPLY_ONLY_MODE`: If `True`, bot applies only the jobs with Easy Apply. Else bot will apply to the jobs with Easy Apply and try to apply to the jobs with 3rd party applications. **WARNING**: applying to the jobs with 3rd party applications is not guaranteed to be successful, but is guaranteed to consume at least 10-100x more tokens!
    *   `RESTART_EVERY_DAY`: If `True`, bot will automatically restart the search every 24 hours when LinkedIn resets the search limits. So you don't have to restart it manually - just run & forget.
    *   `JOB_IS_INTERESTING_THRESH`: LLM evaluated the 'interest' level of the job from 1 to 100. If job 'interest' level not below this threshold - the job is considered interesting for bot. Otherwise not. Because of LinkedIn limits number of daily applications to 50, recommended value of this setting is 70+, so the bot will apply only vacancies that match your resume
    *   `MINIMUM_WAIT_TIME_SEC`: Minimum time spent on one job application, this setting help to prevent ban for too frequent job applies
    *   `TG_CHAT_ID/TG_ERR_TOPIC_ID/TG_REPORT_TOPIC_ID`: Address of your chat in format "@name_of_your_chat" + IDs of topics where bot will send error messages and everyday report on the job applies done. You can find instruction how to create and set your Telegram chat below.
    *   `LLM_MODEL_TYPE`: Choose your LLM provider (e.g., "gemini").
    *   `EASY_APPLY_MODEL`: Specify the exact model to use (e.g., "gemini-2.5-flash").
    *   `REMOTE_BROWSER_CDP_URL`: this se

    ### Supported LLM models

    The bot supports multiple LLM providers. Configure them in `config/app_config.py` using `LLM_MODEL_TYPE` and `EASY_APPLY_MODEL`.

    - **Gemini (Google)**
    - Set: `LLM_MODEL_TYPE = "gemini"`
    - Examples: `gemini-flash-latest`, `gemini-2.5-flash`, `gemini-2.5-flash-lite`, `gemini-2.0-flash`
    - **OpenAI**
    - Set: `LLM_MODEL_TYPE = "openai"`
    - Examples: `gpt-4o`, `gpt-4o-mini`
    - **Claude (Anthropic)**
    - Set: `LLM_MODEL_TYPE = "claude"`
    - Examples: `claude-3-5-sonnet`, `claude-4-opus` (use any valid Claude model ID)
    - **Ollama (local/server)**
    - Set: `LLM_MODEL_TYPE = "ollama"`
    - Examples: `llama3`, `qwen2.5` (any model available in your Ollama)

    Notes:
    - Recommended models: gemini + gemini-flash-latest or openai + gpt-4.1-mini - both are fast, clever and cheap (gemini models can be even free!)
    - Provide your API key in `.env` as `llm_api_key`. Optionally set `llm_proxy`.
    - Model pricing used in reports is taken from an internal map for common models; others fall back to default per-token prices.

4.  **Resume text for LLM (`data/resumes/resume_text.txt`):**
    Resume text must contain information about your first and last names and your gender (that is neccessary for the correct work of anonymization functions)
    You have two options:
    *   **Automatic Parsing (recommended):** Create a `resume_text.txt` file that contatins all available inforamtion about your resume in text format. The bot will use the LLM to parse it into a structured format on the first run. These structured resume data are stored in `data/resumes/structured_resume.yaml`
    *   **Manual Structure:** Create a `resume_text.txt` AND `structured_resume.yaml` files and fill out the second file manually for precise control. Why use this option instead of first? Because if you select first option - all data from your resume text will be sent to LLM for creation of structured_resume file - for some of people how cares about their privacy this would be considered as unacceptable. I want to point out that Automatic Parsing and non-Easy Apply vacancies applying are the only two functions of this bot that send not anonymized user's personal information to LLM. All other bot functions that interact with LLM, anonimyze personal information before sending to LLM.
    Examples of `resume_text.txt` and `structured_resume.yaml` files can be found in `examples/data/resumes` folder

5. **Resume generation:**
    You have two options:
    *   **Ready Made Resume (recommended):** Take your ready-made resume in PDF format, name it as `resume.txt` and put it into `data/resumes/` The bot will use this resume for applying jobs.
    *   **Automatic Creation:** Don't put your ready-made resume in `data/resumes/` and app will create a new resume for every job it applies to. Using this mode, the bot can create resumes tailored to each specific vacancy, but the price is probabiliy of hallucinations which can lead to spoiled resume file. Generated resume will be stored in `data\resumes\generated_resumes\` folder.

    ### How to create resume using bot
    1.  Fill file `data/resumes/resume_text.txt` with information from your resume. Example of resume_text.txt file can be found in `examples` folder.
    2   Run the bot to create file `data/resumes/structured_resume.yaml` and fill it automatically of fill it manually.
    3.  Run this command

        ```bash
        python src/resume_builder/resume_manager.py
        ```
    3.  Select resume style (first style FAANGPath is recommended).
    4.  Output file is `test_generated_resume.pdf` in root directory
    5.  Carefully read the resume, look for **No info** text in it. If you find it - that means that some critical information in your resume text is missing and you must add it to your resume file(s) and repeat the resume creation process.
    6.  If you are satisfied with quality of your resume - you can rename output file to `resume.pdf` and move it to `data/resumes/` folder - bot will use this resume by default.

## ▶️ Usage

Once you have completed the installation and configuration steps, you can run the bot:

```bash
python main.py
```
If bot finds out that there are no information about some fields in your `structured_resume.yaml` file - it will output warning, list of fields with no information and propose two options:
- press `1` to finish bot execution, consider what information is missing and add it to `data/resume/resume_text.txt`. Then delete `structured_resume.yaml` and restart bot OR fill missing fields in `structured_resume.yaml` manually if you don't want LLM to re-generate it automatically because of privacy issues.
- press `2` to continue anyway

If 30 seconds have been passed and yuoyou select `2` or all fields in `structured_resume.yaml` file are filled - the bot will continue work.

The bot will log its progress in the console and create detailed log files in the `logs/` directory. Upon completion, it will send a report to your configured Telegram chat.


### Output files (`data/output/`)

- **answers.yaml**: Stores previously given answers to LinkedIn application questions to reuse across runs and reduce LLM calls.
- **failed.yaml**: Companies and jobs where an application attempt failed due to an error, with reasons.
- **interesting_jobs.yaml**: Jobs flagged as interesting by the LLM along with interest score, reasoning, and extracted key skills, sorted by descending interesting score.
- **last_run.yaml**: Internal cache with timestamps and counters (e.g., `last_run`, `last_apply`, totals) used to enforce the 24-hour scheduling logic.
- **resume_recommendations.txt**: AI-generated recommendations for improving your resume, produced once and reused unless deleted.
- **skill_stat.yaml**: Aggregated statistics of the most frequently requested skills gathered from job descriptions, sorted by descending frequency.
- **skipped.yaml**: Companies and jobs that were intentionally skipped (e.g., blacklist, missing info, not interesting), with reasons.
- **success.yaml**: Companies and jobs where the bot successfully submitted an application, including basic job info.

## ✅ Running Tests

The project includes a suite of tests to ensure its functionality. To run them, first install the development dependencies:

```bash
# Using pip
pip install -r requirements.txt -e ".[dev]"

# Using uv
uv sync --dev
```

Then, run pytest from the root directory:

```bash
pytest
```

## 🐞 Troubleshooting

### 1. Incorrect Information in Job Applications

**Issue:** Bot provides inaccurate data for experience, salary, and notice period

**Solution:**

- Update prompts for professional experience specificity (can be found in `src\llm\prompts.py`)
- Add fields in `structured_resume.yaml` for experience, expected salary, and notice period

### 2. YAML Configuration Errors

**Error Message:**

yaml.scanner.ScannerError: while scanning a simple key

**structured_resume.yaml**

If error happens when `structured_resume.yaml` is processed - delete it and restart the bot - it will parse resume_text.txt file and genereates new correct `structured_resume.yaml` file.

**search_config.yaml**
If error happens when `search_config.yaml` is processed:
- Copy example of `search_config.yaml` from `examples/config/` to `config/` and modify gradually
- Ensure proper YAML indentation and spacing
- Use a YAML validator tool
- Avoid unnecessary special characters or quotes

### 3. Bot Logs In But Doesn't Apply to Jobs

**Issue:** Bot doesn't start at all or starts without applying

**Solution:**

- Delete file `data/output/last_run.yaml` if it exists. It is used for scheduling of bot run every 24 hours, but if previous run was less than 24 hours ago - bot would just stop applying
- Check for security checks or CAPTCHAs
- Verify `search_config.yaml` parameters
- Check how many vacancies can be found on LinkedIn with search settings like in your `search_config.yaml` file (maybe LinkedIn can't find any)
- Ensure your account profile meets job requirements
- Review console output for error messages

### General Troubleshooting Tips

- Use the latest version of the script
- Verify all dependencies are installed and updated
- Check internet connection stability
- Clear browser cache and cookies if issues persist (by deleting `chrome_profile` folder in root directory)

## 📨 Telegram Instruction

1. Create Telegram bot and obtain your token following [this](https://core.telegram.org/bots/tutorial#obtain-your-bot-token) guide for example. Set tg_token variable with your new obtained token in .env file.

2. Create group with topics following [this](https://docs.hetrixtools.com/how-to-enable-telegram-topics/) guide for example

3. Make group public (Tap on group name -> Edit -> Group Type -> Public)

3. Set group's permanent link, e.g. t.me/linkedin_bot_feedback.

4. Set TG_CHAT_ID in `config/app_config.py` with this link, e.g. TG_CHAT_ID = "@linkedin_bot_feedback"

5. Create two topics: one for errors and another for reports

5. Send a message to every topic in the chat. Than click on that message and select *Copy Message Link*. You will get a link like: https://t.me/c/194xxxx987/11/13, so the group Topic ID is 11. Set TG_ERR_TOPIC_ID in `config/app_config.py` with Error Topic ID and TG_REPORT_TOPIC_ID - with Report Topic ID.

## 🤝 Contributing

Contributions are welcome! If you have suggestions for improvements or find a bug, please feel free to open an issue or submit a pull request.

## 📜 License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgements

*   This project is a fork of and builds upon the excellent work of the original [Jobs_Applier_AI_Agent_AIHawk](https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk) project.
*   Non-Easy Apply vacancies are applied using [browser-use](https://github.com/browser-use/browser-use) project.
*   System instructions for the LLM were adapted from this [GitHub repository](https://github.com/DenisSergeevitch/chatgpt-custom-instructions/blob/main/v2.md).
*   The FAANGPath resume style is based on this [Overleaf template](https://www.overleaf.com/latex/templates/faangpath-simple-template/npsfpdqnxmbc).

If you like the project please star ⭐ the repository!
