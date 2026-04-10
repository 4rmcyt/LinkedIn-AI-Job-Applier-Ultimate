import time
from pathlib import Path
from typing import Any, List, Tuple

from playwright.sync_api import Page

from config.app_config import (
    COLLECT_INFO_MODE,
    EASY_APPLY_ONLY_MODE,
    MAX_APPLIES_NUM,
    MINIMUM_WAIT_TIME_SEC,
    MONKEY_MODE,
    TEST_MODE,
)
from config.constants import ANSWERS_FILE, COVER_LETTER_DIR, RESUME_DIR, SEARCH_CONFIG_FILE
from config.logger_config import logger
from src.job_manager.indeed.easy_applier_indeed import IndeedEasyApplier
from src.job_manager.job_manager import BaseJobManager
from src.pydantic_models.job_models import Job
from src.telegram.telegram_manager import TelegramReportSender
from src.utils.browser_utils import (
    debug_capture,
    find_element_safely,
    find_elements_safely,
    safe_click,
)
from src.utils.utils import async_pause, load_yaml_file, sanitize_text

search_config = load_yaml_file(SEARCH_CONFIG_FILE)
logger.info(f"Maximum allowed number of applications: {MAX_APPLIES_NUM}")

INDEED_JOB_CARD_SELECTOR = "div.job_seen_beacon, div[data-testid='jobcard-wrapper']"
INDEED_JOB_TITLE_SELECTOR = "h2.jobTitle a, [data-testid='jobTitle'] a"
INDEED_COMPANY_SELECTOR = "[data-testid='company-name'], .companyName"
INDEED_LOCATION_SELECTOR = "[data-testid='text-location'], .companyLocation"
INDEED_EASY_APPLY_BADGE = "span.iaLabel, [data-testid='ia-badge']"
INDEED_APPLY_BUTTON = "#indeedApplyButton, [data-testid='indeedApplyButton-test'], .jobsearch-IndeedApplyButton-buttonWrapper"
INDEED_NEXT_PAGE_SELECTOR = (
    "a[data-testid='pagination-page-next'], nav[role='navigation'] a[aria-label='Next Page']"
)


class IndeedJobManager(BaseJobManager):
    """Class for searching and sending applications to employers on Indeed"""

    def __init__(
        self, page: Page, linkedin_email: str, resume_anonymizer: Any, search_component: Any
    ):
        logger.info("Initializing IndeedJobManager")
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

        logger.info("IndeedJobManager successfully initialized")

    # ------------------------------------------------------------------
    # Interface methods (same signatures as LinkedIn LinkedInJobManager)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Main application loop
    # ------------------------------------------------------------------

    async def start_applying(self) -> None:
        """Main loop: iterate over all search URLs and apply to jobs"""
        logger.info("IndeedJobManager starting application process")

        search_urls = self.search_component.get_search_urls()
        logger.info(f"Indeed search URLs: {search_urls}")

        for url in search_urls:
            if self.applies_num >= self.max_applies_num:
                logger.info("Reached maximum number of applications")
                break

            self.page_num = 0
            await self.page.goto(url, wait_until="domcontentloaded")
            await async_pause(1, 2)

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
            await self._scroll_left_panel()
            cards = await find_elements_safely(self.page, INDEED_JOB_CARD_SELECTOR)
            return cards or []
        except Exception as e:
            logger.warning(f"Could not find job cards: {e}")
            await debug_capture(self.page, "vacancies_not_found")
            return []

    async def _scroll_left_panel(self) -> None:
        """Scroll the full page to trigger lazy-loading of job cards"""
        await self.page.evaluate(
            """
            () => new Promise((resolve) => {
                const distance = document.body.scrollHeight;
                const durationMs = 2000;
                const startTime = performance.now();
                function step(now) {
                    const progress = Math.min((now - startTime) / durationMs, 1);
                    window.scrollTo(0, distance * progress);
                    if (progress < 1) requestAnimationFrame(step);
                    else resolve();
                }
                requestAnimationFrame(step);
            })
            """
        )
        await async_pause(1, 2)
        await self.page.evaluate("() => window.scrollTo(0, 0)")

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

            if job.apply_method != "easy_apply" and EASY_APPLY_ONLY_MODE:
                logger.info(f"Skipping external apply job: {job.job_title} at {job.company_name}")
                return "skipped"

            minimum_job_time = time.time() + MINIMUM_WAIT_TIME_SEC
            new_page = await self.page.context.new_page()
            try:
                await new_page.goto(job.url, wait_until="domcontentloaded")
                await async_pause(1, 2)
                job.job_description = await self._extract_job_description_from_page(new_page)

                if MONKEY_MODE or COLLECT_INFO_MODE:
                    job_is_interesting = True
                    score, reasoning = 0, "Monkey mode"
                else:
                    interest_result = self.llm_answerer_component.job_is_interesting(
                        job.model_dump()
                    )
                    if interest_result is None:
                        await self._handle_apply_result("error", job, "")
                        return "error"
                    job_is_interesting, score, reasoning = interest_result

                if not job_is_interesting:
                    logger.info(
                        f"Skipping uninteresting job: {job.job_title} at {job.company_name}"
                    )
                    return "skipped"

                job.skills = self._extract_skills_from_vacancy(job)
                self.llm_answerer_component.set_job(job.model_dump())
                if int(score) > 0:
                    self._update_skill_stat(self.job_key_skills)
                    self._save_interesting_job(job, score, reasoning)

                if not EASY_APPLY_ONLY_MODE and job.apply_method == "external":
                    if TEST_MODE:
                        result, cover_letter = "skipped", ""
                    else:
                        result, cover_letter = await self.llm_agent_component.apply_to_job(job.url)
                else:
                    result, cover_letter = await self.easy_apply(job, new_page)

                await self._handle_apply_result(result, job, cover_letter)
                return result
            finally:
                time_left = int(minimum_job_time - time.time())
                if time_left > 0:
                    await async_pause(time_left, time_left + 1)
                await new_page.close()
                await self.page.bring_to_front()

        except Exception as e:
            logger.error(f"Error in apply_job: {e}", exc_info=True)
            await debug_capture(self.page, "apply_job_error")
            self.error_num += 1
            return "error"

    async def easy_apply(self, job: Job, page: Any = None) -> Tuple[str, str]:
        """Delegate application to IndeedEasyApplier"""
        easy_applier = IndeedEasyApplier(
            page=page,
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

            logger.debug(
                f"Extracting job card: title={title!r}, company={company!r}, url={job_url!r}"
            )

            # Check for easy apply badge on card first
            easy_apply_badge = await find_element_safely(
                card, INDEED_EASY_APPLY_BADGE, timeout=1000
            )
            if easy_apply_badge:
                apply_method = "easy_apply"
            else:
                # Click the card to open detail panel and check for apply button
                await title_el.click()
                await async_pause(0.5, 1)
                apply_button = await find_element_safely(
                    self.page, INDEED_APPLY_BUTTON, timeout=3000
                )
                apply_method = "easy_apply" if apply_button else "external"

            job = Job(
                job_title=sanitize_text(title),
                company_name=sanitize_text(company),
                location=sanitize_text(location),
                url=job_url,
                apply_method=apply_method,
            )
            return job

        except Exception as e:
            logger.warning(
                f"Failed to extract job from card: title={title!r}, company={company!r}, url={job_url!r} — {e}"
                if "title" in dir()
                else f"Failed to extract job from card (title not yet parsed): {e}"
            )
            await debug_capture(self.page, "extract_job_error")
            return None

    async def _extract_job_description_from_page(self, page: Any) -> str:
        """Extract job description text from an Indeed job detail page"""
        selectors = [
            "#jobDescriptionText",
            "[data-testid='jobsearch-jobDescriptionText']",
            ".jobsearch-jobDescriptionText",
            "#job-description",
        ]
        for selector in selectors:
            try:
                el = await find_element_safely(page, selector, timeout=3000)
                if el:
                    text = await el.text_content()
                    if text:
                        return text.strip()
            except Exception:
                continue
        logger.debug("Could not extract job description from Indeed page")
        return ""

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
            await async_pause(1, 2)
            logger.info(f"Moved to page {self.page_num + 2}")
            return True
        except Exception as e:
            logger.warning(f"Could not navigate to next page: {e}")
            await debug_capture(self.page, "next_page_error")
            return False

    async def _handle_apply_result(self, result: str, job: Job, cover_letter: str) -> None:
        """Save job result to the appropriate YAML file"""
        result_map = {"success": "Success", "skipped": "Skip", "error": "Error"}
        self._save_company(job, (result_map.get(result, "Error"), ""), {"url": job.url})

        if result == "success":
            self.applies_num += 1
            self.success_applies_num += 1
            self.total_applies_num += 1
            self.cache.success_applies_num = self.success_applies_num
            self.cache.total_applies_num = self.total_applies_num
            self.cache.update_last_apply()
            self._write_the_last_search_time()
        elif result == "error":
            self.error_num += 1
