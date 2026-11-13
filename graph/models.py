import hashlib
import re
from typing import Literal

from pydantic import BaseModel, Field


class RawJob(BaseModel):
    source: str
    url: str
    title: str
    company: str
    external_id: str | None = None
    location: str | None = None
    snippet: str = ""

    @property
    def fingerprint(self) -> str:
        """Cross-source identity: same company+title+location is the same job."""
        parts = (self.company, self.title, self.location or "")
        norm = "|".join(re.sub(r"\s+", " ", part.strip().lower()) for part in parts)
        return hashlib.sha1(norm.encode()).hexdigest()

    @property
    def url_key(self) -> str:
        return self.url.split("?")[0].rstrip("/").lower()


class JobRequirements(BaseModel):
    title: str = ""
    company: str = ""
    location: str | None = None
    remote_policy: Literal["remote", "hybrid", "onsite", "unknown"] = "unknown"
    seniority: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    currency: str | None = None
    must_have_skills: list[str] = Field(default_factory=list)
    nice_to_have_skills: list[str] = Field(default_factory=list)
    years_experience_min: float | None = None
    responsibilities: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    visa_sponsorship: bool | None = None
    application_method: Literal["easy_apply", "external_form", "email", "unknown"] = "unknown"


class ResumeFactDraft(BaseModel):
    section: Literal["experience", "project", "education", "certification", "other"]
    organization: str | None = None
    text: str
    skills: list[str] = Field(default_factory=list)


class ParsedResumeDraft(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    links: list[str] = Field(default_factory=list)
    headline: str | None = None
    total_years_experience: float = 0.0
    skills: list[str] = Field(default_factory=list)
    facts: list[ResumeFactDraft] = Field(default_factory=list)


class ResumeFact(ResumeFactDraft):
    fact_id: str


class ParsedResume(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    links: list[str] = Field(default_factory=list)
    headline: str | None = None
    total_years_experience: float = 0.0
    skills: list[str] = Field(default_factory=list)
    facts: list[ResumeFact] = Field(default_factory=list)


class FitAssessment(BaseModel):
    semantic_fit: float = Field(ge=0.0, le=1.0)
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    rationale: str = ""


class TailoredBullet(BaseModel):
    source_fact_id: str
    text: str


class TailoredResume(BaseModel):
    summary: str
    bullets: list[TailoredBullet]
    skills: list[str] = Field(default_factory=list)


class CoverLetterDraft(BaseModel):
    body: str
    referenced_fact_ids: list[str] = Field(default_factory=list)
