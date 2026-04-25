from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.job_manager.easy_applier import EasyApplier
from src.pydantic_models.job_models import Job


@pytest.fixture
def easy_applier():
    page = MagicMock()
    page.wait_for_selector = AsyncMock()
    page.locator.return_value.all = AsyncMock(return_value=[])

    with (
        patch("src.job_manager.easy_applier.get_first_pdf_file", return_value=None),
        patch.object(EasyApplier, "_load_questions", return_value=[]),
    ):
        return EasyApplier(
            page=page,
            gpt_answerer=MagicMock(),
            resume_anonymizer=MagicMock(),
            resume_generator_manager=MagicMock(),
            pause_checker=None,
            answers_file=Path("answers.yaml"),
            resume_dir=Path("resume"),
            cover_letter_dir=Path("cover_letters"),
            test_mode=True,
        )


class TestEasyApplyButtonDetection:
    @pytest.mark.asyncio
    async def test_find_easy_apply_button_returns_limit(self, easy_applier):
        job = Job(
            job_title="VP Engineering",
            company_name="Example",
            url="https://www.linkedin.com/jobs/view/12345",
        )

        with (
            patch.object(easy_applier, "check_for_premium_redirect", new_callable=AsyncMock),
            patch.object(easy_applier, "_check_easy_apply_limit", new_callable=AsyncMock) as mock_limit,
        ):
            mock_limit.return_value = True

            result = await easy_applier._find_easy_apply_button(job)

            assert result == "Limit"

    @pytest.mark.asyncio
    async def test_find_easy_apply_button_waits_for_modal(self, easy_applier):
        job = Job(
            job_title="VP Engineering",
            company_name="Example",
            url="https://www.linkedin.com/jobs/view/12345",
        )
        button = AsyncMock()
        button.is_visible.return_value = True
        button.is_enabled.return_value = True

        with (
            patch.object(easy_applier, "check_for_premium_redirect", new_callable=AsyncMock),
            patch.object(easy_applier, "_check_easy_apply_limit", new_callable=AsyncMock) as mock_limit,
            patch(
                "src.job_manager.easy_applier.find_elements_safely", new_callable=AsyncMock
            ) as mock_find_elements,
            patch.object(
                easy_applier, "_wait_for_easy_apply_dialog", new_callable=AsyncMock
            ) as mock_wait,
        ):
            mock_limit.return_value = False
            mock_find_elements.side_effect = [[button]]
            mock_wait.return_value = True

            result = await easy_applier._find_easy_apply_button(job)

            assert result is True
            button.click.assert_awaited_once()
            mock_wait.assert_awaited_once()


class TestNextButtonDetection:
    @pytest.mark.asyncio
    async def test_find_next_or_submit_button_uses_dialog_buttons(self, easy_applier):
        dialog_root = MagicMock()
        button = AsyncMock()
        dialog_root.locator.return_value.all = AsyncMock(return_value=[button])

        with (
            patch("src.job_manager.easy_applier.find_element_safely", new_callable=AsyncMock) as mock_find,
            patch("src.job_manager.easy_applier.get_clean_text", new_callable=AsyncMock) as mock_text,
        ):
            mock_find.return_value = dialog_root
            mock_text.return_value = "Review"

            next_button, button_text = await easy_applier._find_next_or_submit_button()

            assert next_button == button
            assert button_text == "review"
