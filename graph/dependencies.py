from dataclasses import dataclass
from typing import Protocol

from langchain_core.language_models import BaseChatModel


@dataclass(frozen=True)
class Settings:
    match_threshold: float = 70.0
    max_missing_must_haves: int = 3
    max_jobs_per_run: int = 25
    scrape_concurrency: int = 4
    min_jd_chars: int = 300
    max_jd_chars: int = 20_000
    max_revisions: int = 2
    daily_apply_cap: int = 15
    min_salary: float | None = None
    blacklist_companies: frozenset[str] = frozenset()
    dry_run: bool = True
    llm_timeout_s: float = 120.0
    apply_timeout_s: float = 300.0


class JobSource(Protocol):
    name: str

    async def search(self, query: dict) -> list[dict]: ...


class Scraper(Protocol):
    async def fetch(self, url: str) -> dict: ...


class ResumeLoader(Protocol):
    async def load_text(self, path: str) -> str: ...


class DocumentRenderer(Protocol):
    async def render_resume(self, tailored: dict, resume: dict) -> str: ...

    async def render_cover_letter(self, body: str, resume: dict, job: dict) -> str: ...


class Notifier(Protocol):
    async def send_approval_request(self, *, idempotency_key: str, payload: dict) -> None: ...


class Applier(Protocol):
    async def apply(self, request: dict) -> dict: ...


class Repository(Protocol):
    async def seen_fingerprints(self, fingerprints: list[str]) -> set[str]: ...

    async def claim_application(self, fingerprint: str, run_id: str) -> bool: ...

    async def applications_today(self) -> int: ...

    async def save_record(self, record: dict) -> None: ...


@dataclass
class Deps:
    llm_fast: BaseChatModel
    llm_strong: BaseChatModel
    sources: list[JobSource]
    scraper: Scraper
    resume_loader: ResumeLoader
    renderer: DocumentRenderer
    notifier: Notifier
    applier: Applier
    repo: Repository


class AgentNodeContext:
    deps: Deps
    settings: Settings
