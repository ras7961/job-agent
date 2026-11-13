import asyncio
import logging
from typing import Any, Awaitable, Callable

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agents.cover_letter import CoverLetterNodes
from agents.job_search import JobSearchNodes
from agents.matcher import MatcherNodes
from agents.resume_writer import ResumeWriterNodes
from graph.dependencies import Deps, Settings
from graph.errors import make_error, now_iso, resilient, retry_async
from graph.helpers import current_job, final_status
from graph.state import AgentState, PER_JOB_RESET


log = logging.getLogger("job_agent")


class JobAgentNodes(
    JobSearchNodes,
    MatcherNodes,
    ResumeWriterNodes,
    CoverLetterNodes,
):
    def __init__(self, deps: Deps, settings: Settings):
        self.deps = deps
        self.settings = settings

    async def human_approval(self, state: AgentState) -> dict:
        """Notify the reviewer and pause until an approval decision is resumed."""
        job = current_job(state)
        requirements, match = state["requirements"], state["match"]
        revision = state.get("revision_count", 0)
        payload = {
            "type": "approval_request",
            "run_id": state["run_id"],
            "fingerprint": job["fingerprint"],
            "revision": revision,
            "job": {
                "title": requirements["title"],
                "company": requirements["company"],
                "url": job["final_url"],
            },
            "score": match["total"],
            "strengths": match["strengths"],
            "gaps": match["gaps"],
            "missing_must_haves": match["missing_must_haves"],
            "tailored_resume": state["tailored_resume"],
            "cover_letter": state["cover_letter"]["body"],
            "resume_pdf_key": state["resume_pdf_key"],
            "cover_letter_pdf_key": state["cover_letter_pdf_key"],
            "allowed_actions": ["approve", "edit", "reject"],
        }
        idempotency_key = f"{state['run_id']}:{job['fingerprint']}:{revision}"
        await retry_async(
            lambda: self.deps.notifier.send_approval_request(
                idempotency_key=idempotency_key,
                payload=payload,
            ),
            attempts=3,
            timeout=20,
            label="notify",
        )
        reply = interrupt(payload)
        reply = reply if isinstance(reply, dict) else {}
        action = str(reply.get("action", "")).lower()
        if action not in {"approve", "edit", "reject", "expired"}:
            action = "reject"
        update: dict[str, Any] = {
            "approval": {
                "action": action,
                "feedback": reply.get("feedback"),
                "decided_at": now_iso(),
            }
        }
        if action == "edit":
            update["human_feedback"] = reply.get("feedback") or ""
        return update

    async def apply(self, state: AgentState) -> dict:
        """Submit only after explicit approval; never retry an uncertain submission."""
        if (state.get("approval") or {}).get("action") != "approve":
            raise PermissionError("ApplyNode reached without explicit human approval")
        job = current_job(state)
        requirements, resume = state["requirements"], state["resume"]
        if self.settings.dry_run:
            return {
                "apply_result": {
                    "status": "dry_run",
                    "detail": "dry_run=True; nothing submitted",
                }
            }
        if not await self.deps.repo.claim_application(job["fingerprint"], state["run_id"]):
            return {"apply_result": {"status": "duplicate", "detail": "already applied"}}

        request = {
            "idempotency_key": f"{state['run_id']}:{job['fingerprint']}",
            "job": {
                "url": job["final_url"],
                "title": requirements["title"],
                "company": requirements["company"],
                "method": requirements.get("application_method", "unknown"),
            },
            "candidate": {
                key: resume.get(key) for key in ("name", "email", "phone", "links")
            },
            "resume_pdf_key": state["resume_pdf_key"],
            "cover_letter_pdf_key": state["cover_letter_pdf_key"],
            "cover_letter_text": state["cover_letter"]["body"],
            "screening_answers": state.get("screening_answers", {}),
        }
        try:
            result = await asyncio.wait_for(
                self.deps.applier.apply(request),
                self.settings.apply_timeout_s,
            )
        except asyncio.TimeoutError:
            return {
                "apply_result": {
                    "status": "uncertain",
                    "detail": "apply timed out; verify manually",
                }
            }
        status = result.get("status")
        if status not in {"submitted", "needs_human", "failed"}:
            status = "failed"
        return {"apply_result": {**result, "status": status, "ts": now_iso()}}

    async def save_results(self, state: AgentState) -> dict:
        """Persist one job result and advance to the next job, even when saving fails."""
        job, status = current_job(state), final_status(state)
        record = {
            "run_id": state["run_id"],
            "fingerprint": job["fingerprint"],
            "status": status,
            "job": {
                key: job.get(key)
                for key in (
                    "source", "external_id", "url", "final_url",
                    "title", "company", "location",
                )
            },
            "requirements": state.get("requirements"),
            "match": state.get("match"),
            "decision": state.get("decision"),
            "tailored_resume": state.get("tailored_resume"),
            "resume_pdf_key": state.get("resume_pdf_key"),
            "cover_letter": state.get("cover_letter"),
            "cover_letter_pdf_key": state.get("cover_letter_pdf_key"),
            "approval": state.get("approval"),
            "apply_result": state.get("apply_result"),
            "failure": state.get("failure"),
            "revision_count": state.get("revision_count", 0),
            "saved_at": now_iso(),
        }
        errors: list[dict] = []
        try:
            await retry_async(
                lambda: self.deps.repo.save_record(record),
                attempts=3,
                timeout=30,
                label="save_record",
            )
        except Exception as exc:
            log.exception("failed to persist record for %s", job["fingerprint"])
            errors.append(make_error("save_results", exc, state))
        summary = {
            "fingerprint": job["fingerprint"],
            "title": job["title"],
            "company": job["company"],
            "status": status,
            "score": (state.get("match") or {}).get("total"),
        }
        return {
            "results": [summary],
            "errors": errors,
            "cursor": state["cursor"] + 1,
            **PER_JOB_RESET,
        }


def make_routes(settings: Settings) -> dict[str, Callable[[AgentState], str]]:
    def after_search(state: AgentState) -> str:
        return "end" if state.get("fatal") or not state.get("raw_jobs") else "continue"

    def after_scrape(state: AgentState) -> str:
        return "end" if state.get("fatal") or not state.get("jobs") else "continue"

    def job_step(state: AgentState) -> str:
        if state.get("fatal"):
            return "end"
        return "save" if state.get("failed") else "next"

    def after_decision(state: AgentState) -> str:
        if state.get("failed"):
            return "save"
        return "rewrite" if state["decision"]["action"] == "apply" else "save"

    def after_approval(state: AgentState) -> str:
        if state.get("fatal"):
            return "end"
        if state.get("failed"):
            return "save"
        action = (state.get("approval") or {}).get("action")
        if action == "approve":
            return "apply"
        if action == "edit" and state.get("revision_count", 0) <= settings.max_revisions:
            return "rewrite"
        return "save"

    def after_save(state: AgentState) -> str:
        return "next_job" if state["cursor"] < len(state["jobs"]) else "end"

    return {
        "after_search": after_search,
        "after_scrape": after_scrape,
        "job_step": job_step,
        "after_decision": after_decision,
        "after_approval": after_approval,
        "after_save": after_save,
    }


def build_graph(
    deps: Deps,
    settings: Settings | None = None,
    checkpointer: Any = None,
):
    """Compile the job application graph with an optional persistent checkpointer."""
    settings = settings or Settings()
    nodes, routes = JobAgentNodes(deps, settings), make_routes(settings)
    llm_timeout = settings.llm_timeout_s

    graph = StateGraph(AgentState)
    graph.add_node(
        "search_jobs",
        resilient("search_jobs", timeout=180, retries=0, fatal=True)(nodes.search_jobs),
    )
    graph.add_node(
        "scrape_jobs",
        resilient("scrape_jobs", timeout=600, retries=0, fatal=True)(nodes.scrape_jobs),
    )
    graph.add_node(
        "analyze_job",
        resilient("analyze_job", timeout=llm_timeout, retries=2)(nodes.analyze_job),
    )
    graph.add_node(
        "parse_resume",
        resilient("parse_resume", timeout=llm_timeout, retries=2, fatal=True)(nodes.parse_resume),
    )
    graph.add_node(
        "match_score",
        resilient("match_score", timeout=llm_timeout, retries=2)(nodes.match_score),
    )
    graph.add_node(
        "decision",
        resilient("decision", timeout=30, retries=2)(nodes.decision),
    )
    graph.add_node(
        "resume_rewrite",
        resilient("resume_rewrite", timeout=llm_timeout, retries=2)(nodes.resume_rewrite),
    )
    graph.add_node(
        "cover_letter",
        resilient("cover_letter", timeout=llm_timeout, retries=2)(nodes.cover_letter),
    )
    graph.add_node(
        "human_approval",
        resilient("human_approval", timeout=None, retries=0)(nodes.human_approval),
    )
    graph.add_node("apply", resilient("apply", timeout=None, retries=0)(nodes.apply))
    graph.add_node("save_results", nodes.save_results)

    graph.add_edge(START, "search_jobs")
    graph.add_conditional_edges(
        "search_jobs", routes["after_search"], {"continue": "scrape_jobs", "end": END}
    )
    graph.add_conditional_edges(
        "scrape_jobs", routes["after_scrape"], {"continue": "analyze_job", "end": END}
    )
    graph.add_conditional_edges(
        "analyze_job", routes["job_step"],
        {"next": "parse_resume", "save": "save_results", "end": END},
    )
    graph.add_conditional_edges(
        "parse_resume", routes["job_step"],
        {"next": "match_score", "save": "save_results", "end": END},
    )
    graph.add_conditional_edges(
        "match_score", routes["job_step"],
        {"next": "decision", "save": "save_results", "end": END},
    )
    graph.add_conditional_edges(
        "decision", routes["after_decision"],
        {"rewrite": "resume_rewrite", "save": "save_results"},
    )
    graph.add_conditional_edges(
        "resume_rewrite", routes["job_step"],
        {"next": "cover_letter", "save": "save_results", "end": END},
    )
    graph.add_conditional_edges(
        "cover_letter", routes["job_step"],
        {"next": "human_approval", "save": "save_results", "end": END},
    )
    graph.add_conditional_edges(
        "human_approval", routes["after_approval"],
        {"apply": "apply", "rewrite": "resume_rewrite", "save": "save_results", "end": END},
    )
    graph.add_edge("apply", "save_results")
    graph.add_conditional_edges(
        "save_results", routes["after_save"], {"next_job": "analyze_job", "end": END}
    )

    return graph.compile(checkpointer=checkpointer or InMemorySaver())


ApprovalHandler = Callable[[dict], Awaitable[dict]]


async def run_agent(
    graph,
    initial_state: AgentState,
    *,
    settings: Settings,
    on_approval: ApprovalHandler,
    thread_id: str | None = None,
) -> dict:
    """Run the graph to completion and answer each approval interrupt."""
    config = {
        "configurable": {"thread_id": thread_id or initial_state["run_id"]},
        "recursion_limit": 10 + 16 * settings.max_jobs_per_run,
    }
    graph_input: Any = initial_state
    while True:
        result = await graph.ainvoke(graph_input, config)
        pending = result.get("__interrupt__")
        if not pending:
            return result
        reply = await on_approval(pending[0].value)
        graph_input = Command(resume=reply)