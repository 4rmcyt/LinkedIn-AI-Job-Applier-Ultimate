import asyncio
import os
from datetime import datetime
from pathlib import Path

from browser_use import Agent, Browser, ChatAnthropic, ChatGoogle, ChatOllama, ChatOpenAI

from config.app_config import APPLY_AGENT_MODEL, HEADLESS_MODE, LLM_MODEL_TYPE
from config.constants import LOG_DIR, PRICE_DICT, RESUME_DIR
from config.logger_config import logger
from src.pydantic_models.log_models import LLMCall
from src.utils.utils import append_yaml_file


class ApplyAgent:
    def __init__(self, api_key: str, browser_storage_state: str) -> None:
        self.api_key = api_key
        self.model = APPLY_AGENT_MODEL
        self.model_type = LLM_MODEL_TYPE
        self.llm = self.select_model_type(self.model_type)
        self.calls_log = os.path.join(Path(LOG_DIR), "llm_api_calls.yaml")
        self.agent = None
        self.resume_readable = None
        self.browser = Browser(headless=HEADLESS_MODE)

    def select_model_type(self, model_type: str) -> None:
        """Select the model to use."""
        self.model_type = model_type
        if model_type == "gemini":
            llm = ChatGoogle(api_key=self.api_key, model=self.model)
        elif model_type == "openai":
            llm = ChatOpenAI(api_key=self.api_key, model=self.model, reasoning_effort="minimal")
        elif model_type == "claude":
            llm = ChatAnthropic(api_key=self.api_key, model=self.model)
        elif model_type == "ollama":
            llm = ChatOllama(api_key=self.api_key, model=self.model)
        else:
            raise ValueError(f"Unsupported model type: {model_type}")
        return llm

    def set_resume(self, resume_readable: str) -> None:
        """Add resume for analysis."""
        self.resume_readable = resume_readable

    async def apply(self, job_url: str) -> None:
        """Apply to the job using AI Agent"""
        task = f"""
        Go to page with URL {job_url} and apply to the job using information from my resume.
        ## Additional rules:
            - if you can't apply, just finish the task, don't try to apply using different URLs
            - some textboxes may have dropdowns, so after filling the textbox, check if there is a dropdown and if there is, select the correct option
        ## My resume text: {self.resume_readable}
        """
        available_file_paths = [
            str(Path(RESUME_DIR).absolute() / "resume.pdf"),
        ]

        self.agent = Agent(
            task=task,
            browser=self.browser,
            llm=self.llm,
            use_vision=False,
            use_thinking=False,
            save_conversation_path=Path(LOG_DIR).absolute() / "apply_agent_conversation",
            available_file_paths=available_file_paths,
        )
        await self.agent.run()

        self._log_token_usage(task)

    def _log_token_usage(self, task: str) -> None:
        """Log AI Agent token usage and calculate the total cost"""
        prices = PRICE_DICT.get(
            self.model, {"price_per_input_token": 1e-7, "price_per_output_token": 4e-7}
        )
        token_usage = self.agent.token_cost_service.get_usage_tokens_for_model(self.model)
        input_tokens, output_tokens = token_usage.prompt_tokens, token_usage.completion_tokens
        total_tokens = input_tokens + output_tokens
        logger.info(
            f"Token usage - Input: {input_tokens}, Output: {output_tokens}, Total: {total_tokens}"
        )
        price_per_input_token = prices["price_per_input_token"]
        price_per_output_token = prices["price_per_output_token"]
        total_cost = (input_tokens * price_per_input_token) + (
            output_tokens * price_per_output_token
        )
        logger.info(f"Total cost calculated: {total_cost}")

        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            log_entry = LLMCall(
                model_name=self.model,
                timestamp=current_time,
                prompts={
                    "prompt_1": task,
                    "prompt_2": "<Some browser content>",
                },
                parsed_reply="<Some reply from agent>",
                total_tokens=total_tokens,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_cost=total_cost,
            )
            logger.debug(f"Log entry created: {log_entry}")
        except KeyError as e:
            logger.error(f"Error creating log entry: missing key {str(e)} in parsed_reply")
            raise

        append_yaml_file(Path(self.calls_log), log_entry.model_dump())

        return total_cost

    async def apply_to_job(self, job_url: str) -> tuple[str, str]:
        """Apply to job, handling event loop properly"""
        # Directly await the apply method since we're already in an async context
        try:
            await self.apply(job_url)
            return ("Success", "")
        except Exception as e:
            logger.error(f"Error applying to job: {e}")
            return ("Error", str(e))


if __name__ == "__main__":
    """Test ApplyAgent functionality"""
    import traceback

    import dotenv

    from config.constants import BROWSER_STORAGE_STATE
    from src.pydantic_models.prompt_models import ResumeStructure
    from src.utils.utils import load_yaml_file

    async def test_apply_agent():
        """Test ApplyAgent with a real LinkedIn job posting"""
        logger.info("Starting ApplyAgent test...")

        try:
            # Load secrets for LLM
            secrets = dotenv.dotenv_values(".env")
            llm_api_key = secrets.get("llm_api_key", "")

            if not llm_api_key:
                logger.error("❌ LLM API key not found in .env file")
                return False

            # Initialize ApplyAgent
            apply_agent = ApplyAgent(llm_api_key, BROWSER_STORAGE_STATE)
            logger.info("ApplyAgent initialized successfully")

            # Load resume data
            RESUME_STRUCTURED_FILE = Path(RESUME_DIR) / "structured_resume.yaml"
            RESUME_TEXT_FILE = Path(RESUME_DIR) / "resume_text.txt"

            if not RESUME_STRUCTURED_FILE.exists():
                logger.error(f"❌ Resume structured file not found: {RESUME_STRUCTURED_FILE}")
                return False

            if not RESUME_TEXT_FILE.exists():
                logger.error(f"❌ Resume text file not found: {RESUME_TEXT_FILE}")
                return False

            # Load and set resume data
            resume_structured = load_yaml_file(RESUME_STRUCTURED_FILE)
            resume_structured = ResumeStructure(**resume_structured).model_dump()

            with open(RESUME_TEXT_FILE, "r") as f:
                resume_text = f.read()

            # Set resume and job for the agent
            apply_agent.set_resume(resume_text)
            logger.info("Resume and job data set successfully")

            # Test the apply_to_job method
            vacancy_url = "https://topskill.io/jobs/frontend-web-developer-hr1w2"
            logger.info(f"Testing ApplyAgent.apply_to_job method with job: {vacancy_url}")
            logger.info("This will open a browser and attempt to apply to the job...")

            # Run the application
            await apply_agent.apply_to_job(vacancy_url)

            logger.info("✅ ApplyAgent test completed successfully!")
            logger.info("Check the browser window to see the application process")
            return True

        except Exception as e:
            logger.error(f"❌ ApplyAgent test failed with error: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False

    # Run the test
    success = asyncio.run(test_apply_agent())
    if success:
        print("✅ Test passed!")
    else:
        print("❌ Test failed!")
