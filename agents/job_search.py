import asyncio
import logging
import re

from graph.dependencies import AgentNodeContext
from graph.errors import make_error, now_iso, retry_async
from graph.models import JobRequirements, RawJob
from graph.state import AgentState
from graph.helpers import current_job, structured
from prompts.job_search import ANALYZE_SYSTEM
from pydantic import ValidationError


log = logging.getLogger("job_agent")


class JobSearchNodes(AgentNodeContext):
    async def search_jobs(self, state: AgentState) -> dict:
        """Search all sources concurrently, deduplicate results, and drop previously seen jobs."""
        query, sources = state["search_query"], self.deps.sources
        outcomes = await asyncio.gather(
            *(retry_async(lambda s=s: s.search(query), attempts=3, timeout=60, label=f"search:{s.name}")
              for s in sources),
            return_exceptions=True,
        )
        raw: list[RawJob] = []
        errors: list[dict] = []
        for source, outcome in zip(sources, outcomes):
            if isinstance(outcome, BaseException):
                errors.append(make_error(f"search_jobs[{source.name}]", outcome))
                continue
            for item in outcome:
                try:
                    raw.append(RawJob.model_validate(item))
                except ValidationError:
                    log.warning("dropping malformed job from %s: %r", source.name, item)
        if not raw and sources and len(errors) == len(sources):
            raise RuntimeError("All job sources failed")

        unique: dict[str, RawJob] = {}
        seen_urls: set[str] = set()
        for job in raw:
            if job.fingerprint in unique or job.url_key in seen_urls:
                continue
            unique[job.fingerprint] = job
            seen_urls.add(job.url_key)
        known = await self.deps.repo.seen_fingerprints(list(unique))
        fresh = [
            job for fingerprint, job in unique.items() if fingerprint not in known
        ][: self.settings.max_jobs_per_run]
        return {
            "raw_jobs": [{**job.model_dump(), "fingerprint": job.fingerprint} for job in fresh],
            "cursor": 0,
            "errors": errors,
        }

    async def scrape_jobs(self, state: AgentState) -> dict:
        """Fetch descriptions with bounded concurrency; isolate per-job scrape failures."""
        semaphore = asyncio.Semaphore(self.settings.scrape_concurrency)
        settings = self.settings

        async def scrape_one(job: dict) -> tuple[dict | None, dict | None]:
            async with semaphore:
                try:
                    page = await retry_async(
                        lambda: self.deps.scraper.fetch(job["url"]),
                        attempts=3,
                        timeout=45,
                        label="scrape",
                    )
                except Exception as exc:
                    return None, make_error("scrape_jobs", exc) | {"fingerprint": job["fingerprint"]}
            text = re.sub(r"\n{3,}", "\n\n", page.get("text", "")).strip()
            if len(text) < settings.min_jd_chars:
                error = {
                    "node": "scrape_jobs",
                    "fingerprint": job["fingerprint"],
                    "type": "ThinContent",
                    "message": f"only {len(text)} chars (login wall or empty page)",
                    "retryable": False,
                    "ts": now_iso(),
                }
                return None, error
            scraped_job = {
                **job,
                "final_url": page.get("final_url") or job["url"],
                "jd_text": text[: settings.max_jd_chars],
            }
            return scraped_job, None

        outcomes = await asyncio.gather(*(scrape_one(job) for job in state["raw_jobs"]))
        jobs = [job for job, _ in outcomes if job]
        errors = [error for _, error in outcomes if error]
        by_fingerprint = {job["fingerprint"]: job for job in state["raw_jobs"]}
        failed_results = [
            {
                "fingerprint": error["fingerprint"],
                "title": by_fingerprint[error["fingerprint"]]["title"],
                "company": by_fingerprint[error["fingerprint"]]["company"],
                "status": "scrape_failed",
            }
            for error in errors
        ]
        return {"jobs": jobs, "cursor": 0, "results": failed_results, "errors": errors}

    async def analyze_job(self, state: AgentState) -> dict:
        job = current_job(state)
        human = f"Source: {job['source']}\n<job_description>\n{job['jd_text']}\n</job_description>"
        requirements = await structured(
            self.deps.llm_fast, JobRequirements, ANALYZE_SYSTEM, human
        )
        requirements.title = requirements.title or job["title"]
        requirements.company = requirements.company or job["company"]
        requirements.location = requirements.location or job.get("location")
        return {"requirements": requirements.model_dump()}