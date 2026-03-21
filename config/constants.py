# Dummy personal data for anonymization (male)
DUMMY_PERSONAL_INFO_MALE = {
    "name": "Zorquill Thalaorix",
    "last_name_2": "Zorquill Thalaorix",
    # "birthday": "03.05.1993",
    "phone": "9335753535",
    "email": "zorquill_thalaorix@gmail.com",
    "linkedin": "https://linkedin.com/in/zorquill_thalaorix-f3e57c712",
    "github": "https://github.com/zorquill_thalaorix",
    "zip_code": "09876",
    "address": "1234 Imaginary Lane",
}

# Dummy personal data for anonymization (female)
DUMMY_PERSONAL_INFO_FEMALE = {
    "name": "Zorquillia Thalaorix",
    "last_name_2": "Zorquillia Thalaorix",
    # "birthday": "03.05.1993",
    "phone": "9335753535",
    "email": "zorquillia_thalaorix@gmail.com",
    "linkedin": "https://linkedin.com/in/zorquillia_thalaorix-f3e57c712",
    "github": "https://github.com/zorquillia_thalaorix",
    "zip_code": "09876",
    "address": "1234 Imaginary Lane",
}

# Paths to log files and settings
SEARCH_CONFIG_FILE = "config/search_config.yaml"
LAST_RUN_FILE = "data/output/last_run.yaml"
ANSWERS_FILE = "data/output/answers.yaml"
OUTPUT_DIR = "data/output"
LOG_DIR = "logs"
RESUME_DIR = "data/resumes"
COVER_LETTER_DIR = "data/cover_letters"
BROWSER_STORAGE_STATE = "browser_session/linkedin_state.json"
INDEED_BROWSER_STORAGE_STATE = "browser_session/indeed_state.json"
APP_CONFIG_FILE = "config/app_config.yaml"

# Default cost per token in case when model is not supported by litellm
CUSTOM_COST_PER_TOKEN = {
    "input_cost_per_token": 0.25 / 1_000_000,
    "output_cost_per_token": 1.50 / 1_000_000,
}
