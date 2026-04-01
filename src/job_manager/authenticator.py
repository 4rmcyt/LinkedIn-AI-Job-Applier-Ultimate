from pathlib import Path
from typing import Union

from playwright.sync_api import Page

from config.logger_config import logger
from src.utils.browser_utils import find_element_safely, safe_click, safe_fill
from src.utils.utils import pause


class LinkedInAuthenticator:
    """Class for LinkedIn login and session management"""

    def __init__(self, page: Union[Page, any] = None):
        self.page = page
        self.email = None
        self.password = None
        self.session_file = Path("data/linkedin_session.json")

        logger.info("LinkedIn authenticator initialized")

    def set_parameters(self, email: str, password: str) -> None:
        logger.info("Setting LinkedIn Authenticator parameters")
        self.email = email
        self.password = password

    async def start(self) -> bool:
        """Main method for starting authentication (async)"""
        logger.info("Starting LinkedIn login process")

        # Load saved session if available
        logger.info("Checking if user is logged into LinkedIn...")
        if await self.is_logged_in():
            logger.info("User already logged into LinkedIn using saved session")
            return True
        else:
            logger.info("Saved session is invalid, performing new login")

        # Perform new login
        result = await self.handle_login()
        if result:
            logger.info("LinkedIn login successful, session saved")
        return result

    async def is_logged_in(self) -> bool:
        """Check if user is logged into LinkedIn (async)"""
        try:
            await self.page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
            logger.info("Checking if user is logged into LinkedIn...")

            # Check current URL to see if we're redirected to login
            current_url = self.page.url
            if "/login" in current_url or "/uas/login" in current_url:
                logger.warning("Redirected to login page, user not authorized")
                return False

            # Additional check - look for the main nav bar (reliable across LinkedIn redesigns)
            nav_selectors = [
                "nav[aria-label='Main']",
                "button[aria-label*='Home']",
                ".feed-shared-update-v2",
                "[data-view-name='feed-full-content']",
            ]
            for selector in nav_selectors:
                element = await find_element_safely(self.page, selector, timeout=5000)
                if element:
                    logger.info(f"Logged-in indicator found ({selector}), user is logged in")
                    return True

            logger.warning("Could not determine authorization status, assuming not logged in")
            return False

        except Exception as e:
            logger.error(f"Error checking authorization status: {e}")
            return False

    async def handle_login(self) -> bool:
        """Login to LinkedIn (async)"""
        logger.info("Navigating to LinkedIn login page...")

        try:
            await self.page.goto("https://www.linkedin.com/login")
            pause(1, 2)
            return await self.enter_credentials()

        except Exception as e:
            logger.error(f"Error accessing LinkedIn login: {e}")
            return False

    async def enter_credentials(self) -> bool:
        """Enter user credentials (async)"""
        logger.info("Entering user credentials in LinkedIn...")

        try:
            # Try to find saved session
            profile_locator = self.page.locator(".member__profile")
            if await profile_locator.count() > 0:
                logger.info("Profile locator found")
            else:  # if saved session is invalid, then fill email field
                if not await safe_fill(self.page, "#username", self.email):
                    logger.error("Failed to fill email field")
                    return False
                logger.info("Email entered")

            # Wait for and fill password field
            if not await safe_fill(self.page, "#password", self.password):
                logger.error("Failed to fill password field")
                return False
            logger.info("Password entered")

            pause(1, 2)

            # Click login button with multiple selectors
            login_selectors = [
                "button[type='submit']",
                "button[data-id='sign-in-form__submit-btn']",
                ".btn__primary--large",
            ]

            for selector in login_selectors:
                if await safe_click(self.page, selector, timeout=10000):
                    logger.info(f"Login button clicked using selector: {selector}")
                    break
            else:
                logger.error("Could not find or click login button")
                return False

            # Wait for login to complete
            pause(3, 5)

            # Check login success
            if await self.check_login_success():
                logger.info("LinkedIn login successful")
                return True
            else:
                logger.error("Login verification failed")
                return False

        except Exception as e:
            logger.error(f"Error during credential entry: {e}")
            return False

    async def check_login_success(self) -> bool:
        """Check login success with improved detection (async)"""
        try:
            # Wait up to 30 seconds for login process to complete
            for attempt in range(30):
                current_url = self.page.url
                logger.debug(f"Login check attempt {attempt + 1}, current URL: {current_url}")

                # Success URL patterns
                success_patterns = [
                    "/feed/",
                    "/in/",
                    "/mynetwork/",
                    "/notifications/",
                    "/messaging/",
                ]

                # Check if we reached a success page
                if current_url == "https://www.linkedin.com/" or any(
                    pattern in current_url for pattern in success_patterns
                ):
                    logger.info("Login successful - reached authenticated page")
                    return True

                # If still on login page - check for errors or challenges
                if "/login" in current_url or "/uas/login" in current_url:
                    # Check for various error message selectors
                    error_selectors = [
                        ".form__label--error",
                        ".alert--error",
                        ".mercado-flash-error",
                        "[data-test='error-message']",
                        ".login-form__error-message",
                    ]

                    for error_selector in error_selectors:
                        error_locator = self.page.locator(error_selector)
                        if await error_locator.count() > 0:
                            error_text = await error_locator.first.text_content()
                            if error_text and error_text.strip():
                                logger.error(f"Login error detected: {error_text.strip()}")
                                return False

                    # Check for CAPTCHA or security challenge
                    challenge_selectors = [
                        ".captcha-form",
                        ".challenge-form",
                        ".security-challenge",
                        "[data-test='captcha-form']",
                    ]

                    for challenge_selector in challenge_selectors:
                        challenge_locator = self.page.locator(challenge_selector)
                        if await challenge_locator.count() > 0:
                            logger.warning(
                                "Security challenge or CAPTCHA detected - manual intervention may be required"
                            )
                            # Continue waiting for user to solve challenge
                            break

                # Check for redirect or checkpoint pages
                if "/checkpoint/challenge" in current_url or "/challenge" in current_url:
                    logger.warning(
                        "LinkedIn security checkpoint detected - waiting 60s for resolution before next try"
                    )
                    pause(60, 60)

                pause(1, 2)

            # Final attempt - check if we can detect logged-in state
            logger.info("Login timeout reached")
            return False

        except Exception as e:
            logger.error(f"Error checking login success: {e}")
            return False
