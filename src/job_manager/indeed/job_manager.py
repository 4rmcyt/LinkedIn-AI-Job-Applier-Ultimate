from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml
from playwright.sync_api import Page

from config.app_config import MAX_APPLIES_NUM, TEST_MODE
from config.constants import (
    ANSWERS_FILE,
    COVER_LETTER_DIR,
    OUTPUT_DIR,
    RESUME_DIR,
    SEARCH_CONFIG_FILE,
)
from config.logger_config import logger
from src.job_manager.indeed.easy_applier import IndeedEasyApplier
from src.pydantic_models.job_models import Job, JobInfo, JobManagerCache
from src.telegram.telegram_manager import TelegramReportSender
from src.utils.browser_utils import (
    debug_capture,
    find_element_safely,
    find_elements_safely,
    safe_click,
)
from src.utils.utils import load_yaml_file, pause, sanitize_text

search_config = load_yaml_file(SEARCH_CONFIG_FILE)
logger.info(f"Maximum allowed number of applications: {MAX_APPLIES_NUM}")

LAST_RUN_FILE = Path(OUTPUT_DIR) / "last_run.yaml"

INDEED_JOB_CARD_SELECTOR = "div.job_seen_beacon, div[data-testid='jobcard-wrapper']"
INDEED_JOB_TITLE_SELECTOR = "h2.jobTitle a, [data-testid='jobTitle'] a"
INDEED_COMPANY_SELECTOR = "[data-testid='company-name'], .companyName"
INDEED_LOCATION_SELECTOR = "[data-testid='text-location'], .companyLocation"
INDEED_EASY_APPLY_BADGE = "span.iaLabel, [data-testid='ia-badge']"
INDEED_NEXT_PAGE_SELECTOR = (
    "a[data-testid='pagination-page-next'], nav[role='navigation'] a[aria-label='Next Page']"
)


class IndeedJobApplier:
    """Class for searching and sending applications to employers on Indeed"""

    def __init__(
        self, page: Page, linkedin_email: str, resume_anonymizer: Any, search_component: Any
    ):
        logger.info("Initializing IndeedJobApplier")
        self.page = page
        self.email = linkedin_email  # reuses the same parameter name for interface compatibility
        self.resume_anonymizer = resume_anonymizer
        self.search_component = search_component
        self.llm_answerer_component = None
        self.llm_agent_component = None
        self.resume_generator_manager = None
        self.pause_checker = None
        self.jobs_no_info: List[Any] = []
        self.job_key_skills: List[str] = []
        self.interesting_jobs: List[Any] = []
        self.page_num = 0
        self.error_num = 0
        self.total_applies_num = 0
        self.applies_num = 0
        self.success_applies_num = 0
        self.resume_recommendations = ""

        logger.info("IndeedJobApplier successfully initialized")

    # ------------------------------------------------------------------
    # Interface methods (same signatures as LinkedIn JobApplier)
    # ------------------------------------------------------------------

    def set_parameters(self, parameters: Dict[str, Any]):
        """Setting IndeedJobApplier parameters"""
        logger.info("Setting IndeedJobApplier parameters")
        self.max_applies_num = MAX_APPLIES_NUM
        self.apply_once_at_company = parameters.get("apply_once_at_company", True)
        self.job_blacklist = [sanitize_text(j) for j in parameters.get("job_blacklist", [])]
        self.success_companies = self._load_companies_from_yaml("success.yaml")
        self.skipped_companies = self._load_companies_from_yaml("skipped.yaml")
        self.failed_companies = self._load_companies_from_yaml("failed.yaml")
        self.seen_answers = self._load_data_from_yaml("answers.yaml")
        self.skill_stat = self._load_data_from_yaml("skill_stat.yaml")
        self.interesting_jobs = self._load_data_from_yaml("interesting_jobs.yaml")
        self.interesting_jobs = [JobInfo(**job) for job in self.interesting_jobs]
        self.cache = self._load_cache()
        self.applies_num = 0
        self.previous_apply_number = self._check_the_previous_apply_number()
        self.success_applies_num = self.previous_apply_number
        self.total_applies_num = self.cache.total_applies_num
        logger.info("Parameters successfully set")

    def set_answerer_and_agent(self, llm_answerer_component: Any, llm_agent_component: Any):
        self.llm_answerer_component = llm_answerer_component
        self.llm_agent_component = llm_agent_component

    def set_resume(self, resume: Dict[str, Any]) -> None:
        self.resume = resume

    def set_resume_generator_manager(self, resume_generator_manager: Any):
        self.resume_generator_manager = resume_generator_manager

    def set_pause_checker(self, pause_checker):
        self.pause_checker = pause_checker

    # ------------------------------------------------------------------
    # Main application loop
    # ------------------------------------------------------------------

    async def start_applying(self) -> None:
        """Main loop: iterate over all search URLs and apply to jobs"""
        logger.info("IndeedJobApplier starting application process")

        search_urls = self.search_component.get_search_urls()
        logger.info(f"Indeed search URLs: {search_urls}")

        for url in search_urls:
            if self.applies_num >= self.max_applies_num:
                logger.info("Reached maximum number of applications")
                break

            self.page_num = 0
            await self.page.goto(url, wait_until="domcontentloaded")
            pause(1, 2)

            while True:
                if self.applies_num >= self.max_applies_num:
                    break
                if self.pause_checker:
                    await self.pause_checker()

                vacancies = await self.get_vacancies_from_page()
                logger.info(f"Found {len(vacancies)} job cards on page {self.page_num + 1}")

                for vacancy in vacancies:
                    if self.applies_num >= self.max_applies_num:
                        break
                    try:
                        await self.apply_job(vacancy)
                    except Exception as e:
                        logger.error(f"Unexpected error processing vacancy: {e}", exc_info=True)
                        self.error_num += 1

                if not await self._go_to_next_page():
                    break
                self.page_num += 1

        await self.send_report("finished")
        logger.info(
            f"Indeed application process finished. "
            f"Applied: {self.applies_num}, Errors: {self.error_num}"
        )

    async def get_vacancies_from_page(self) -> List[Any]:
        """Return all job card elements on the current page"""
        try:
            await self.page.wait_for_selector(INDEED_JOB_CARD_SELECTOR, timeout=15000)
            cards = await find_elements_safely(self.page, INDEED_JOB_CARD_SELECTOR)
            return cards or []
        except Exception as e:
            logger.warning(f"Could not find job cards: {e}")
            await debug_capture(self.page, "vacancies_not_found")
            return []

    async def apply_job(self, vacancy: Any) -> str:
        """Process a single Indeed job card"""
        try:
            job = await self._extract_job_from_card(vacancy)
            if job is None:
                return "skipped"

            already_seen, reason = self._job_is_already_seen(job)
            if already_seen:
                logger.info(
                    f"Skipping already seen job: {job.job_title} at {job.company_name} ({reason})"
                )
                return "skipped"

            if self.search_component.is_job_blacklisted(
                job.job_title, job.company_name, job.location
            ):
                logger.info(f"Skipping blacklisted job: {job.job_title} at {job.company_name}")
                return "skipped"

            result, cover_letter = await self.easy_apply(job)
            await self._handle_apply_result(result, job, cover_letter)
            return result

        except Exception as e:
            logger.error(f"Error in apply_job: {e}", exc_info=True)
            await debug_capture(self.page, "apply_job_error")
            self.error_num += 1
            return "error"

    async def easy_apply(self, job: Job) -> Tuple[str, str]:
        """Delegate application to IndeedEasyApplier"""
        easy_applier = IndeedEasyApplier(
            page=self.page,
            gpt_answerer=self.llm_answerer_component,
            resume_anonymizer=self.resume_anonymizer,
            resume_generator_manager=self.resume_generator_manager,
            pause_checker=self.pause_checker,
            answers_file=Path(ANSWERS_FILE),
            resume_dir=Path(RESUME_DIR),
            cover_letter_dir=Path(COVER_LETTER_DIR),
            test_mode=TEST_MODE,
        )
        return await easy_applier.job_apply(job)

    async def send_report(self, result: str) -> None:
        """Send Telegram report"""
        try:
            reporter = TelegramReportSender()
            await reporter.send_report(
                result=result,
                applies_num=self.applies_num,
                error_num=self.error_num,
            )
        except Exception as e:
            logger.warning(f"Failed to send Telegram report: {e}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _extract_job_from_card(self, card: Any) -> Job | None:
        """Extract job data from an Indeed job card element"""
        try:
            title_el = await find_element_safely(card, INDEED_JOB_TITLE_SELECTOR, timeout=3000)
            if not title_el:
                return None
            title = await title_el.text_content() or ""
            job_url_path = await title_el.get_attribute("href") or ""
            job_url = (
                job_url_path
                if job_url_path.startswith("http")
                else f"https://www.indeed.com{job_url_path}"
            )

            company_el = await find_element_safely(card, INDEED_COMPANY_SELECTOR, timeout=3000)
            company = (await company_el.text_content() or "") if company_el else ""

            location_el = await find_element_safely(card, INDEED_LOCATION_SELECTOR, timeout=3000)
            location = (await location_el.text_content() or "") if location_el else ""

            easy_apply_badge = await find_element_safely(
                card, INDEED_EASY_APPLY_BADGE, timeout=2000
            )
            apply_method = "easy_apply" if easy_apply_badge else "external"

            job = Job(
                job_title=sanitize_text(title),
                company_name=sanitize_text(company),
                location=sanitize_text(location),
                url=job_url,
                apply_method=apply_method,
            )
            return job

        except Exception as e:
            logger.warning(f"Failed to extract job from card: {e}")
            await debug_capture(self.page, "extract_job_error")
            return None

    async def _dismiss_overlays(self) -> None:
        """Dismiss Indeed overlay portals that intercept clicks"""
        for selector in ["ifl-portal", "div.gnav-hovbc7"]:
            try:
                count = await self.page.locator(selector).count()
                if count > 0:
                    await self.page.evaluate(
                        f"document.querySelectorAll('{selector}').forEach(el => el.remove())"
                    )
                    logger.debug(f"Dismissed {count} overlay(s) matching '{selector}'")
            except Exception:
                pass

    async def _go_to_next_page(self) -> bool:
        """Click next page button and return True if successful"""
        try:
            next_btn = await find_element_safely(self.page, INDEED_NEXT_PAGE_SELECTOR, timeout=5000)
            if not next_btn:
                logger.info("No next page button found - reached last page")
                return False
            await self._dismiss_overlays()
            clicked = await safe_click(self.page, INDEED_NEXT_PAGE_SELECTOR, timeout=5000)
            if not clicked:
                logger.debug("Normal click failed, retrying with force")
                await next_btn.click(force=True, timeout=5000)
            await self.page.wait_for_load_state("domcontentloaded")
            pause(1, 2)
            logger.info(f"Moved to page {self.page_num + 2}")
            return True
        except Exception as e:
            logger.warning(f"Could not navigate to next page: {e}")
            await debug_capture(self.page, "next_page_error")
            return False

    async def _handle_apply_result(self, result: str, job: Job, cover_letter: str) -> None:
        """Save job result to the appropriate YAML file"""
        record = {
            "title": job.job_title,
            "company": job.company_name,
            "location": job.location,
            "link": job.url,
            "date": datetime.now().isoformat(),
        }
        filename_map = {
            "success": "success.yaml",
            "skipped": "skipped.yaml",
            "error": "failed.yaml",
        }
        filename = filename_map.get(result, "failed.yaml")
        self._save_data_to_yaml(filename, record)

        if result == "success":
            self.applies_num += 1
            self.success_applies_num += 1
        elif result == "error":
            self.error_num += 1

    def _job_is_already_seen(self, job: Job) -> Tuple[bool, str]:
        """Check if job was already processed in a previous run"""
        all_companies = {
            **self.success_companies,
            **self.skipped_companies,
            **self.failed_companies,
        }
        company_key = sanitize_text(job.company_name)
        if self.apply_once_at_company and company_key in all_companies:
            return True, "apply_once_at_company"
        return False, ""

    def _check_the_previous_apply_number(self) -> int:
        """Return total successful applications from previous runs"""
        try:
            data = self._load_data_from_yaml("success.yaml")
            if isinstance(data, list):
                return len(data)
            return 0
        except Exception:
            return 0

    def _load_cache(self) -> JobManagerCache:
        try:
            data = self._load_data_from_yaml("cache.yaml")
            if isinstance(data, dict):
                return JobManagerCache(**data)
        except Exception:
            pass
        return JobManagerCache()

    def _load_companies_from_yaml(self, filename: str) -> Dict[str, Any]:
        try:
            data = self._load_data_from_yaml(filename)
            if isinstance(data, list):
                return {sanitize_text(entry.get("company", "")): entry for entry in data if entry}
            return {}
        except Exception:
            return {}

    def _load_data_from_yaml(self, filename: str):
        try:
            filepath = Path(OUTPUT_DIR) / filename
            if not filepath.exists():
                return []
            with open(filepath, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or []
        except Exception as e:
            logger.warning(f"Could not load {filename}: {e}")
            return []

    def _save_data_to_yaml(self, filename: str, record: Dict) -> None:
        try:
            filepath = Path(OUTPUT_DIR) / filename
            existing = self._load_data_from_yaml(filename)
            if not isinstance(existing, list):
                existing = []
            existing.append(record)
            with open(filepath, "w", encoding="utf-8") as f:
                yaml.dump(existing, f, allow_unicode=True)
        except Exception as e:
            logger.error(f"Could not save to {filename}: {e}")

    @staticmethod
    def _define_output_file(filename: str) -> Path:
        return Path(OUTPUT_DIR) / filename
