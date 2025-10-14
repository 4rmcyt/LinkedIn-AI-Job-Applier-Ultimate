import os
import re
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml
from playwright.sync_api import Page

from config.app_config import (
    COLLECT_INFO_MODE,
    EASY_APPLY_ONLY_MODE,
    MAX_APPLIES_NUM,
    MINIMUM_WAIT_TIME_SEC,
    MONKEY_MODE,
    TEST_MODE,
)
from config.constants import (
    ANSWERS_FILE,
    COVER_LETTER_DIR,
    OUTPUT_DIR,
    RESUME_DIR,
    SEARCH_CONFIG_FILE,
)
from config.logger_config import logger
from src.job_manager.easy_applier import EasyApplier
from src.pydantic_models.job_models import Job, JobInfo, JobManagerCache
from src.telegram.telegram_manager import TelegramReportSender
from src.utils.browser_utils import (
    find_element_safely,
    find_elements_safely,
    get_clean_text,
    get_element_attribute_safely,
    get_element_text,
    is_scrollable,
    safe_click,
    scroll_slowly,
)
from src.utils.utils import load_yaml_file, pause, sanitize_text, save_yaml_file, sleep

search_config = load_yaml_file(SEARCH_CONFIG_FILE)
logger.info(f"Maximum allowed number of applications: {MAX_APPLIES_NUM}")

LAST_RUN_FILE = Path(OUTPUT_DIR) / "last_run.yaml"


class JobApplier:
    """Class for searching and sending applications to employers"""

    def __init__(
        self, page: Page, linkedin_email: str, resume_anonymizer: Any, search_component: Any
    ):
        logger.info("Initializing JobApplier")
        self.page = page
        self.linkedin_email = linkedin_email
        self.resume_anonymizer = resume_anonymizer
        self.search_component = search_component
        self.llm_answerer_component = None
        self.llm_agent_component = None
        self.resume_generator_manager = None
        self.jobs_no_info = (
            []
        )  # vacancies to which applications were not sent due to missing information
        self.job_key_skills = []  # key skills according to employer's opinion
        self.interesting_jobs = []
        self.page_num = 0
        self.resume_vac_page_num = -1  # number of pages with vacancies similar to resume
        self.error_num = 0
        self.total_applies_num = 0
        self.resume_recommendations = ""

        logger.info("JobApplier successfully initialized")

    def set_parameters(self, parameters: Dict[str, Any]):
        """Setting JobApplier parameters"""
        logger.info("Setting JobApplier parameters")
        # set maximum number of applications
        self.max_applies_num = MAX_APPLIES_NUM
        # load additional search settings
        self.apply_once_at_company = parameters.get("apply_once_at_company", True)
        # load job blacklist
        self.job_blacklist = parameters.get("job_blacklist", [])
        if self.job_blacklist:
            self.job_blacklist = [sanitize_text(j_b) for j_b in self.job_blacklist]
        # load companies to which applications were successfully sent
        self.success_companies = self._load_companies_from_yaml("success.yaml")
        # load companies to which applications were not sent
        self.skipped_companies = self._load_companies_from_yaml("skipped.yaml")
        # load companies to which applications were not sent due to software error
        self.failed_companies = self._load_companies_from_yaml("failed.yaml")
        # load list of questions to which answers were already given
        self.seen_answers = self._load_data_from_yaml("answers.yaml")
        # load statistics of most demanded skills in vacancies
        self.skill_stat = self._load_data_from_yaml("skill_stat.yaml")
        # load list of interesting jobs
        self.interesting_jobs = self._load_data_from_yaml("interesting_jobs.yaml")
        self.interesting_jobs = [JobInfo(**job) for job in self.interesting_jobs]
        # load cache with information about last search
        self.cache = self._load_cache()
        self.applies_num = 0
        self.previous_apply_number = self._check_the_previous_apply_number()
        self.success_applies_num = self.previous_apply_number
        self.total_applies_num = self.cache.total_applies_num
        logger.info("Parameters successfully set")

    def set_answerer_and_agent(self, llm_answerer_component: Any, llm_agent_component: Any):
        """
        Set LLM for answering questions and writing cover letters
        """
        self.llm_answerer_component = llm_answerer_component
        self.llm_agent_component = llm_agent_component

    def set_resume(self, resume: Dict[str, Any]) -> None:
        """Add resume for analysis"""
        self.resume = resume

    def set_resume_generator_manager(self, resume_generator_manager: Any):
        """
        Set resume generator manager for writing resumes
        """
        self.resume_generator_manager = resume_generator_manager

    async def get_vacancies_from_page(self) -> List[Any]:
        """Parse job vacancies from current LinkedIn page (async)"""
        logger.info(f"Parsing job vacancies from LinkedIn page {self.page_num}")
        vacancies = []

        try:
            # Scroll to load all job listings on the page
            await self._scroll_to_load_jobs()

            # Find all job listing elements on the current page using multiple selectors
            job_selectors = [
                "//*[starts-with(@class, 'flex-grow-1')]",
                "div[data-job-id]",
                ".jobs-search-results__list-item",
                ".job-card-container",
                ".base-card",
                ".job-card-list__entity-lockup",
                ".scaffold-layout__list-item",
            ]

            job_elements = []
            for selector in job_selectors:
                by = "xpath" if selector.startswith("//") else "css selector"
                elements = await find_elements_safely(self.page, selector, by)
                if elements:
                    job_elements = elements
                    logger.debug(f"Found {len(elements)} job elements using selector: {selector}")
                    break

            logger.info(f"Found {len(job_elements)} job elements on page {self.page_num}")

            for job_element in job_elements:
                vacancy = {}
                try:
                    # Try to extract job URL and ID
                    job_url = await self._extract_job_url(job_element)
                    if job_url:
                        match = re.search(r"/jobs/view/(\d+)", job_url)
                        job_id = match.group(1) if match else None
                        vacancy["url"] = job_url
                        vacancy["id"] = job_id
                        vacancies.append(vacancy)
                    else:
                        logger.debug("Could not extract URL from job element")

                except Exception as e:
                    logger.warning(f"Error parsing job element: {e}")
                    continue

            # If no jobs found on first page, log warning
            if self.page_num == 0 and len(vacancies) == 0:
                logger.warning("No job listings found on LinkedIn search page")

            logger.info(
                f"Successfully parsed {len(vacancies)} job vacancies from page {self.page_num}"
            )

        except Exception as e:
            logger.error(f"Error parsing job vacancies from page {self.page_num}: {e}")
            # Return empty list on error to continue processing
            return []

        return vacancies

    async def start_applying(self) -> None:
        """Send applications to all employers on all pages (async)"""
        self.easy_applier_component = EasyApplier(
            self.page,
            self.llm_answerer_component,
            self.resume_anonymizer,
            self.resume_generator_manager,
            ANSWERS_FILE,
            RESUME_DIR,
            COVER_LETTER_DIR,
            TEST_MODE,
        )
        # define the start time of the search
        if self.cache.last_run:
            last_run = self.cache.get_last_run_datetime()
            # if this is not the first launch - increase the time of the last search by 24 hours
            # and write it as the last search (to avoid the drift of the start time of the program)
            self.cache.last_run = (last_run + timedelta(hours=24)).isoformat()
        else:
            self.cache.update_last_run()
        result = ""
        # write recommendations for improving the resume
        self.resume_improvement_recommendations()
        # continue until the maximum number of applications is reached
        while self.success_applies_num < self.max_applies_num and self.applies_num < 400:
            # go through all pages until they are finished
            vacancies = await self.get_vacancies_from_page()
            if len(vacancies) == 0:
                if self.page_num == 1:
                    logger.warning("No vacancies found for the search query")
                break
            for vacancy in vacancies:
                url = vacancy.get("url")
                try:
                    result = await self.apply_job(vacancy)
                    if result == "Limit":
                        logger.warning("Maximum number of applications reached")
                        break
                except Exception:
                    tb_str = traceback.format_exc()
                    logger.error(f"Unknown error on the page: {url}\n{tb_str}")
                    # counter of repeated errors, if too many errors in a row -
                    # exit the program and send a notification
                    if self.error_num == MAX_APPLIES_NUM:
                        logger.error(f"Critical number of consecutive errors {MAX_APPLIES_NUM}")
                        result = "Error"
                        break
                    else:
                        self.error_num += 1
                    continue
                else:
                    self.error_num = 0
            # break the search for vacancies if the limit is reached
            if result == "Limit" or result == "Error":
                break
            # go to the next page
            await self._go_to_next_page()
        logger.info(f"Applications sent: {self.success_applies_num}")
        logger.info("Ending the work.")
        await self.send_report(result)

    async def apply_job(self, vacancy: Dict[str, Any]) -> str:
        """Send applications to all employers on the page (async)"""
        # Open vacancy in a new window/tab
        self._new_page = await self.page.context.new_page()
        self._original_page = self.page
        self.page = self._new_page
        self.easy_applier_component.set_page(self.page)

        # Navigate to job page
        try:
            await self.page.goto(vacancy["url"])
        except Exception as e:
            logger.error(f"Failed to navigate to job URL: {vacancy['url']}, error: {e}")
            await self._new_page.close()
            pause()
            self.page = self._original_page
            self.easy_applier_component.set_page(self.page)
            return "Error"

        # scrape the vacancy
        job = await self._get_detailed_job_description()
        minimum_job_time = time.time() + MINIMUM_WAIT_TIME_SEC
        company_name = job.company_name
        company_job_title = job.job_title
        logger.info(f"Found a vacancy {company_job_title}")
        # if the vacancy has not been seen yet and the company is not in the blacklist
        # - start the process of applying to the vacancy
        if not job.is_valid_for_application():
            reason = "Job is not valid for application. Reason: "
            if not job.job_title:
                reason += "Job is empty\n"
            elif not job.company_name:
                reason += "Company name is empty\n"
            elif not job.url:
                reason += "URL is empty\n"
            if not job.job_description:
                reason += "Job description is empty\n"
            apply_result = "Skip", reason
            logger.warning(f"Job is not valid for application, skipping:\n{reason}")
            pause(1, 2)
        elif self._is_blacklisted(sanitize_text(company_name)):
            apply_result = "Skip", "Vacancy in the blacklist"
            logger.warning("Vacancy in the blacklist, skipping")
            pause(1, 2)
        else:
            is_seen, reason = self._job_is_already_seen(job)
            if is_seen:
                apply_result = "Skip", reason
                logger.warning(f"Skipping the vacancy for the reason: {reason}")
                pause(1, 2)
            else:
                # set the vacancy to answerer and agent for evaluation
                self.llm_answerer_component.set_job(job.model_dump())
                if MONKEY_MODE is True and COLLECT_INFO_MODE is False:
                    # in 'monkey mode' any vacancy is considered interesting
                    job_is_interesting = True
                else:
                    # otherwise ask LLM to evaluate if the vacancy is interesting or not
                    (
                        job_is_interesting,
                        score,
                        reasoning,
                    ) = self.llm_answerer_component.job_is_interesting()
                    self._save_interesting_job(job, score, reasoning)
                # apply to the vacancy only if it is interesting
                if job_is_interesting:
                    # update the list of required skills for the vacancy only if the vacancy is interesting
                    self._update_skill_stat(self.job_key_skills)
                    if EASY_APPLY_ONLY_MODE is False:
                        apply_url = await self._check_apply_button()
                        if apply_url:
                            if TEST_MODE is False:
                                apply_result = await self.llm_agent_component.apply_to_job(
                                    apply_url
                                )
                            else:
                                apply_result = "Skip", "Test mode"
                        else:
                            apply_result = await self.easy_apply(job)
                    else:
                        apply_result = await self.easy_apply(job)
                    result, reason = apply_result
                    # if the vacancy is skipped for the reason of missing information, add it to the list of vacancies,
                    # information about which will then be sent to the client
                    if result == "Skip" and reason.startswith("Could not"):
                        self._collect_job_info(
                            company_job_title, company_name, vacancy["url"], reason
                        )
                elif job_is_interesting is None:
                    apply_result = "Error", "Error calling LLM."
                else:
                    apply_result = "Skip", "Vacancy is not interesting"
                    logger.debug("Vacancy is not interesting, skipping")
        # Switch back to the original window
        await self._new_page.close()
        pause()
        self.page = self._original_page
        self.easy_applier_component.set_page(self.page)
        # if we are in one of the information collection modes - do not track vacancy application statistics
        if COLLECT_INFO_MODE is True:
            return "OK"
        result = self.get_apply_result(apply_result, job, vacancy, minimum_job_time)
        return result

    async def easy_apply(self, job: Job) -> Tuple[str, str]:
        """Apply to the vacancy using LinkedIn Easy Apply functionality (async)"""
        try:
            if COLLECT_INFO_MODE is True:
                # if we are in the mode of collecting information for interesting jobs and skill statistics -
                # do not apply to the vacancy, only save gathered information to files
                logger.info(
                    "We are in the mode of collecting skill statistics or searching for interesting jobs - do not apply to the vacancy"
                )
            else:
                # Use LinkedIn Easy Apply functionality
                apply_result = await self.easy_applier_component.apply_to_job(job)
                if apply_result[0] == "Success":
                    logger.info(
                        f"Successfully applied to the vacancy of the company {job.company_name}"
                    )
                elif apply_result[0] == "Limit":
                    logger.warning("Reached the limit of applications")
                    return "Limit", ""
                else:
                    logger.warning(
                        f"Skipping the vacancy of the company {job.company_name} for the reason: {apply_result[1]}"
                    )
                    return apply_result
            pause()
        except Exception as e:
            tb_str = traceback.format_exc()
            logger.error(
                f"Unknown error on the page {job.url} during applying to the vacancy {job.job_title} of the company {job.company_name}\n{tb_str}"
            )
            return "Error", str(e)
        return "Success", ""

    def get_apply_result(
        self,
        apply_result: Tuple[str, str],
        job: Job,
        vacancy: Dict[str, Any],
        minimum_job_time: float,
    ) -> Tuple[str, str]:
        """Get the apply result"""
        result, _ = apply_result
        # increase the counters of all applications and successful applications
        self.applies_num += 1
        if result == "Success":
            self.success_applies_num += 1
            self.total_applies_num += 1
            self.cache.success_applies_num = self.success_applies_num
            self.cache.total_applies_num = self.total_applies_num
            self.cache.update_last_apply()
            self._write_the_last_search_time()
            logger.info(
                f"Number of vacancies, on which successfully applied: {self.success_applies_num}"
            )
            logger.info(f"Total number of successful applications: {self.total_applies_num}")
        if result != "Limit":
            self._save_company(job, apply_result, vacancy)
        # if the page was processed faster than the minimum time -
        # wait until this time is over
        time_left = int(minimum_job_time - time.time())
        if time_left > 0:
            sleep((time_left, time_left + 5))
        # if we hit the limit on vacancies - stop applying
        if result == "Limit":
            return "Limit"
        stop_reason = ""
        if self.success_applies_num >= self.max_applies_num:
            stop_reason = f"The maximum number of applications has been reached: {self.success_applies_num}/{self.max_applies_num}"
        if stop_reason:
            logger.info(stop_reason)
            return "Limit"
        return result

    def resume_improvement_recommendations(self) -> None:
        """
        Write recommendations for improving the resume
        """
        resume_recommendations_file = self._define_output_file("resume_recommendations.txt")
        try:
            with open(resume_recommendations_file, "r", encoding="utf-8") as f:
                resume_recommendations = f.read()
        except FileNotFoundError:
            resume_recommendations = ""
        # if the file with recommendations has not been created yet - write recommendations for improving the resume
        # and save them to a file
        if not resume_recommendations:
            self.resume_recommendations = (
                self.llm_answerer_component.resume_improvement_recommendations()
            )
            self.resume_recommendations = self.resume_anonymizer.deanonymize_text(
                self.resume_recommendations
            )
            with open(resume_recommendations_file, "w", encoding="utf-8") as f:
                f.write(self.resume_recommendations)

    async def send_report(self, result: str) -> None:
        """
        After the resume sending is completed, send a report, which will contain
        the number of vacancies, on which the applications were sent, the list of vacancies,
        on which the application was not sent for some reason,
        as well as recommendations for improving the resume
        """
        # if the search was successful - send a report about the work done
        if not (TEST_MODE is True or COLLECT_INFO_MODE is True) and result != "Error":
            # if at least one vacancy was sent successfully since the start
            # write the time of the last search and send a report
            if self.previous_apply_number < self.success_applies_num:
                logger.info("Sending a report about the work done in Telegram")
                bot = TelegramReportSender()
                await bot.send_telegram_report(
                    self.linkedin_email,
                    self.resume,
                    self.success_applies_num,
                    self.jobs_no_info,
                    self.skill_stat,
                    self.resume_recommendations,
                    self.resume_anonymizer,
                )
                self._write_the_last_search_time()

    def check_the_last_search_time(self) -> bool:
        """
        Check if the job search was started not earlier than 24 hours after the previous start.
        Or check if the last application was less than an hour ago
        This means that the application was forcibly restarted.
        """
        logger.info(
            "Checking if the job search was started not earlier than 24 hours after the previous start"
        )
        if self.cache.last_run:
            last_run = self.cache.get_last_run_datetime()
        else:
            return True
        if (
            datetime.now() - last_run
        ).total_seconds() >= 60 * 60 * 24 or self.previous_apply_number > 0:
            return True
        return False

    def _load_cache(self) -> JobManagerCache:
        """Load cache from file"""
        try:
            with open(LAST_RUN_FILE, "r") as f:
                cache = yaml.safe_load(f) or {}
                cache = JobManagerCache(**cache)
                return cache
        except Exception:
            logger.warning("Could not load cache from file")
            return JobManagerCache()

    async def _scroll_to_load_jobs(self):
        """Scroll the job results container to load all job listings (async)"""
        scrollable_elements = []

        async def _get_children(element, current_depth: int = 0, max_depth: int = 3) -> None:
            """Get all children of the element (Playwright Locator-aware) - async."""
            if current_depth > max_depth:
                return
            try:
                # Use Playwright locator scoping for child selection
                if hasattr(element, "locator"):
                    child_locator = element.locator(":scope > *")
                    count = await child_locator.count()
                    for i in range(count):
                        child = child_locator.nth(i)
                        try:
                            if await is_scrollable(child):
                                scrollable_elements.append(child)
                            else:
                                await _get_children(child, current_depth + 1, max_depth)
                        except Exception:
                            continue
            except Exception:
                pass

        try:
            # Find the LinkedIn job results container using safe methods
            job_container = None
            container_selectors = [
                ".scaffold-layout__list",
                ".scaffold-layout__list-container",
                "[data-job-id]",
                ".jobs-search-results-list",
                ".jobs-search-results__list",
                ".jobs-search-results__list-container",
                ".jobs-search-results",
            ]

            for selector in container_selectors:
                job_container = await find_element_safely(self.page, selector, "css selector")
                if job_container:
                    logger.debug(f"Found job container with selector: {selector}")
                    break
            else:
                logger.debug("Could not find job container with selector: {selector}")
                return

            # Try to scroll all scrollable elements
            await _get_children(job_container)

            for element in scrollable_elements:
                try:
                    # Scroll to bottom, then back to top to load all content
                    if await scroll_slowly(element, "down"):
                        pause(0.2, 0.3)
                        await scroll_slowly(element, "up")
                except Exception as e:
                    logger.debug(f"Element scrolling failed: {e}")

        except Exception as e:
            logger.warning(f"Error during job container scrolling: {e}")

    async def _extract_job_url(self, job_element) -> str:
        """Extract job URL from job element using multiple selector strategies (async)"""
        logger.debug("Extracting job URL from element")
        # Try different selectors for job links
        link_selectors = [
            "a[href*='/jobs/view/']",
            "a[data-control-name='job_card_title']",
            ".base-card__full-link",
            ".job-card-container__link",
            ".jobs-search-results__list-item-action",
        ]

        for selector in link_selectors:
            try:
                href = await get_element_attribute_safely(job_element, selector, "href")
                if href and "/jobs/view/" in href:
                    if not href.startswith("https://linkedin.com"):
                        href = "https://linkedin.com" + href
                    return href
            except Exception:
                continue

        return None

    async def _get_detailed_job_description(self) -> Job:
        """Get detailed job description by extracting specific sections from the job page (async)"""
        job = Job()

        # Extract job ID and set URL from current URL - framework agnostic
        try:
            current_url = self.page.url
            if "/jobs/view/" in current_url:
                match = re.search(r"/jobs/view/(\d+)", current_url)
                if match:
                    job.job_id = match.group(1)
                job.url = current_url
        except Exception as e:
            logger.warning(f"Could not extract URL information: {e}")

        try:
            job.job_title = await self._extract_job_title()
            job.company_name = await self._extract_company_name()
            job.job_description = await self._extract_job_description()
            job.company_description = await self._extract_company_description()
            job.recruiter_link = await self._get_job_recruiter()
            pause(1, 2)
            await self._extract_skills_and_preferences(job)

        except Exception as e:
            logger.warning(f"Could not get detailed job description: {e}")

        return job

    async def _extract_company_name(self) -> str:
        """Extract company name from the job page using multiple selector strategies (async)"""
        company_name_selectors = [
            ".job-details-jobs-unified-top-card__company-name a",
            ".jobs-unified-top-card__company-name a",
            "*[class*='company-name'] a",
            "a[data-control-name='job_details_topcard_company_url']",
        ]

        for selector in company_name_selectors:
            company_name = await get_element_text(self.page, selector)
            if company_name:
                logger.debug(f"Found company name '{company_name}' using selector: {selector}")
                return company_name

        # Fallback to xpath approach
        xpath_selectors = [
            "//*[contains(@class, 'company-name')]",
        ]

        for xpath_selector in xpath_selectors:
            elements = await find_elements_safely(self.page, xpath_selector, "xpath")
            for element in elements:
                try:
                    text = await get_clean_text(element)
                    if text:
                        logger.debug(f"Found company name '{text}' using xpath: {xpath_selector}")
                        return text
                except Exception:
                    continue

        logger.debug("Could not extract company name from job page")
        return None

    async def _extract_job_title(self) -> str:
        """Extract job title from the job page using multiple selector strategies (async)"""
        job_title_selectors = [
            ".job-details-jobs-unified-top-card__job-title h1",
            ".jobs-unified-top-card__job-title h1",
            "*[class*='job-title'] h1",
            "h1.jobs-unified-top-card__job-title-link",
        ]

        for selector in job_title_selectors:
            job_title = await get_element_text(self.page, selector)
            if job_title:
                logger.debug(f"Found job title '{job_title}' using selector: {selector}")
                return job_title

        # Fallback to xpath approach
        xpath_selectors = [
            "//*[contains(@class, 'job-title')]",
        ]

        for xpath_selector in xpath_selectors:
            elements = await find_elements_safely(self.page, xpath_selector, "xpath")
            for element in elements:
                try:
                    text = await get_clean_text(element)
                    if text:
                        logger.debug(f"Found job title '{text}' using xpath: {xpath_selector}")
                        return text
                except Exception:
                    continue

        logger.debug("Could not extract job title from job page")
        return None

    async def _extract_job_description(self) -> str:
        """Extract "About the job" section using multiple selector strategies (async)"""
        about_job_selectors = [
            ".jobs-box__html-content",
            ".jobs-description",
            "[data-test-id='job-description']",
            ".jobs-description-content",
            ".jobs-box__html-content .jobs-description-content__text",
            ".job-details-job-description__content",
            ".jobs-description__container .jobs-description-content__text",
        ]

        for selector in about_job_selectors:
            job_description = await get_element_text(self.page, selector)
            if job_description:
                logger.debug(f"Found job description using selector: {selector}")
                # Clean up the text
                job_description = job_description.strip()
                if len(job_description) > 50:  # Ensure it's substantial content
                    return job_description

        logger.debug("Could not extract job description from job page")
        return None

    async def _extract_company_description(self) -> str:
        """Extract company description from the job page (async)"""
        company_description = None
        about_company_selectors = [
            ".jobs-company__box",
            ".jobs-company__description",
            ".jobs-company__overview",
            ".jobs-company__box .jobs-company__description",
            ".jobs-company__overview .jobs-company__description",
        ]

        for selector in about_company_selectors:
            element_text = await get_element_text(self.page, selector)
            if element_text:
                element_text = " ".join(element_text.split("\n")[:-1])
                company_description = element_text
                return company_description

    async def _extract_skills_and_preferences(self, job: Job):
        """Extract skills information from skill match element (async)"""
        try:
            skills_buttons = self.page.locator("button[aria-label='Skills']")
            if await skills_buttons.count() > 0:
                await skills_buttons.first.click(timeout=1000)
            else:
                buttons = await find_elements_safely(self.page, "//button", "xpath")
                for button in buttons:
                    try:
                        txt = (await button.text_content() or "").lower()
                        if "skills" in txt:
                            await button.click(timeout=1000)
                            break
                    except Exception:
                        continue
            pause()
            # Wait for the skill page to appear
            skill_element = "//*[starts-with(@class, 'job-details-preferences-and-skills__modal-section-insights-list-item')]"
            skills = await find_elements_safely(self.page, skill_element, "xpath")

            job_types = [
                "Full-time",
                "Part-time",
                "Contract",
                "Temporary",
                "Volunteer",
                "Internship",
                "Apprenticeship",
                "Other",
                "On-site",
                "Hybrid",
                "Remote",
                "$",
            ]
            # Extract skills and preferences using clean text extraction
            clean_texts = []
            for s in skills:
                text = await get_clean_text(s)
                clean_texts.append(text)
            clean_texts = [text for text in clean_texts if text]  # Remove empty strings

            job_skills = [text for text in clean_texts if not any([j in text for j in job_types])]
            preferences = [text for text in clean_texts if any([j in text for j in job_types])]

            # Save skills and preferences
            self.job_key_skills = job_skills
            job.skills = ", ".join(job_skills)
            job.preferences = ", ".join(preferences)

            # Extract additional metadata from preferences
            for pref in preferences:
                pref_lower = pref.lower()
                if any(
                    job_type in pref_lower
                    for job_type in [
                        "full-time",
                        "part-time",
                        "contract",
                        "temporary",
                        "volunteer",
                        "internship",
                        "other",
                    ]
                ):
                    job.employment_type = pref
                elif any(
                    level in pref_lower
                    for level in ["entry", "mid", "senior", "lead", "principal", "director"]
                ):
                    job.experience_level = pref
                elif "$" in pref or "salary" in pref_lower or "compensation" in pref_lower:
                    job.salary_range = pref
                elif any(work_type in pref_lower for work_type in ["remote", "on-site", "hybrid"]):
                    if "remote" in pref_lower:
                        job.is_remote = True

            # Close the skill page
            pause()
        except Exception as e:
            logger.warning(f"Could not wait for the skill page: {e}")

        if skills:
            try:
                await self.page.locator("button[aria-label='Dismiss']").first.click(timeout=1000)
            except Exception as e:
                logger.warning(f"Could not close the skill page: {e}")

    async def _get_job_recruiter(self):
        """Get job recruiter information (async)"""
        logger.debug("Getting job recruiter information")
        try:
            recruiter = self.page.locator(
                "xpath=//h2[text()=\"Meet the hiring team\" or contains(text(), 'Meet the hiring team')]/following::a[contains(@href, 'linkedin.com/in/')]"
            ).first
            if await recruiter.count() > 0:
                recruiter_link = await recruiter.get_attribute("href") or ""
                logger.debug(f"Job recruiter link retrieved successfully: {recruiter_link}")
                return recruiter_link
            logger.debug("No recruiter link found in the hiring team section")
            return ""
        except Exception:
            logger.warning("Failed to retrieve recruiter information")
            return ""

    async def _check_apply_button(self) -> str:
        """Check if the apply button is present and return the URL of the apply button (async).
        If no apply button is found, return an empty string."""
        easy_apply_selectors = [
            '//button[contains(@class, "jobs-apply-button") and contains(., "Easy Apply")]',
            '//button[contains(text(), "Easy Apply") or text()="Easy Apply"]',
        ]
        for selector in easy_apply_selectors:
            easy_apply_buttons = await find_elements_safely(self.page, selector, "xpath")
            if len(easy_apply_buttons) > 0:
                return None

        apply_selectors = [
            '//button[contains(@class, "jobs-apply-button") and contains(., "Apply")]',
            '//button[contains(text(), "Apply") or text()="Apply"]',
        ]
        for selector in apply_selectors:
            apply_buttons = await find_elements_safely(self.page, selector, "xpath")
            if len(apply_buttons) > 0:
                return await self._get_button_link(apply_buttons)
        return None

    async def _get_button_link(self, apply_buttons: List[Any]) -> str:
        """Get the link of the button (Playwright context) - async."""
        try:
            for button in apply_buttons:
                if not (await button.is_visible() and await button.is_enabled()):
                    logger.debug("Apply button is not visible or enabled")
                    continue
                async with self.page.context.expect_page() as new_page_info:
                    logger.debug("Clicking apply button")
                    await button.first.click(timeout=1000)
                new_page = await new_page_info.value
                pause()
                link = new_page.url
                pause()
                await new_page.close()
                logger.debug(f"Apply button link is obtained successfully: {link}")
                return link
            logger.warning("No apply button found")
            return ""
        except Exception:
            logger.warning("Failed to get the link of the apply button")
            return ""

    def _check_the_previous_apply_number(self) -> bool:
        """
        Check if there were applications without a completed search.
        If yes, return the number of applications
        """
        logger.info("Checking the time of the last application")
        if self.cache.last_apply:
            last_apply = self.cache.get_last_apply_datetime()
        else:
            return 0
        # If the previous search was not completed, and therefore less than an hour has passed since the last application,
        # then we consider starting from the previous number of applications
        if (datetime.now() - last_apply).total_seconds() < 59 * 60:
            prev_apply_num = self.cache.success_applies_num
            return prev_apply_num
        return 0

    def _write_the_last_search_time(self) -> None:
        """
        Write the time of the last job search
        """
        save_yaml_file(LAST_RUN_FILE, self.cache.model_dump())

    def _collect_job_info(
        self, company_job_title: str, company_name: str, job_link: str, reason: str
    ) -> None:
        """Add information about the vacancy to the list of vacancies for subsequent sending of a report to the client"""
        job_info = JobInfo(
            job_title=company_job_title,
            company_name=company_name,
            url=job_link,
            skip_reason=reason,
        )
        self.jobs_no_info.append(job_info.model_dump())

    @staticmethod
    def _define_output_file(filename: str) -> Path:
        """Define the path to the output file"""
        try:
            output_file = os.path.join(Path("data/output"), filename)
            logger.info(f"The path to the output file has been defined: {output_file}")
        except Exception:
            tb_str = traceback.format_exc()
            logger.error(f"Error in defining the location of the file: {tb_str}")
            raise
        return output_file

    def _update_skill_stat(self, skills):
        """Update the statistics of the most demanded skills in the vacancy and save it to a file"""
        logger.info("Updating the statistics of the most demanded skills in the vacancy")
        for skill in skills:
            if ";" in skill:
                processed_skills = self._process_skill_string(skill)
                for skill in processed_skills:
                    self.skill_stat[skill] = self.skill_stat.get(skill, 0) + 1
            else:
                self.skill_stat[skill] = self.skill_stat.get(skill, 0) + 1
        self.skill_stat = sorted(self.skill_stat.items(), key=lambda x: x[1], reverse=True)
        self.skill_stat = {k: v for k, v in self.skill_stat}
        self._save_data_to_yaml(self.skill_stat, "skill_stat.yaml", sort_keys=False)

    def _process_skill_string(self, skill_string: str) -> List[str]:
        """Split the string with skills into a list of skills"""
        processed_skills = []
        for part in skill_string.split(";"):
            cleaned = "".join(char for char in part if char.isalnum() or char.isspace())
            cleaned = cleaned.strip()
            if cleaned:
                processed_skills.append(cleaned)
        return processed_skills

    def _save_company(
        self,
        job: Job,
        apply_result: Tuple[str, str],
        vacancy: Dict[str, Any],
    ) -> None:
        """
        Determine in which category to save the company and information about it,
        and then save it to the corresponding YAML file
        """
        company_name = job.company_name
        company_job_title = job.job_title

        result, reason = apply_result

        if result == "Success":
            companies = self.success_companies
            filename = "success.yaml"
        elif result == "Skip":
            companies = self.skipped_companies
            filename = "skipped.yaml"
        else:
            companies = self.failed_companies
            filename = "failed.yaml"

        seen_companies = companies

        job_info = JobInfo(
            job_title=company_job_title,
            url=vacancy["url"],
            skip_reason=reason,
        )

        # Check by company_id and/or by job title
        if company_name:
            if company_name in seen_companies:
                seen_companies[company_name].append(job_info.model_dump())
            else:
                seen_companies[company_name] = [job_info.model_dump()]

        self._save_company_to_yaml(filename, companies)

    def _save_company_to_yaml(self, filename: str, companies: List[Dict[str, str]]) -> None:
        """Save already viewed companies and their vacancies to a file"""
        output_file = self._define_output_file(filename)
        logger.info("Saving data about the vacancy in YAML")
        try:
            save_yaml_file(output_file, companies)
            logger.info("Data about the company and its vacancy successfully saved to YAML file")
        except Exception:
            tb_str = traceback.format_exc()
            logger.error(
                f"Error in saving information about viewed companies in YAML file\n{tb_str}"
            )
            raise Exception("Error in saving information about viewed companies in YAML file")

    def _load_companies_from_yaml(self, filename: str) -> Dict[str, List[dict]]:
        """Load file with already viewed companies and their vacancies"""
        output_file = self._define_output_file(filename)
        logger.info(f"Loading companies from YAML file: {output_file}")
        try:
            with open(output_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            logger.info(
                "Data about companies and their vacancies successfully loaded from YAML file"
            )
            if not data:
                return {}
            return data
        except FileNotFoundError:
            logger.warning(f"File {filename} not found, returning empty list")
            return {}
        except Exception:
            tb_str = traceback.format_exc()
            logger.error(f"Error loading information about viewed companies in YAML file\n{tb_str}")
            return {}

    def _save_interesting_job(self, job: Job, score: int, reasoning: str) -> None:
        """Save interesting job to a file"""
        logger.info("Saving interesting job to a file")
        interesting_job = JobInfo(
            job_title=job.job_title,
            company_name=job.company_name,
            url=job.url,
            interest_score=score,
            interest_reason=reasoning,
            skills=self.job_key_skills,
        )
        self.interesting_jobs.append(interesting_job)
        self.interesting_jobs = sorted(
            self.interesting_jobs, key=lambda x: int(x.interest_score), reverse=True
        )
        self._save_data_to_yaml(
            [job.model_dump() for job in self.interesting_jobs], "interesting_jobs.yaml"
        )
        logger.info("Interesting job successfully saved to a file")

    def _save_data_to_yaml(
        self,
        data: List[Dict[str, Any]] | Dict[str, Any] | str,
        filename: str,
        sort_keys: bool = True,
    ) -> None:
        """Save data to a file"""
        output_file = self._define_output_file(filename)
        logger.info(f"Saving data to file {filename}")
        try:
            save_yaml_file(output_file, data, sort_keys=sort_keys)
            logger.info(f"Data successfully saved to the file {filename}")
        except Exception:
            tb_str = traceback.format_exc()
            logger.error(f"Error in saving data to the file {filename}\n{tb_str}")
            raise Exception(f"Error in saving data to the file {filename}")

    def _load_data_from_yaml(self, filename: str) -> List[Dict[str, Any]] | Dict[str, Any] | str:
        """Load data from a file"""
        output_file = self._define_output_file(filename)
        logger.info(f"Loading data from file: {filename}")
        try:
            with open(output_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if (
                    filename == "answers.yaml" or filename == "interesting_jobs.yaml"
                ) and not isinstance(data, list):
                    raise ValueError(
                        f"The format of the file {filename} is incorrect, we expect a list"
                    )
            logger.info(f"Data successfully loaded from the file {filename}")
            return data
        except FileNotFoundError:
            logger.warning(f"The file {filename} was not found, returning an empty list")
            if filename == "answers.yaml" or filename == "interesting_jobs.yaml":
                return []
            return {}
        except Exception:
            tb_str = traceback.format_exc()
            logger.error(f"Error in loading the list of data from the file {filename}\n{tb_str}")
            raise Exception(f"Error in loading data from the file {filename}")

    def _is_blacklisted(self, company: str) -> bool:
        """Check if the company is in the blacklist"""
        if company in self.job_blacklist:
            logger.warning("The company is in the blacklist, skipping")
            return True
        return False

    def _job_is_already_seen(self, job: Job) -> Tuple[bool, str]:
        """Check if we have already applied to this vacancy"""
        company_name = job.company_name
        job_title = job.job_title
        if COLLECT_INFO_MODE is True:
            my_companies = self.interesting_jobs
            for job_info in my_companies:
                if company_name == job_info.company_name and job_title == job_info.job_title:
                    logger.warning("The vacancy has already been encountered, skipping")
                    return True, "The vacancy has already been encountered"
        else:
            my_companies = self.success_companies
            for comp in my_companies:
                if sanitize_text(company_name) == sanitize_text(comp):
                    if self.apply_once_at_company and COLLECT_INFO_MODE is False:
                        logger.warning(
                            "The company has already been encountered and the setting is not to apply "
                            "again to the same company, skipping"
                        )
                        return (
                            True,
                            "The company has already been encountered and the setting is not to apply "
                            "again to the same company",
                        )
                    for job_info in my_companies[comp]:
                        if job_title == job_info.job_title:
                            logger.warning("The vacancy has already been encountered, skipping")
                            return True, "The vacancy has already been encountered"
        return False, ""

    async def _go_to_next_page(self) -> None:
        """Go to the next page using framework-agnostic methods (async)"""
        self.page_num += 1
        logger.info(f"Going to the page {self.page_num}")

        # Try multiple selectors for next page button
        next_page_selectors = [
            "//button[contains(@aria-label, 'next')]",
            "//button[contains(@aria-label, 'Next')]",
            "button[aria-label*='Next']",
            "button[aria-label*='next']",
            ".jobs-search-results-list__pagination button[aria-label*='next']",
            "button.artdeco-pagination__button--next",
        ]

        page_clicked = False
        for selector in next_page_selectors:
            if await safe_click(self.page, selector):
                logger.debug(f"Clicked next page using selector: {selector}")
                page_clicked = True
                break

        if not page_clicked:
            # Fallback - try to find element first, then click
            for selector in next_page_selectors:
                by = "xpath" if selector.startswith("//") else "css selector"
                element = await find_element_safely(self.page, selector, by)
                if element:
                    try:
                        await element.click(timeout=1000)
                        logger.debug(f"Clicked next page element using selector: {selector}")
                        page_clicked = True
                        break
                    except Exception as e:
                        logger.debug(f"Failed to click next page element: {e}")
                        continue

        if not page_clicked:
            logger.warning("Could not find or click next page button")

        pause(2, 3)


if __name__ == "__main__":
    """Test script to verify Playwright scrolling functionality"""
    import asyncio

    from src.utils.browser_utils import create_playwright_browser

    async def test_linkedin_job_extraction():
        """Test finding job elements and extracting URLs from LinkedIn jobs page (async)"""
        print("🚀 Starting LinkedIn job extraction test...")

        async def extract_job_url_from_element(job_element):
            """Extract job URL from job element using multiple selector strategies (async)"""
            print(f"🔍 Extracting job URL from element type: {type(job_element)}")
            # Try different selectors for job links
            link_selectors = [
                "a[href*='/jobs/view/']",
                "a[data-control-name='job_card_title']",
                ".base-card__full-link",
                ".job-card-container__link",
                ".jobs-search-results__list-item-action",
            ]

            for selector in link_selectors:
                try:
                    href = await get_element_attribute_safely(job_element, selector, "href")
                    if href and "/jobs/view/" in href:
                        print(f"✅ Found job URL using selector '{selector}': {href}")
                        return href
                except Exception as e:
                    print(f"⚠️ Failed with selector '{selector}': {e}")
                    continue

            print("❌ Could not extract job URL from element")
            return None

        try:
            # Create Playwright browser instance (async)
            browser, context, page = await create_playwright_browser()
            print("✅ Browser created successfully (async)")

            # Navigate to LinkedIn jobs search
            print("🔗 Navigating to LinkedIn jobs search...")
            await page.goto("https://linkedin.com/jobs/search")
            print("✅ Page loaded")

            # Wait a bit for dynamic content to load
            pause(5, 6)

            # Find all job listing elements using multiple selectors
            print("🔍 Looking for job elements...")
            job_selectors = [
                "//*[starts-with(@class, 'flex-grow-1')]",
                "div[data-job-id]",
                ".jobs-search-results__list-item",
                ".job-card-container",
                ".base-card",
                ".job-card-list__entity-lockup",
                ".scaffold-layout__list-item",
            ]

            job_elements = []
            for selector in job_selectors:
                print(f"🔍 Trying selector: {selector}")
                try:
                    if selector.startswith("//"):
                        # XPath selector
                        elements = await page.locator("xpath=" + selector).all()
                    else:
                        # CSS selector
                        elements = await page.locator(selector).all()

                    if elements:
                        job_elements = elements
                        print(f"✅ Found {len(elements)} job elements using selector: {selector}")
                        break
                except Exception as e:
                    print(f"⚠️ Selector '{selector}' failed: {e}")
                    continue

            if not job_elements:
                print("❌ No job elements found!")
                # Debug: show what elements are available
                all_elements = await page.locator("*").all()
                print(f"📋 Total elements on page: {len(all_elements)}")

                # Show elements with job-related classes
                job_related = await page.locator("[class*='job']").all()
                print(f"📋 Elements with 'job' in class: {len(job_related)}")
                return

            print(f"🎯 Processing {len(job_elements)} job elements...")

            # Extract URLs from each job element
            extracted_urls = []
            for i, job_element in enumerate(job_elements[:5]):  # Test first 5 elements
                print(f"\n📝 Processing job element {i + 1}/{min(5, len(job_elements))}...")
                job_url = await extract_job_url_from_element(job_element)
                if job_url:
                    extracted_urls.append(job_url)
                    # Extract job ID from URL
                    match = re.search(r"/jobs/view/(\d+)", job_url)
                    job_id = match.group(1) if match else "unknown"
                    print(f"✅ Job {i + 1} - ID: {job_id}")
                else:
                    print(f"❌ Job {i + 1} - No URL found")

            # Summary
            print("\n📊 SUMMARY:")
            print(f"Total job elements found: {len(job_elements)}")
            print(f"URLs successfully extracted: {len(extracted_urls)}")
            print(f"Success rate: {len(extracted_urls) / min(5, len(job_elements)) * 100:.1f}%")

            if extracted_urls:
                print("\n🔗 Sample URLs:")
                for i, url in enumerate(extracted_urls[:3]):
                    print(f"  {i + 1}. {url}")

            # Keep browser open for a moment to see results
            print("\n⏳ Keeping browser open for 10 seconds to observe results...")
            pause(10, 11)

        except Exception as e:
            print(f"❌ Test failed with error: {e}")
            import traceback

            traceback.print_exc()

        finally:
            try:
                # Clean up (async)
                await context.close()
                await browser.close()
                print("✅ Browser closed successfully")
            except Exception:
                pass

    # Run the async test
    asyncio.run(test_linkedin_job_extraction())
