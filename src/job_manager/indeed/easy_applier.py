import traceback
from pathlib import Path
from typing import Any, List, Tuple

from playwright.sync_api import Page

from config.logger_config import logger
from src.job_manager.resume_anonymizer import ResumeAnonymizer
from src.llm.llm_manager import GPTAnswerer
from src.pydantic_models.job_models import Job, Question
from src.utils.browser_utils import (
    debug_capture,
    find_element_safely,
    find_elements_safely,
    get_clean_text,
)
from src.utils.utils import async_pause, load_yaml_file, sanitize_text, save_yaml_file

INDEED_APPLY_BUTTON_SELECTOR = "button#indeedApplyButton, button[data-jk], .ia-IndeedApplyButton"
INDEED_APPLY_MODAL_SELECTOR = "div.ia-BasePage, div[data-testid='ia-container']"
INDEED_NEXT_BUTTON_SELECTOR = "button[data-testid='continue-button'], button[data-testid^='hp-continue-button'], button[data-testid='ia-continueButton'], .ia-BasePage-component button:has-text('Continue')"
INDEED_SUBMIT_BUTTON_SELECTOR = "button[data-testid='ia-submitButton'], button.ia-submitButton"


class NoInfoException(Exception):
    pass


class IndeedEasyApplier:
    """Handle Indeed 'Easily apply' application forms"""

    def __init__(
        self,
        page: Page,
        gpt_answerer: GPTAnswerer,
        resume_anonymizer: ResumeAnonymizer,
        resume_generator_manager: Any,
        pause_checker: Any,
        answers_file: Path,
        resume_dir: Path,
        cover_letter_dir: Path,
        test_mode: bool,
    ):
        self.page = page
        self.gpt_answerer = gpt_answerer
        self.resume_anonymizer = resume_anonymizer
        self.resume_generator_manager = resume_generator_manager
        self.pause_checker = pause_checker
        self.answers_file = answers_file
        self.resume_dir = resume_dir
        self.cover_letter_dir = cover_letter_dir
        self.test_mode = test_mode
        self.questions: List[Question] = self._load_questions()

        logger.info("IndeedEasyApplier initialized")

    def set_page(self, page: Page) -> None:
        self.page = page

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def apply_to_job(self, job: Job) -> None:
        """Entry point - navigate to job page and apply"""
        logger.info(f"Navigating to Indeed job: {job.url}")
        await self.page.goto(job.url, wait_until="domcontentloaded")
        await async_pause(1, 2)
        await self.job_apply(job)

    async def job_apply(self, job: Job) -> Tuple[str, str]:
        """
        Attempt to apply to an Indeed job.
        Returns (result, cover_letter_text) where result is 'success' | 'skipped' | 'error'.
        """
        cover_letter = ""
        try:
            apply_btn = await self._find_apply_button(job)
            if not apply_btn:
                logger.warning(f"No apply button found for: {job.job_title} at {job.company_name}")
                return "skipped", cover_letter

            await apply_btn.click()
            await async_pause(1, 2)

            if self.test_mode:
                logger.info("TEST_MODE: skipping form submission")
                await self._discard_application()
                return "skipped", cover_letter

            cover_letter = await self._fill_application_form(job)
            result = await self._submit_application()
            return ("success" if result else "error"), cover_letter

        except Exception as e:
            logger.error(f"Error applying to Indeed job {job.job_title}: {e}", exc_info=True)
            await debug_capture(self.page, "indeed_job_apply_error")
            try:
                await self._discard_application()
            except Exception:
                pass
            return "error", cover_letter

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _find_apply_button(self, job: Job) -> Any:
        """Locate the Indeed apply button on the job detail page"""
        await self.page.goto(job.url, wait_until="domcontentloaded")
        await async_pause(1, 2)

        for selector in INDEED_APPLY_BUTTON_SELECTOR.split(", "):
            btn = await find_element_safely(self.page, selector.strip(), timeout=5000)
            if btn:
                logger.info(f"Found apply button: {selector.strip()}")
                return btn

        logger.warning("No apply button found on job page")
        return None

    async def _fill_application_form(self, job: Job) -> str:
        """Step through multi-page Indeed application form and fill fields"""
        cover_letter = ""
        max_steps = 10

        for step in range(max_steps):
            logger.info(f"Application form step {step + 1}")

            if self.pause_checker:
                await self.pause_checker()

            # Check if we reached the final submit page
            submit_btn = await find_element_safely(
                self.page, INDEED_SUBMIT_BUTTON_SELECTOR, timeout=3000
            )
            if submit_btn:
                logger.info("Reached submit page")
                break

            # Fill visible form sections
            await self._fill_up(job)

            # Click next
            next_btn = await find_element_safely(
                self.page, INDEED_NEXT_BUTTON_SELECTOR, timeout=5000
            )
            if not next_btn:
                logger.warning("No next/submit button found")
                break

            await next_btn.click()
            await async_pause(1, 2)

        return cover_letter

    async def _fill_up(self, job: Job) -> None:
        """Fill visible form fields on the current step"""
        try:
            sections = await find_elements_safely(
                self.page,
                "div.ia-Questions-item, div[data-testid='ia-Questions-item']",
            )
            for section in sections or []:
                await self._process_form_section(section, job)
        except Exception as e:
            logger.error(f"Error filling form step: {e}", exc_info=True)
            await debug_capture(self.page, "indeed_fill_form_error")

    async def _process_form_section(self, section: Any, job: Job) -> None:
        """Fill a single form section based on its detected type"""
        try:
            # Text input / textarea
            text_input = await find_element_safely(
                section, "input[type='text'], input[type='number'], textarea", timeout=2000
            )
            if text_input:
                question_label = await get_clean_text(section)
                if question_label:
                    answer = await self._get_llm_answer(question_label, job)
                    await text_input.fill(answer)
                    logger.debug(f"Filled text field '{question_label}' with '{answer}'")
                return

            # Radio / checkbox
            radio = await find_element_safely(section, "input[type='radio']", timeout=2000)
            if radio:
                await self._handle_radio_section(section, job)
                return

            # Select / dropdown
            select = await find_element_safely(section, "select", timeout=2000)
            if select:
                await self._handle_dropdown_section(section, select, job)
                return

        except Exception as e:
            logger.warning(f"Error processing form section: {e}")
            await debug_capture(self.page, "indeed_form_section_error")

    async def _handle_radio_section(self, section: Any, job: Job) -> None:
        """Select appropriate radio option using LLM"""
        try:
            question_text = await get_clean_text(section)
            radios = await find_elements_safely(section, "input[type='radio']")
            if not radios:
                return
            labels = []
            for radio in radios:
                label_id = await radio.get_attribute("id")
                if label_id:
                    label_el = await find_element_safely(
                        section, f"label[for='{label_id}']", timeout=1000
                    )
                    labels.append(await get_clean_text(label_el) if label_el else "")
                else:
                    labels.append("")
            options_str = ", ".join(labels)
            answer = await self._get_llm_answer(f"{question_text}. Options: {options_str}", job)
            for radio, label in zip(radios, labels):
                if answer.lower() in label.lower():
                    await radio.click()
                    logger.debug(f"Selected radio '{label}'")
                    return
            # Fallback: click first option
            await radios[0].click()
        except Exception as e:
            logger.warning(f"Error handling radio section: {e}")
            await debug_capture(self.page, "indeed_radio_error")

    async def _handle_dropdown_section(self, section: Any, select: Any, job: Job) -> None:
        """Select appropriate dropdown option using LLM"""
        try:
            question_text = await get_clean_text(section)
            options = await select.query_selector_all("option")
            option_texts = [await get_clean_text(o) for o in options]
            options_str = ", ".join(option_texts)
            answer = await self._get_llm_answer(f"{question_text}. Options: {options_str}", job)
            for opt_text in option_texts:
                if answer.lower() in opt_text.lower():
                    await select.select_option(label=opt_text)
                    logger.debug(f"Selected dropdown option '{opt_text}'")
                    return
            # Fallback: skip default/empty option and pick the first real one
            if len(option_texts) > 1:
                await select.select_option(label=option_texts[1])
        except Exception as e:
            logger.warning(f"Error handling dropdown section: {e}")
            await debug_capture(self.page, "indeed_dropdown_error")

    async def _get_llm_answer(self, question: str, job: Job) -> str:
        """Get LLM answer for a form question"""
        try:
            if self.gpt_answerer:
                return await self.gpt_answerer.answer_question_textual_wide_range(question)
        except Exception as e:
            logger.warning(f"LLM answer failed for question '{question}': {e}")
        return ""

    async def _submit_application(self) -> bool:
        """Click the final submit button"""
        try:
            submit_btn = await find_element_safely(
                self.page, INDEED_SUBMIT_BUTTON_SELECTOR, timeout=5000
            )
            if not submit_btn:
                logger.error("Submit button not found")
                return False
            await submit_btn.click()
            await async_pause(2, 4)
            logger.info("Application submitted on Indeed")
            return True
        except Exception as e:
            logger.error(f"Error submitting Indeed application: {e}", exc_info=True)
            await debug_capture(self.page, "indeed_submit_error")
            return False

    async def _discard_application(self) -> None:
        """Close/discard the current Indeed application modal"""
        try:
            close_selectors = [
                "button[data-testid='ExitLinkWithModalComponent-exitButton']",
                "button[aria-label='Close']",
                "button.ia-CloseButton",
                "button[data-testid='ia-closeButton']",
            ]
            for selector in close_selectors:
                btn = await find_element_safely(self.page, selector, timeout=3000)
                if btn:
                    await btn.click()
                    logger.info("Indeed application discarded")
                    return
        except Exception as e:
            logger.warning(f"Could not discard Indeed application: {e}")
            await debug_capture(self.page, "indeed_discard_error")

    def _load_questions(self) -> List[Question]:
        """Load previously answered questions from YAML cache"""
        try:
            if self.answers_file.exists():
                data = load_yaml_file(str(self.answers_file))
                if isinstance(data, list):
                    return [Question(**q) for q in data if q]
        except Exception as e:
            logger.warning(f"Could not load answers file: {e}")
        return []

    def _save_questions(self, question_data: Question) -> None:
        """Persist a new answered question to the YAML cache"""
        try:
            self.questions.append(question_data)
            save_yaml_file(
                str(self.answers_file),
                [q.model_dump() for q in self.questions],
            )
        except Exception as e:
            logger.warning(f"Could not save question: {e}")
