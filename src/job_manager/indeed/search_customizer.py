"""
Module for customizing Indeed job search parameters.
"""

from typing import Any, Union
from urllib.parse import quote_plus

from playwright.sync_api import Page

from config.logger_config import logger
from src.job_manager.search_customizer import BaseSearchCustomizer
from src.utils.utils import async_pause

# Indeed experience level mapping
_EXPERIENCE_LEVEL_MAP = {
    "internship": "internship",
    "entry_level": "entry_level",
    "associate": "associate",
    "mid_senior_level": "mid_level",
    "director": "director",
    "executive": "executive",
}

# Indeed job type mapping
_JOB_TYPE_MAP = {
    "full_time": "fulltime",
    "part_time": "parttime",
    "contract": "contract",
    "temporary": "temporary",
    "internship": "internship",
}

# Indeed date posted mapping
_DATE_POSTED_MAP = {
    "past_24_hours": "1",
    "past_week": "7",
    "past_month": "14",
    "any_time": "",
}

# Indeed remote/work location mapping
_REMOTE_MAP = {
    "remote": "remote",
    "hybrid": "hybrid",
    "onsite": "onsite",
}

INDEED_BASE_URL = "https://www.indeed.com/jobs"


class IndeedSearchCustomizer(BaseSearchCustomizer):
    def __init__(self, page: Union[Page, Any]):
        super().__init__(page)
        logger.info("IndeedSearchCustomizer initialized")

    def _build_search_url(self, position: str, location: str = "") -> str:
        """Build an Indeed search URL for a given position and location"""
        params = [f"q={quote_plus(position)}"]

        if location:
            params.append(f"l={quote_plus(location)}")

        # Work arrangement
        work_arrangements = []
        if self.remote:
            work_arrangements.append(_REMOTE_MAP["remote"])
        if self.hybrid:
            work_arrangements.append(_REMOTE_MAP["hybrid"])
        if self.onsite:
            work_arrangements.append(_REMOTE_MAP["onsite"])
        if work_arrangements:
            params.append(f"sc=0kf%3Aattr({','.join(work_arrangements)})")

        # Job type
        active_job_types = [
            _JOB_TYPE_MAP[k] for k, v in self.job_types.items() if v and k in _JOB_TYPE_MAP
        ]
        if active_job_types:
            params.append(f"jt={active_job_types[0]}")

        # Date posted
        for key, value in self.date_posted.items():
            if value and key in _DATE_POSTED_MAP and _DATE_POSTED_MAP[key]:
                params.append(f"fromage={_DATE_POSTED_MAP[key]}")
                break

        return f"{INDEED_BASE_URL}?{'&'.join(params)}"

    async def set_search_params(self) -> None:
        """Navigate to the first Indeed search URL"""
        if not self.positions:
            logger.warning("No positions configured for Indeed search")
            return

        location = self.locations[0] if self.locations else ""
        url = self._build_search_url(self.positions[0], location)
        logger.info(f"Navigating to Indeed search: {url}")
        await self.page.goto(url, wait_until="domcontentloaded")
        await async_pause(1, 2)

    def get_search_urls(self) -> list:
        """Return all search URL combinations (position x location)"""
        urls = []
        locations = self.locations if self.locations else [""]
        for position in self.positions:
            for location in locations:
                urls.append(self._build_search_url(position, location))
        return urls
