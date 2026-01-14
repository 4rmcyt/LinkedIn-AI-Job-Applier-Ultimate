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
APP_CONFIG_FILE = "config/app_config.yaml"

# Dictionary for calculating model request cost
PRICE_DICT = {
    "gpt-4o": {
        "price_per_input_token": 2.5e-6,
        "price_per_output_token": 1e-5,
    },
    "gpt-4o-mini": {
        "price_per_input_token": 1.5e-7,
        "price_per_output_token": 6e-7,
    },
    "gemini-2.0-flash": {
        "price_per_input_token": 1e-7,
        "price_per_output_token": 4e-7,
    },
    "gemini-2.5-flash-lite": {
        "price_per_input_token": 1e-7,
        "price_per_output_token": 4e-7,
    },
    "gemini-2.5-flash": {
        "price_per_input_token": 3e-7,
        "price_per_output_token": 2.5e-6,
    },
}
