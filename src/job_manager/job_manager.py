import os
import traceback
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

from config.app_config import COLLECT_INFO_MODE
from config.constants import OUTPUT_DIR
from config.logger_config import logger
from src.pydantic_models.job_models import Job, JobInfo
from src.utils.utils import sanitize_text, save_yaml_file


class BaseJobManager(ABC):
    @abstractmethod
    def set_parameters(self, parameters: Dict[str, Any]):
        pass

    @abstractmethod
    def start_applying(self) -> None:
        pass

    @abstractmethod
    def apply_job(self, vacancy: Any) -> str:
        pass

    @abstractmethod
    def easy_apply(self, job: Job) -> Tuple[str, str]:
        pass

    @abstractmethod
    def send_report(self, result: str) -> None:
        pass

    @staticmethod
    def _define_output_file(filename: str) -> Path:
        """Define the path to the output file"""
        try:
            output_file = os.path.join(Path(OUTPUT_DIR), filename)
            logger.info(f"The path to the output file has been defined: {output_file}")
        except Exception:
            tb_str = traceback.format_exc()
            logger.error(f"Error in defining the location of the file: {tb_str}")
            raise
        return output_file

    def _update_skill_stat(self, skills) -> None:
        """Update the statistics of the most demanded skills in the vacancy and save it to a file"""
        logger.info("Updating the statistics of the most demanded skills in the vacancy")
        for skill in skills:
            if ";" in skill:
                processed_skills = self._process_skill_string(skill)
                for s in processed_skills:
                    self.skill_stat[s] = self.skill_stat.get(s, 0) + 1
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
        """Determine in which category to save the company and save it to the corresponding YAML file"""
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

        try:
            job_info = JobInfo(
                job_title=company_job_title,
                url=vacancy["url"],
                skip_reason=reason,
            )
        except Exception as e:
            logger.warning(f"Error in saving job info: {e}")
            return

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
        """Load file with already viewed companies and their vacancies.
        Handles both dict format (LinkedIn-style) and legacy list format (older Indeed output)."""
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
            logger.warning(f"File {filename} not found, returning empty dict")
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
                        if job_title == job_info["job_title"]:
                            logger.warning("The vacancy has already been encountered, skipping")
                            return True, "The vacancy has already been encountered"
        return False, ""
