from graph.dependencies import AgentNodeContext
from graph.errors import GroundingError
from graph.helpers import facts_block, norm_skill, numbers, structured
from graph.models import (
    JobRequirements,
    ParsedResume,
    ParsedResumeDraft,
    ResumeFact,
    TailoredResume,
)
from graph.state import AgentState
from prompts.resume import PARSE_SYSTEM, REWRITE_SYSTEM


class ResumeWriterNodes(AgentNodeContext):
    async def parse_resume(self, state: AgentState) -> dict:
        """Parse the resume once per run and reuse its structured facts for later jobs."""
        if state.get("resume"):
            return {}
        text = state.get("resume_text") or await self.deps.resume_loader.load_text(
            state["resume_path"]
        )
        if not text or len(text.strip()) < 100:
            raise ValueError("Resume text is empty or too short to parse")
        draft = await structured(
            self.deps.llm_fast, ParsedResumeDraft, PARSE_SYSTEM, text[:30_000]
        )
        facts = [
            ResumeFact(fact_id=f"{fact.section[:3]}_{index:03d}", **fact.model_dump())
            for index, fact in enumerate(draft.facts)
        ]
        parsed = ParsedResume(**draft.model_dump(exclude={"facts"}), facts=facts)
        if not parsed.facts:
            raise ValueError("No facts extracted from resume")
        return {"resume": parsed.model_dump()}

    async def resume_rewrite(self, state: AgentState) -> dict:
        """Reorder and rephrase existing facts only; reject unsupported claims."""
        resume = ParsedResume.model_validate(state["resume"])
        requirements = JobRequirements.model_validate(state["requirements"])
        facts = {fact.fact_id: fact for fact in resume.facts}
        feedback = state.get("human_feedback")
        human = (
            f"TARGET JOB: {requirements.title} at {requirements.company}\n"
            f"Must-have: {', '.join(requirements.must_have_skills)}\n"
            f"Keywords: {', '.join(requirements.keywords)}\n\n"
            f"CANDIDATE SKILLS: {', '.join(resume.skills)}\n"
            f"FACTS:\n{facts_block(resume)}"
            + (f"\n\nREVIEWER FEEDBACK TO APPLY:\n{feedback}" if feedback else "")
        )
        draft = await structured(
            self.deps.llm_strong, TailoredResume, REWRITE_SYSTEM, human
        )

        bullets = []
        for bullet in draft.bullets:
            source = facts.get(bullet.source_fact_id)
            if not source:
                continue
            invented_numbers = numbers(bullet.text) - numbers(source.text)
            bullets.append(
                {
                    "source_fact_id": source.fact_id,
                    "text": source.text if invented_numbers else bullet.text.strip(),
                }
            )
        if not bullets:
            raise GroundingError(
                "Tailored resume contained no bullets traceable to the master resume"
            )
        allowed_skills = {norm_skill(skill) for skill in resume.skills}
        tailored = {
            "summary": draft.summary.strip(),
            "bullets": bullets,
            "skills": [
                skill for skill in draft.skills if norm_skill(skill) in allowed_skills
            ],
        }
        key = await self.deps.renderer.render_resume(tailored, state["resume"])
        return {
            "tailored_resume": tailored,
            "resume_pdf_key": key,
            "revision_count": state.get("revision_count", 0) + 1,
        }