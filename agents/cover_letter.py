from graph.dependencies import AgentNodeContext
from graph.errors import GroundingError
from graph.helpers import current_job, facts_block, numbers, structured
from graph.models import CoverLetterDraft, JobRequirements, ParsedResume
from graph.state import AgentState
from prompts.coverletter import COVER_SYSTEM


class CoverLetterNodes(AgentNodeContext):
    async def cover_letter(self, state: AgentState) -> dict:
        resume = ParsedResume.model_validate(state["resume"])
        requirements = JobRequirements.model_validate(state["requirements"])
        job = current_job(state)
        feedback = state.get("human_feedback")
        human = (
            f"Write to the hiring team at {requirements.company} for the role "
            f"'{requirements.title}'.\n"
            f"Candidate: {resume.name}, {resume.total_years_experience} years experience.\n"
            f"Key requirements: {', '.join(requirements.must_have_skills)}\n"
            f"Responsibilities:\n- " + "\n- ".join(requirements.responsibilities[:8]) +
            f"\n\nFACTS:\n{facts_block(resume)}"
            + (f"\n\nREVIEWER FEEDBACK TO APPLY:\n{feedback}" if feedback else "")
        )
        draft = await structured(
            self.deps.llm_strong, CoverLetterDraft, COVER_SYSTEM, human
        )

        valid_ids = {fact.fact_id for fact in resume.facts}
        if not draft.referenced_fact_ids or not set(draft.referenced_fact_ids) <= valid_ids:
            raise GroundingError("Cover letter cites missing or unknown fact_ids")
        allowed_numbers = numbers(
            " ".join(fact.text for fact in resume.facts)
            + " " + job["jd_text"]
            + f" {int(resume.total_years_experience)}"
        )
        if numbers(draft.body) - allowed_numbers:
            raise GroundingError("Cover letter contains numbers not found in resume or posting")
        words = len(draft.body.split())
        if not 120 <= words <= 450:
            raise GroundingError(f"Cover letter length out of range: {words} words")
        key = await self.deps.renderer.render_cover_letter(
            draft.body, state["resume"], job
        )
        return {
            "cover_letter": {
                "body": draft.body.strip(),
                "fact_ids": draft.referenced_fact_ids,
            },
            "cover_letter_pdf_key": key,
        }