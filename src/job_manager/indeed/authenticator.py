from typing import Union

from playwright.sync_api import Page

from config.logger_config import logger
from src.utils.browser_utils import find_element_safely, safe_click, safe_fill
from src.utils.utils import pause


class IndeedAuthenticator:
    """Class for Indeed login and session management"""

    INDEED_LOGIN_URL = "https://secure.indeed.com/account/login"
    INDEED_HOME_URL = "https://www.indeed.com"

    def __init__(self, page: Union[Page, any] = None):
        self.page = page
        self.email = None
        self.password = None

        logger.info("Indeed authenticator initialized")

    def set_parameters(self, email: str, password: str = None) -> None:
        logger.info("Setting Indeed Authenticator parameters")
        self.email = email
        self.password = password

    async def start(self) -> bool:
        """Main method for starting authentication"""
        logger.info("Starting Indeed login process")

        if await self.is_logged_in():
            logger.info("User already logged into Indeed using saved session")
            return True

        logger.info("Saved session is invalid, performing new login")
        result = await self.handle_login()
        if result:
            logger.info("Indeed login successful")
        return result

    async def is_logged_in(self) -> bool:
        """Check if user is logged into Indeed"""
        try:
            await self.page.goto(self.INDEED_HOME_URL, wait_until="domcontentloaded")
            logger.info("Checking if user is logged into Indeed...")

            current_url = self.page.url
            if "login" in current_url or "signin" in current_url:
                logger.warning("Redirected to login page, user not authorized")
                return False

            signed_in_element = await find_element_safely(
                self.page, "[data-gnav-element-name='SignIn']", timeout=5000
            )
            if signed_in_element:
                logger.warning("Sign-in button visible, user is not logged in")
                return False

            logger.info("User appears to be logged into Indeed")
            return True

        except Exception as e:
            logger.error(f"Error checking Indeed authorization status: {e}")
            return False

    async def handle_login(self) -> bool:
        """Navigate to Indeed login and authenticate"""
        logger.info("Navigating to Indeed login page...")
        try:
            await self.page.goto(self.INDEED_LOGIN_URL)
            pause(1, 2)
            return await self.enter_credentials()
        except Exception as e:
            logger.error(f"Error accessing Indeed login: {e}")
            return False

    async def enter_credentials(self) -> bool:
        """Enter email on Indeed login form and wait for manual login completion"""
        logger.info("Entering email in Indeed login form...")
        try:
            if not await safe_fill(self.page, "input[type='email']", self.email):
                logger.error("Failed to fill email field")
                return False
            logger.info("Email entered. Please complete login manually within 60 seconds...")

            continue_selectors = [
                "button[type='submit']",
                "#login-submit-button",
            ]
            for selector in continue_selectors:
                if await safe_click(self.page, selector, timeout=10000):
                    break

            pause(60, 60)

            return await self.check_login_success()

        except Exception as e:
            logger.error(f"Error during Indeed credential entry: {e}")
            return False

    async def check_login_success(self) -> bool:
        """Verify successful Indeed login"""
        try:
            for attempt in range(30):
                current_url = self.page.url
                logger.debug(f"Login check attempt {attempt + 1}, current URL: {current_url}")

                success_patterns = [
                    "indeed.com/jobs",
                    "indeed.com/myjobs",
                    "indeed.com/?",
                    "indeed.com/account",
                ]
                if any(pattern in current_url for pattern in success_patterns):
                    # Confirm sign-in button is gone
                    sign_in_btn = await find_element_safely(
                        self.page, "[data-gnav-element-name='SignIn']", timeout=3000
                    )
                    if not sign_in_btn:
                        logger.info("Indeed login successful - reached authenticated page")
                        return True

                if "login" in current_url or "signin" in current_url:
                    error_locator = self.page.locator(".icl-Alert--error, .error-message")
                    if await error_locator.count() > 0:
                        error_text = await error_locator.first.text_content()
                        if error_text and error_text.strip():
                            logger.error(f"Indeed login error: {error_text.strip()}")
                            return False

                if "/challenge" in current_url or "captcha" in current_url:
                    logger.warning("Indeed security challenge detected - waiting for resolution")
                    pause(60, 60)

                pause(1, 2)

            logger.info("Indeed login timeout reached")
            return False

        except Exception as e:
            logger.error(f"Error checking Indeed login success: {e}")
            return False
