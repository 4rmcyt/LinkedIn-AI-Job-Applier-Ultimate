import os
import re
import traceback
from pathlib import Path
from typing import Any, List, Tuple

from playwright.sync_api import Page

from config.logger_config import logger
from src.job_manager.easy_applier import BaseEasyApplier, NoInfoException
from src.job_manager.resume_anonymizer import ResumeAnonymizer
from src.llm.llm_manager import GPTAnswerer
from src.pydantic_models.job_models import Job, Question
from src.utils.browser_utils import (
    debug_capture,
    find_element_safely,
    find_elements_safely,
    get_clean_text,
)
from src.utils.utils import async_pause, get_first_pdf_file, load_yaml_file, sanitize_text

INDEED_APPLY_BUTTON_SELECTOR = "button#indeedApplyButton, button[data-jk], .ia-IndeedApplyButton"
INDEED_APPLY_MODAL_SELECTOR = "div.ia-BasePage, div[data-testid='ia-container']"
INDEED_NEXT_BUTTON_SELECTOR = "button[data-testid='continue-button'], button[data-testid^='hp-continue-button'], button[data-testid='ia-continueButton'], .ia-BasePage-component button:has-text('Continue'), button:has-text('Review your application'), button:has-text('Continue')"
INDEED_SUBMIT_BUTTON_SELECTOR = "button[data-testid='ia-submitButton'], button.ia-submitButton, button[data-testid='submit-application-button']"


class IndeedEasyApplier(BaseEasyApplier):
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
        self.all_questions: List[Question] = self._load_questions()
        self.previous_question_texts: List[str] = []
        self.generated_resume_dir = Path(resume_dir) / "generated_resumes"
        self.ready_made_resume_path = get_first_pdf_file(Path(resume_dir))

        logger.info("IndeedEasyApplier initialized")

    def set_page(self, page: Page) -> None:
        self.page = page

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def apply_to_job(self, job: Job) -> Tuple[str, str]:
        """Entry point - navigate to job page and apply"""
        logger.info(f"Navigating to Indeed job: {job.url}")
        await self.page.goto(job.url, wait_until="domcontentloaded")
        await async_pause(1, 2)
        result, cover_letter = await self.job_easy_apply(job)
        return result, cover_letter

    async def job_easy_apply(self, job: Job) -> Tuple[str, str]:
        """
        Attempt to apply to an Indeed job.
        Returns (result, cover_letter_text) where result is 'success' | 'skipped' | 'error'.
        """
        cover_letter = ""
        self.current_job = job
        try:
            apply_btn = await self._find_apply_button(job)
            if not apply_btn:
                logger.warning(f"No apply button found for: {job.job_title} at {job.company_name}")
                return "skipped", cover_letter

            try:
                async with self.page.context.expect_page(timeout=5000) as new_page_info:
                    await apply_btn.click()
                new_page = await new_page_info.value
                await new_page.wait_for_load_state("domcontentloaded")
                self.page = new_page
                logger.info("Application form opened in new tab, switched to it")
            except Exception:
                # No new tab — form is a modal on the current page
                logger.debug("No new tab opened, form is modal on current page")
                await async_pause(1, 2)

            cover_letter = await self._fill_application_form(job)
            if self.test_mode:
                logger.info("TEST_MODE: skipping form submission")
                await self._discard_application()
                return "success", cover_letter

            result = await self._submit_application()
            return ("success" if result else "error"), cover_letter

        except NoInfoException as e:
            logger.warning(f"Could not apply to {job.job_title} at {job.company_name}. Reason: {e}")
            return (
                "Skip",
                f"Could not apply to {job.job_title} at {job.company_name}. Reason: {e}",
            )
        except Exception as e:
            logger.error(f"Error applying to Indeed job {job.job_title}: {e}", exc_info=True)
            await debug_capture(self.page, "indeed_job_easy_apply_error")
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
        # await self.page.goto(job.url, wait_until="domcontentloaded")
        # await async_pause(1, 2)

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
            self.previous_question_texts = []

            if self.pause_checker:
                await self.pause_checker()

            # Pause for captcha if present before trying to advance
            captcha = await find_element_safely(self.page, "[data-testid='captcha']", timeout=1000)
            if captcha:
                logger.warning("Captcha detected on form step — pausing for manual solve")
                while True:
                    if not await find_element_safely(
                        self.page, "[data-testid='captcha']", timeout=1000
                    ):
                        break
                    response = await self.page.locator("#g-recaptcha-response").input_value()
                    if response:
                        break
                    await async_pause(3, 3)
                logger.info("Captcha resolved, continuing")

            # Check if we reached the final submit page
            submit_btn = await find_element_safely(
                self.page, INDEED_SUBMIT_BUTTON_SELECTOR, timeout=15000
            )
            if submit_btn:
                logger.info("Reached submit page")
                break

            # Fill visible form sections
            await self._fill_up(job)

            # Click next — find first *visible* button across the ordered selectors
            next_btn = await self._find_visible_next_button()
            if not next_btn:
                logger.warning("No next/submit button found")
                await debug_capture(self.page, "indeed_no_next_button")
                break

            await next_btn.click()
            try:
                await self.page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                await async_pause(2, 3)

        return cover_letter

    async def _find_visible_next_button(self) -> Any:
        """Return the first *visible* next/continue button, trying each selector in order."""
        for selector in INDEED_NEXT_BUTTON_SELECTOR.split(", "):
            selector = selector.strip()
            try:
                locator = self.page.locator(selector)
                count = await locator.count()
                for i in range(count):
                    btn = locator.nth(i)
                    if await btn.is_visible():
                        return btn
            except Exception:
                continue
        return None

    async def _fill_up(self, job: Job) -> None:
        """Fill visible form fields on the current step"""
        try:
            # Handle special-case pages first
            if await find_element_safely(
                self.page, "[data-testid='resume-selection-form']", timeout=1000
            ):
                await self.page.wait_for_load_state("domcontentloaded")
                await self._handle_resume_selection(job)
                return

            if await find_element_safely(
                self.page, "[data-testid='profile-location-page']", timeout=1000
            ):
                await self._fill_profile_location_page()
                return

            sections = await find_elements_safely(
                self.page,
                "div.ia-Questions-item, div[data-testid='ia-Questions-item']",
            )
            for section in sections or []:
                try:
                    await self._process_form_section(section)
                except NoInfoException as e:
                    logger.warning(f"{e}")
                    continue
        except Exception as e:
            logger.error(f"Error filling form step: {e}", exc_info=True)
            await debug_capture(self.page, "indeed_fill_form_error")

    async def _handle_resume_selection(self, job: Job) -> None:
        """Prefer Indeed Resume if available; otherwise upload ready-made or generated resume"""
        try:
            indeed_resume_radio = await find_element_safely(
                self.page,
                "input[data-testid='resume-selection-structured-resume-radio-card-input']",
                timeout=3000,
            )
            if indeed_resume_radio:
                if not await indeed_resume_radio.is_checked():
                    await indeed_resume_radio.click()
                    await async_pause(0.5, 1)
                logger.info("Using Indeed Resume")
                return

            upload_radio_input = await find_element_safely(
                self.page,
                "input[data-testid='resume-selection-file-resume-upload-radio-card-input']",
                timeout=3000,
            )
            if upload_radio_input and not await upload_radio_input.is_checked():
                # The radio input is visually hidden; click the visible label instead
                upload_label = await find_element_safely(
                    self.page,
                    "label[data-testid='resume-selection-file-resume-upload-radio-card-label']",
                    timeout=3000,
                )
                if upload_label:
                    await upload_label.click()
                    await async_pause(0.5, 1)
                    logger.info("Selected 'Upload a resume' radio option")

            file_input = await find_element_safely(
                self.page,
                "input[data-testid='resume-selection-file-resume-upload-radio-card-file-input']",
                timeout=3000,
            )
            if not file_input:
                logger.warning("Resume file input not found on resume selection page")
                return

            if (
                self.ready_made_resume_path is not None
                and self.ready_made_resume_path.resolve().is_file()
            ):
                abs_path = os.path.abspath(str(self.ready_made_resume_path.resolve()))
                await file_input.set_input_files(abs_path)
                logger.info(f"Uploaded ready-made resume: {abs_path}")
            else:
                await self._create_and_upload_resume(file_input, job)
        except Exception as e:
            logger.error(f"Error handling resume selection page: {e}", exc_info=True)
            await debug_capture(self.page, "indeed_resume_selection_error")

    async def _fill_profile_location_page(self) -> None:
        """Fill the 'Review your location details' profile page using resume data"""
        try:
            personal = {}
            if self.gpt_answerer and hasattr(self.gpt_answerer, "resume_structured"):
                personal = self.gpt_answerer.resume_structured.get("personal_information", {})

            postal_code = str(personal.get("zip_code", "") or "")
            city = str(personal.get("city", "") or "")
            address = str(personal.get("address", "") or "")

            if postal_code:
                field = await find_element_safely(
                    self.page,
                    "input[data-testid='location-fields-postal-code-input']",
                    timeout=3000,
                )
                if field:
                    await field.fill(postal_code)
                    logger.debug(f"Filled postal code: {postal_code}")

            if city:
                field = await find_element_safely(
                    self.page, "input[data-testid='location-fields-locality-input']", timeout=3000
                )
                if field:
                    await field.fill(city)
                    logger.debug(f"Filled city: {city}")

            if address:
                field = await find_element_safely(
                    self.page, "input[data-testid='location-fields-address-input']", timeout=3000
                )
                if field:
                    await field.fill(address)
                    logger.debug(f"Filled street address: {address}")

        except Exception as e:
            logger.error(f"Error filling profile location page: {e}", exc_info=True)
            await debug_capture(self.page, "indeed_profile_location_error")

    async def _handle_terms_of_service(self, section: Any) -> bool:
        return False

    async def _find_and_handle_textbox_question(self, section: Any) -> bool:
        """Fill appropriate textbox using cache or LLM"""
        text_input = await find_element_safely(
            section, "input[type='text'], input[type='number'], textarea", timeout=2000
        )
        question_text = await get_clean_text(section)
        if not text_input or not question_text:
            return False

        self.previous_question_texts.append(question_text)
        current_question_sanitized = sanitize_text(question_text)
        existing_answer = None
        for item in self.all_questions:
            if item.question == current_question_sanitized and item.question_type == "text":
                existing_answer = item.answer
                break
        if existing_answer:
            answer = existing_answer
            logger.debug(f"Using cached answer for '{question_text}': '{answer}'")
        else:
            answer = self.gpt_answerer.answer_question_textual_wide_range(
                question_text, self.previous_question_texts[:-1]
            )
            if answer.lower().startswith("no info"):
                raise NoInfoException(f"No info found for question: {question_text}")
            self._save_questions(
                Question(question_type="text", question=question_text, answer=answer)
            )
        await text_input.fill(answer)
        logger.debug(f"Filled text field '{question_text}' with '{answer}'")
        return True

    async def _find_and_handle_checkbox_question(self, section: Any) -> bool:
        """Select appropriate checkboxes (multi-select) using LLM"""
        checkbox = await find_element_safely(section, "input[type='checkbox']", timeout=2000)
        if not checkbox:
            return False

        try:
            question_text = await get_clean_text(section)
            checkboxes = await find_elements_safely(section, "input[type='checkbox']")
            if not checkboxes:
                return

            checkbox_data = []
            option_texts = []
            for cb in checkboxes:
                cb_id = await cb.get_attribute("id")
                label_text = ""
                if cb_id:
                    label_el = await find_element_safely(
                        section, f"label[for='{cb_id}']", timeout=1000
                    )
                    if label_el:
                        label_text = await get_clean_text(label_el)
                if label_text:
                    checkbox_data.append((cb, label_text))
                    option_texts.append(label_text)

            if not option_texts:
                return False

            # Remove options from question text
            for option in sorted(option_texts, key=lambda x: len(x), reverse=True):
                question_text = question_text[::-1].replace(option[::-1], "", 1)[::-1]
            question_text = re.sub(
                r"Clear your answer(s)?", "", question_text, flags=re.IGNORECASE
            ).strip()

            if question_text:
                self.previous_question_texts.append(question_text)

            current_question_sanitized = sanitize_text(question_text)
            existing_answer = None
            for item in self.all_questions:
                if item.question == current_question_sanitized and item.question_type == "checkbox":
                    existing_answer = item.answer
                    break

            if existing_answer:
                selected_options = (
                    existing_answer if isinstance(existing_answer, list) else [existing_answer]
                )
                logger.debug(f"Using cached checkboxes for '{question_text}': {selected_options}")
            else:
                selected_options = self.gpt_answerer.select_many_answers_from_options(
                    question_text, option_texts, self.previous_question_texts[:-1]
                )
                self._save_questions(
                    Question(
                        question_type="checkbox",
                        question=question_text,
                        answer=selected_options,
                    )
                )
            logger.debug(f"Selected checkboxes: {selected_options}")

            for cb, label_text in checkbox_data:
                if any(
                    sel.lower() in label_text.lower() or label_text.lower() in sel.lower()
                    for sel in selected_options
                    if not sel.lower().startswith("no info")
                ):
                    if not await cb.is_checked():
                        cb_id = await cb.get_attribute("id")
                        if cb_id:
                            label_el = await find_element_safely(
                                section, f"label[for='{cb_id}']", timeout=1000
                            )
                            if label_el:
                                await label_el.click()
                                logger.debug(f"Checked checkbox via label: '{label_text}'")
                                continue
                        await cb.click()
                        logger.debug(f"Checked checkbox: '{label_text}'")
        except Exception as e:
            logger.warning(f"Error handling checkbox section: {e}")
            await debug_capture(self.page, "indeed_checkbox_error")
            return False
        return True

    async def _find_and_handle_radio_question(self, section: Any) -> bool:
        """Select appropriate radio option using LLM"""
        radio = await find_element_safely(section, "input[type='radio']", timeout=2000)
        if not radio:
            return False

        try:
            question_text = await get_clean_text(section)
            radios = await find_elements_safely(section, "input[type='radio']")
            if not radios:
                return False
            option_texts = []
            for radio in radios:
                label_id = await radio.get_attribute("id")
                if label_id:
                    label_el = await find_element_safely(
                        section, f"label[for='{label_id}']", timeout=1000
                    )
                    option_texts.append(await get_clean_text(label_el) if label_el else "")
                else:
                    option_texts.append("")

            # Remove options from question text
            for option in sorted(option_texts, key=lambda x: len(x), reverse=True):
                question_text = question_text[::-1].replace(option[::-1], "", 1)[::-1]
            question_text = re.sub(
                r"Clear your answer(s)?", "", question_text, flags=re.IGNORECASE
            ).strip()

            if question_text:
                self.previous_question_texts.append(question_text)

            current_question_sanitized = sanitize_text(question_text)
            existing_answer = None
            for item in self.all_questions:
                if current_question_sanitized in item.question and item.question_type == "radio":
                    existing_answer = item.answer
                    break

            if existing_answer:
                answer = existing_answer
                logger.debug(f"Using cached radio answer for '{question_text}': '{answer}'")
            else:
                answer = self.gpt_answerer.select_one_answer_from_options(
                    question_text, option_texts, self.previous_question_texts[:-1]
                )
                if answer.lower().startswith("no info"):
                    raise NoInfoException(f"No info found for question: {question_text}")
                self._save_questions(
                    Question(question_type="radio", question=question_text, answer=answer)
                )
            # Exact match first to avoid substring false positives (e.g. "male" in "female")
            for radio, option in zip(radios, option_texts):
                if answer.lower() == option.lower():
                    await radio.click()
                    logger.debug(f"Selected radio '{option}'")
                    return
            for radio, option in zip(radios, option_texts):
                if answer.lower() in option.lower():
                    await radio.click()
                    logger.debug(f"Selected radio '{option}'")
                    return
            # Fallback: click first option
            await radios[0].click()
        except Exception as e:
            logger.warning(f"Error handling radio section: {e}")
            await debug_capture(self.page, "indeed_radio_error")
            return False
        return True

    async def _find_and_handle_dropdown_question(self, section: Any) -> bool:
        """Select appropriate dropdown option using LLM"""
        dropdown = await find_element_safely(section, "select", timeout=2000)
        if not dropdown:
            return False

        try:
            question_text = await get_clean_text(section)
            if not question_text:
                return False

            options = await dropdown.query_selector_all("option")
            option_texts = [await get_clean_text(o) for o in options]

            self.previous_question_texts.append(question_text)

            current_question_sanitized = sanitize_text(question_text)
            existing_answer = None
            for item in self.all_questions:
                if current_question_sanitized in item.question and item.question_type == "dropdown":
                    existing_answer = item.answer
                    break

            if existing_answer:
                answer = existing_answer
                logger.debug(f"Using cached dropdown answer for '{question_text}': '{answer}'")
            else:
                answer = self.gpt_answerer.select_one_answer_from_options(
                    question_text, option_texts, self.previous_question_texts[:-1]
                )
                if not answer.lower().startswith("no info"):
                    self._save_questions(
                        Question(question_type="dropdown", question=question_text, answer=answer)
                    )
            # Exact match first to avoid substring false positives
            for opt_text in option_texts:
                if answer.lower() == opt_text.lower():
                    await dropdown.select_option(label=opt_text)
                    logger.debug(f"Selected dropdown option '{opt_text}'")
                    return
            for opt_text in option_texts:
                if answer.lower() in opt_text.lower():
                    await dropdown.select_option(label=opt_text)
                    logger.debug(f"Selected dropdown option '{opt_text}'")
                    return
            # Fallback: skip default/empty option and pick the first real one
            if len(option_texts) > 1:
                await dropdown.select_option(label=option_texts[1])
        except Exception as e:
            logger.warning(f"Error handling dropdown section: {e}")
            await debug_capture(self.page, "indeed_dropdown_error")
            return False
        return True

    async def _submit_application(self) -> bool:
        """Click the final submit button"""
        try:
            submit_btn = await find_element_safely(
                self.page, INDEED_SUBMIT_BUTTON_SELECTOR, timeout=5000
            )
            if not submit_btn:
                logger.error("Submit button not found")
                await debug_capture(self.page, "indeed_submit_button_not_found")
                return False

            captcha = await find_element_safely(self.page, "[data-testid='captcha']", timeout=2000)
            if captcha:
                logger.warning("Captcha detected before submission — pausing for manual solve")
                while True:
                    if not await find_element_safely(
                        self.page, "[data-testid='captcha']", timeout=1000
                    ):
                        break
                    response = await self.page.locator("#g-recaptcha-response").input_value()
                    if response:
                        break
                    await async_pause(3, 3)

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
                    await async_pause(1, 2)
                    # Handle "Save application progress" dialog if it appears
                    dont_save = await find_element_safely(
                        self.page, "button:has-text('Don\\'t save')", timeout=3000
                    )
                    if dont_save:
                        await dont_save.click()
                        logger.info("Dismissed save dialog with 'Don't save'")
                    logger.info("Indeed application discarded")
                    return
        except Exception as e:
            logger.warning(f"Could not discard Indeed application: {e}")
            await debug_capture(self.page, "indeed_discard_error")

    async def _fill_textbox_question_errors(self) -> None:
        # TODO: implement this method
        pass


if __name__ == "__main__":
    """Simple test for IndeedEasyApplier functionality"""
    import asyncio
    from pathlib import Path

    import dotenv

    from config.constants import ANSWERS_FILE, COVER_LETTER_DIR, RESUME_DIR
    from src.job_manager.resume_anonymizer import ResumeAnonymizer
    from src.llm.llm_manager import GPTAnswerer
    from src.pydantic_models.job_models import Job
    from src.pydantic_models.prompt_models import ResumeStructure
    from src.resume_builder.resume_generator import ResumeGenerator
    from src.resume_builder.resume_manager import ResumeManager
    from src.resume_builder.style_manager import StyleManager
    from src.utils.browser_utils import create_playwright_browser, save_browser_session

    RESUME_STRUCTURED_FILE = Path(RESUME_DIR) / "structured_resume.yaml"
    RESUME_TEXT_FILE = Path(RESUME_DIR) / "resume_text.txt"
    paused = False

    async def check_pause():
        """Check if execution is paused and wait if needed"""
        global paused
        if paused:
            while paused:
                await asyncio.sleep(0.5)

    async def test_indeed_easy_applier():
        """Test IndeedEasyApplier with a real Indeed job posting (async)"""
        logger.info("Starting IndeedEasyApplier test...")

        # Test job URL
        # job_url = (
        #     "https://www.indeed.com/viewjob?jk=55f3b1bf0b69babb&tk=1jlgv4jjvi96p881&from=serp&vjs=3"
        # )
        job_url = (
            "https://www.indeed.com/viewjob?jk=5d8d545b93be6f7f&tk=1jlgv2qrp21cc009&from=serp&vjs=3"
        )
        # job_url = (
        #     "https://www.indeed.com/viewjob?jk=f50b368946d1affe&tk=1jlgv2qrp21cc009&from=serp&vjs=3"
        # )
        # job_url = "https://www.indeed.com/viewjob?jk=db5d6bbd822a8a89&from=serp&vjs=3"
        # job_url = (
        #     "https://www.indeed.com/viewjob?jk=0cc1bcc48e791a51&tk=1jlgv2qrp21cc009&from=serp&vjs=3"
        # )

        # Initialize Playwright browser
        try:
            browser, context, page = await create_playwright_browser()
            logger.info("Playwright browser initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Playwright browser: {e}")
            return False

        # Create test job object
        test_job = Job(
            job_title="Junior Software Developer",
            company_name="Example Corp",
            location="Remote",
            url=job_url,
            job_description="Entry-level software developer role working on web applications.",
            apply_method="Easy Apply",
        )

        try:
            # Load secrets for LLM
            secrets = dotenv.dotenv_values(".env")
            llm_api_key = secrets.get("llm_api_key", "")
            llm_proxy = secrets.get("llm_proxy", "")

            # Initialize GPT answerer
            gpt_answerer = GPTAnswerer(llm_api_key, llm_proxy)
            resume_structured = load_yaml_file(RESUME_STRUCTURED_FILE)
            resume_structured = ResumeStructure(**resume_structured).model_dump()
            with open(RESUME_TEXT_FILE, "r") as f:
                resume_text = f.read()

            # Set resume anonymizer and anonymize the resume information
            resume_anonymizer = ResumeAnonymizer(resume_structured)
            resume_anonymizer.anonymize_personal_information()
            resume_structured = resume_anonymizer.resume_anonymized
            resume_text = resume_anonymizer.anonymize_text(resume_text)

            gpt_answerer.set_resume(resume_structured, resume_text)
            gpt_answerer.set_job(test_job, is_test=True)

            # Initialize resume generator manager
            style_manager = StyleManager()
            resume_generator = ResumeGenerator(gpt_answerer, resume_anonymizer)
            resume_generator_manager = ResumeManager(llm_api_key, style_manager, resume_generator)

            # Initialize IndeedEasyApplier
            easy_applier = IndeedEasyApplier(
                page,
                gpt_answerer,
                resume_anonymizer,
                resume_generator_manager,
                check_pause,
                ANSWERS_FILE,
                RESUME_DIR,
                COVER_LETTER_DIR,
                test_mode=True,
            )
            if not easy_applier.ready_made_resume_path.is_file():
                resume_generator_manager.choose_style()

            # Navigate to job page
            # logger.info(f"Navigating to job page: {job_url}")
            # await page.goto(job_url)
            # await async_pause(3, 5)

            # Test the apply_to_job method
            logger.info("Testing IndeedEasyApplier.apply_to_job method...")
            result = await easy_applier.apply_to_job(test_job)

            if result[0] == "success":
                logger.info("✅ IndeedEasyApplier test completed successfully!")
                return True
            else:
                logger.error(f"❌ IndeedEasyApplier test failed - result: {result[0]}")
                return False

        except Exception as e:
            logger.error(f"❌ IndeedEasyApplier test failed with error: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False
        finally:
            # Keep browser open for manual inspection
            logger.info(
                "Test completed. Browser will remain open for 5 minutes for manual inspection..."
            )
            await async_pause(300, 300)
            try:
                await save_browser_session(context)
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass

    print("\nTesting full IndeedEasyApplier functionality...")
    success = asyncio.run(test_indeed_easy_applier())
    if success:
        print("✅ IndeedEasyApplier test passed!")
    else:
        print("❌ IndeedEasyApplier test failed!")
