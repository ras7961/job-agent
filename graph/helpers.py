import re
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from graph.models import ParsedResume
from graph.state import AgentState


_ALIASES = {
    "postgres": "postgresql",
    "k8s": "kubernetes",
    "js": "javascript",
    "ts": "typescript",
    "py": "python",
    "golang": "go",
    "nodejs": "node",
    "reactjs": "react",
    "mongo": "mongodb",
}
_NUM = re.compile(r"\d[\d,]*\.?\d*%?")


def norm_skill(skill: str) -> str:
    normalized = re.sub(r"[^a-z0-9+#]", "", skill.lower())
    return _ALIASES.get(normalized, normalized)


def numbers(text: str) -> set[str]:
    return {match.rstrip(".,") for match in _NUM.findall(text)}


def current_job(state: AgentState) -> dict:
    return state["jobs"][state["cursor"]]


async def structured(
    llm: BaseChatModel,
    schema: type[BaseModel],
    system: str,
    human: str,
) -> Any:
    output = await llm.with_structured_output(schema).ainvoke(
        [SystemMessage(content=system), HumanMessage(content=human)]
    )
    return output if isinstance(output, schema) else schema.model_validate(output)


def facts_block(resume: ParsedResume, limit: int = 80) -> str:
    return "\n".join(
        f"[{fact.fact_id}] ({fact.section}) {fact.text}"
        for fact in resume.facts[:limit]
    )


def final_status(state: AgentState) -> str:
    if state.get("failed"):
        return f"failed:{(state.get('failure') or {}).get('node', 'unknown')}"
    decision = state.get("decision") or {}
    if decision.get("action") == "skip":
        return "deferred" if "daily_cap_reached" in decision.get("reasons", []) else "skipped"
    action = (state.get("approval") or {}).get("action")
    if action == "reject":
        return "rejected"
    if action == "expired":
        return "expired"
    if action == "edit":
        return "revision_limit"
    if state.get("apply_result"):
        return state["apply_result"]["status"]
    return "incomplete"
