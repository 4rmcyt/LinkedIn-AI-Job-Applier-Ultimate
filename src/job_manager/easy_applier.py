import traceback
from abc import ABC, abstractmethod
from typing import List, Tuple

from config.logger_config import logger
from src.pydantic_models.job_models import Job, Question
from src.utils.utils import ConfigError, load_yaml_file, sanitize_text, save_yaml_file


class NoInfoException(Exception):
    pass


class EasyApplier(ABC):
    def __init__(self) -> None:
        super().__init__()

    @abstractmethod
    async def apply_to_job(self, job: Job) -> None:
        pass

    @abstractmethod
    async def job_easy_apply(self, job: Job) -> Tuple[str, str]:
        pass

    def _save_questions(self, question_data: Question) -> None:
        """Save questions to YAML file"""
        question_data.question = sanitize_text(question_data.question)

        logger.debug(f"Checking if question data already exists: {question_data}")
        try:
            should_be_saved: bool = not self._answer_contains_company_name(question_data.answer)
            self.all_questions = [
                q for q in self.all_questions if q.question != question_data.question
            ]
            if should_be_saved:
                logger.debug("New question found, appending to YAML")
                self.all_questions.append(question_data)
                save_yaml_file(
                    self.answers_file, [question.model_dump() for question in self.all_questions]
                )
            else:
                logger.debug("Question already exists, skipping save")
        except Exception:
            tb_str = traceback.format_exc()
            logger.error(f"Error saving questions data to YAML file: {tb_str}")
            raise Exception(f"Error saving questions data to YAML file: \nTraceback:\n{tb_str}")

    def _answer_contains_company_name(self, answer: str) -> bool:
        """Check if answer contains company name"""
        return (
            isinstance(answer, str)
            and self.current_job.company_name is not None
            and self.current_job.company_name in answer
        )

    def _load_questions(self) -> List[Question]:
        logger.info(f"Loading questions from YAML file: {self.answers_file}")
        try:
            data = load_yaml_file(self.answers_file)
            logger.info("Questions loaded successfully from YAML")
            if not data:
                return []
            return [Question(**question) for question in data]
        except ConfigError:
            logger.warning("Answers file not found, returning empty list")
            return []
        except Exception:
            tb_str = traceback.format_exc()
            logger.error(f"Error loading questions data from YAML file: {tb_str}")
            raise Exception(f"Error loading questions data from YAML file: \nTraceback:\n{tb_str}")
