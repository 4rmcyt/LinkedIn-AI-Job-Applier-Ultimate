"""This module is used to run the LinkedIn bot"""

import asyncio
import os
import time
import traceback
from pathlib import Path
from threading import Lock

import dotenv

# Try to import pynput for keyboard control (optional, not available in Docker)
try:
    from pynput import keyboard as pynput_kb

    PYNPUT_AVAILABLE = True
except (ImportError, Exception) as e:
    PYNPUT_AVAILABLE = False
    pynput_kb = None

from config.app_config import RESTART_EVERY_DAY
from config.constants import BROWSER_STORAGE_STATE, RESUME_DIR, SEARCH_CONFIG_FILE
from config.logger_config import logger
from src.dashboard.runtime import StopRequested, emit_event, get_control_state
from src.job_manager.authenticator import LinkedInAuthenticator

# Commented out hh.ru specific imports - will be used later
from src.job_manager.bot_facade import BotFacade
from src.job_manager.job_manager import JobApplier
from src.job_manager.resume_anonymizer import ResumeAnonymizer
from src.job_manager.search_customizer import SearchCustomizer
from src.llm.apply_agent import ApplyAgent
from src.llm.llm_manager import GPTAnswerer
from src.pydantic_models.config_models import SearchConfig, Secrets
from src.pydantic_models.prompt_models import ResumeStructure
from src.resume_builder.resume_generator import ResumeGenerator
from src.resume_builder.resume_manager import ResumeManager
from src.resume_builder.style_manager import StyleManager
from src.utils.browser_utils import create_playwright_browser, save_browser_session
from src.utils.utils import (
    get_first_pdf_file,
    load_yaml_file,
    save_yaml_file,
    validate_and_prompt_resume_completion,
)

# Create necessary directories if they don't exist
os.makedirs(RESUME_DIR, exist_ok=True)

# Resume file paths
RESUME_STRUCTURED_FILE = Path(RESUME_DIR) / "structured_resume.yaml"
RESUME_TEXT_FILE = Path(RESUME_DIR) / "resume_text.txt"

READY_MADE_RESUME = get_first_pdf_file(Path(RESUME_DIR))

# Global pause state for keyboard control
paused = False
pause_lock = Lock()
ctrl_pressed = False
last_dashboard_pause_state = False


class ConfigError(Exception):
    pass


class ConfigValidator:
    """Class for validating configuration settings"""

    def validate_search_config(self, config_yaml_path: Path) -> dict:
        """Validate LinkedIn search configuration settings"""
        try:
            parameters = load_yaml_file(config_yaml_path)
            parameters = SearchConfig(**parameters)
            logger.debug("LinkedIn search config loaded successfully.")
            return parameters.model_dump()
        except Exception as e:
            raise ConfigError(f"LinkedIn configuration validation error: {str(e)}")

    @staticmethod
    def validate_secrets() -> dict:
        """Check for LinkedIn secret keys"""
        secrets = {**dotenv.dotenv_values(".env")}
        try:
            # Check for required LinkedIn credentials
            required_keys = ["linkedin_email", "linkedin_password"]
            missing_keys = [key for key in required_keys if key not in secrets or not secrets[key]]

            if missing_keys:
                raise ValueError(f"Missing required keys: {', '.join(missing_keys)}")

            # Still validate with Pydantic if we have the full secrets structure
            secrets_config = Secrets(**secrets)
            logger.debug("LinkedIn secrets validated successfully.")
            return secrets_config.model_dump()
        except Exception as e:
            raise ConfigError(f"Secrets validation error: {str(e)}")

    @staticmethod
    def validate_resume_text(resume_file: Path) -> str:
        """Check for resume file"""
        try:
            with open(resume_file, "r", encoding="utf-8") as f:
                resume_text = f.read()
                if not resume_text:
                    raise ConfigError("Resume not found")
                return resume_text
        except FileNotFoundError:
            return ""
        except Exception as e:
            raise ConfigError(f"Resume validation error: {str(e)}")

    @staticmethod
    def validate_resume_structured(resume_structured_file: Path) -> dict:
        """Check for structured resume file"""
        try:
            resume_structured = load_yaml_file(resume_structured_file)
            resume_structured = ResumeStructure(**resume_structured)
            return resume_structured.model_dump()
        except Exception as e:
            if str(e).startswith("File not found"):
                logger.warning("Resume template not found, creating new one")
                return {}
            raise ConfigError(f"Structured resume validation error: {str(e)}")


def on_press(key):
    """Handle key press events"""
    if not PYNPUT_AVAILABLE:
        return

    global paused, ctrl_pressed
    try:
        # Track Ctrl key state
        if key in (pynput_kb.Key.ctrl_l, pynput_kb.Key.ctrl_r):
            ctrl_pressed = True
        # Check for 'x' key when Ctrl is pressed
        elif hasattr(key, "char") and key.char == "x" and ctrl_pressed:
            with pause_lock:
                paused = not paused
                if paused:
                    logger.warning("⏸️  PAUSED - Press Ctrl+X to continue")
                    emit_event("pause_state_changed", "Keyboard pause requested", paused=True)
                else:
                    logger.info("▶️  RESUMED")
                    emit_event("pause_state_changed", "Keyboard resume requested", paused=False)
    except AttributeError:
        pass


def on_release(key):
    """Handle key release events"""
    if not PYNPUT_AVAILABLE:
        return

    global ctrl_pressed
    # Reset Ctrl key state
    if key in (pynput_kb.Key.ctrl_l, pynput_kb.Key.ctrl_r):
        ctrl_pressed = False


def start_keyboard_listener():
    """Start keyboard listener in background thread"""
    if not PYNPUT_AVAILABLE:
        logger.info("Keyboard control disabled (pynput not available - Docker/headless mode)")
        return

    try:
        listener = pynput_kb.Listener(on_press=on_press, on_release=on_release)
        listener.daemon = True
        listener.start()
        logger.info("Keyboard listener started - Press Ctrl+X to pause/resume")
    except Exception as e:
        logger.warning(f"Could not start keyboard listener: {e}")


async def check_pause():
    """Check if execution is paused and wait if needed"""
    global last_dashboard_pause_state, paused

    control = get_control_state()
    effective_paused = paused or control.get("pause_requested", False)

    if control.get("stop_requested"):
        raise StopRequested("Dashboard requested stop")

    if effective_paused != last_dashboard_pause_state:
        emit_event(
            "pause_state_changed",
            f"Execution {'paused' if effective_paused else 'resumed'}",
            paused=effective_paused,
            source="dashboard" if control.get("pause_requested") and not paused else "keyboard",
        )
        last_dashboard_pause_state = effective_paused

    while paused or get_control_state().get("pause_requested", False):
        if get_control_state().get("stop_requested"):
            raise StopRequested("Dashboard requested stop")
        await asyncio.sleep(0.5)

    if last_dashboard_pause_state:
        emit_event("pause_state_changed", "Execution resumed", paused=False)
        last_dashboard_pause_state = False


async def create_and_run_bot(
    search_config: dict,
    secrets: dict,
    resume_text: str,
    resume_structured: dict,
):
    """Start LinkedIn bot (async)"""
    logger.info("Initializing LinkedIn bot...")
    emit_event(
        "run_started",
        "LinkedIn bot run started",
        positions=search_config.get("positions", []),
        locations=search_config.get("locations", []),
    )

    # Initialize browser Playwright based on configuration
    try:
        browser, context, page = await create_playwright_browser()
        logger.info("Playwright browser initialized successfully")
        emit_event("browser_initialized", "Playwright browser initialized")

    except Exception as e:
        logger.error(f"Browser initialization error: {e}")
        emit_event("run_failed", "Browser initialization failed", error=str(e))
        raise RuntimeError(f"Failed to initialize browser: {e}")

    try:
        # Initialize LinkedIn authenticator
        authenticator = LinkedInAuthenticator(page)
        linkedin_email = secrets["linkedin_email"]
        linkedin_password = secrets["linkedin_password"]
        authenticator.set_parameters(linkedin_email, linkedin_password)

        # Attempt LinkedIn login
        login_success = await authenticator.start()
        if login_success:
            await save_browser_session(context)
            logger.info("Successfully logged into LinkedIn!")
            logger.info("LinkedIn bot ready to work")
            emit_event("login_success", "LinkedIn login succeeded")
        else:
            logger.error("Failed to log into LinkedIn")
            emit_event("run_failed", "LinkedIn login failed")
            return False

        # Set GPT answerer
        llm_api_key = secrets.get("llm_api_key")
        llm_proxy = secrets.get("llm_proxy")
        llm_api_url = secrets.get("llm_api_url")
        llm_answerer_component = GPTAnswerer(llm_api_key, llm_proxy, llm_api_url)
        llm_agent_component = ApplyAgent(
            llm_api_key, BROWSER_STORAGE_STATE, llm_api_url, linkedin_email
        )

        if not resume_structured:
            resume_structured = llm_answerer_component.parse_resume(resume_text)
            resume_structured = ResumeStructure(**resume_structured).model_dump()
            save_yaml_file(RESUME_STRUCTURED_FILE, resume_structured)

        # Set resume anonymizer and anonymize the resume information
        resume_anonymizer = ResumeAnonymizer(resume_structured)
        resume_anonymizer.anonymize_personal_information()
        resume_structured = resume_anonymizer.resume_anonymized
        resume_text_anonymized = resume_anonymizer.anonymize_text(resume_text)

        # Set GPT resume generator
        style_manager = StyleManager()
        resume_generator = ResumeGenerator(llm_answerer_component, resume_anonymizer)
        resume_generator_manager = ResumeManager(llm_api_key, style_manager, resume_generator)

        if not READY_MADE_RESUME.resolve().is_file():
            resume_generator_manager.choose_style()

        # Set search component
        search_component = SearchCustomizer(page)

        # Set apply component
        apply_component = JobApplier(page, linkedin_email, resume_anonymizer, search_component)

        # Set bot facade
        bot = BotFacade(resume_anonymizer, search_component, apply_component, llm_agent_component)
        bot.set_parameters(search_config)
        bot.set_pause_checker(check_pause)

        # Check if the last search was less than a day ago
        if RESTART_EVERY_DAY and not apply_component.check_the_last_search_time():
            logger.warning(
                "Last search was less than a day ago, finishing work. If you want to restart the search, delete the file data/output/last_run.yaml file"
            )
            emit_event(
                "run_stopped", "Run skipped because the daily restart window is still active"
            )
            return True

        # Validate structured resume and prompt user if needed
        if not validate_and_prompt_resume_completion(
            resume_structured, RESUME_STRUCTURED_FILE, RESUME_TEXT_FILE
        ):
            logger.info("User chose to exit and complete resume information")
            emit_event("run_stopped", "Run stopped because resume validation was not accepted")
            return False

        await bot.set_search_parameters(search_config)
        bot.set_answerer_and_agent(llm_answerer_component, llm_agent_component, search_config)
        bot.set_resume(resume_structured, resume_text, resume_text_anonymized)
        if not READY_MADE_RESUME.resolve().is_file():
            bot.set_resume_generator(resume_generator_manager)
        await bot.start_apply()
        emit_event("run_completed", "LinkedIn bot run completed successfully")

    finally:
        # Cleanup browser resources
        logger.info("Cleaning up browser resources...")
        try:
            await save_browser_session(context)
            # Close Playwright browser
            await browser.close()
            logger.info("Playwright browser closed")
            emit_event("browser_closed", "Playwright browser closed")

        except Exception as e:
            logger.warning(f"Error during browser cleanup: {e}")


def main() -> None:
    # Start keyboard listener for pause/resume functionality
    start_keyboard_listener()

    while True:
        should_exit = False
        try:
            # create output folder if it doesn't exist
            data = Path("data")
            output_folder = data / "output"
            output_folder.mkdir(exist_ok=True)

            # validate config files
            config_validator = ConfigValidator()
            secrets = config_validator.validate_secrets()
            search_config = config_validator.validate_search_config(SEARCH_CONFIG_FILE)
            resume_text = config_validator.validate_resume_text(RESUME_TEXT_FILE)
            resume_structured = config_validator.validate_resume_structured(RESUME_STRUCTURED_FILE)

            if not resume_text and not resume_structured:
                raise FileNotFoundError(
                    f"Can't find neither resume text file {RESUME_TEXT_FILE} nor resume structured file {RESUME_STRUCTURED_FILE}"
                )

            logger.info("Starting LinkedIn Job Applier...")
            logger.info(f"Search config loaded with {len(search_config)} parameters")

            # Run LinkedIn bot (async)
            asyncio.run(create_and_run_bot(search_config, secrets, resume_text, resume_structured))
            logger.info("LinkedIn bot completed successfully")

        except StopRequested as stop_requested:
            logger.warning(str(stop_requested))
            emit_event("run_stopped", "Run stopped gracefully by dashboard")
            should_exit = True

        except ConfigError as ce:
            logger.error(f"Configuration error: {str(ce)}")
            emit_event("run_failed", "Configuration error", error=str(ce))
        except FileNotFoundError as fnf:
            tb_str = traceback.format_exc()
            logger.error(f"File not found: {str(fnf)}\n{tb_str}")
            emit_event("run_failed", "Required file was not found", error=str(fnf))
        except RuntimeError as re:
            tb_str = traceback.format_exc()
            logger.error(f"Runtime error: {str(re)}\n{tb_str}")
            emit_event("run_failed", "Runtime error", error=str(re))
        except Exception as e:
            tb_str = traceback.format_exc()
            logger.error(f"Unknown error: {str(e)}\n{tb_str}")
            emit_event("run_failed", "Unhandled exception", error=str(e))
        finally:
            logger.info("Program completed")
            # Wait 1 hour total before next run
            if RESTART_EVERY_DAY and not should_exit:
                logger.info("Waiting 1 hour before next run")
                time.sleep(3600)
            else:
                logger.info("Exiting program")
                should_exit = True

        if should_exit:
            break


if __name__ == "__main__":
    main()
