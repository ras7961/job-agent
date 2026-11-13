import operator
from typing import Annotated, Any, TypedDict


class AgentState(TypedDict, total=False):
    run_id: str
    search_query: dict
    resume_text: str | None
    resume_path: str | None
    screening_answers: dict

    raw_jobs: list[dict]
    jobs: list[dict]
    cursor: int
    resume: dict | None

    requirements: dict | None
    match: dict | None
    decision: dict | None
    tailored_resume: dict | None
    resume_pdf_key: str | None
    cover_letter: dict | None
    cover_letter_pdf_key: str | None
    human_feedback: str | None
    revision_count: int
    approval: dict | None
    apply_result: dict | None

    failed: bool
    fatal: bool
    failure: dict | None

    results: Annotated[list[dict], operator.add]
    errors: Annotated[list[dict], operator.add]


PER_JOB_RESET: dict[str, Any] = {
    "requirements": None, "match": None, "decision": None,
    "tailored_resume": None, "resume_pdf_key": None,
    "cover_letter": None, "cover_letter_pdf_key": None,
    "human_feedback": None, "revision_count": 0,
    "approval": None, "apply_result": None,
    "failed": False, "failure": None,
}


def make_initial_state(
    run_id: str,
    search_query: dict,
    *,
    resume_path: str | None = None,
    resume_text: str | None = None,
    screening_answers: dict | None = None,
) -> AgentState:
    if not (resume_path or resume_text):
        raise ValueError("Provide resume_path or resume_text")
    return AgentState(
        run_id=run_id,
        search_query=search_query,
        resume_path=resume_path,
        resume_text=resume_text,
        screening_answers=screening_answers or {},
        raw_jobs=[],
        jobs=[],
        cursor=0,
        resume=None,
        results=[],
        errors=[],
        fatal=False,
        **PER_JOB_RESET,
    )