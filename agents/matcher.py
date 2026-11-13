from graph.dependencies import AgentNodeContext
from graph.helpers import current_job, facts_block, norm_skill, structured
from graph.models import FitAssessment, JobRequirements, ParsedResume
from graph.state import AgentState
from prompts.matcher import MATCH_SYSTEM


class MatcherNodes(AgentNodeContext):
    async def match_score(self, state: AgentState) -> dict:
        """Combine deterministic skill/experience coverage with semantic fit."""
        requirements = JobRequirements.model_validate(state["requirements"])
        resume = ParsedResume.model_validate(state["resume"])
        have = {
            norm_skill(skill)
            for skill in resume.skills
        } | {
            norm_skill(skill)
            for fact in resume.facts
            for skill in fact.skills
        }

        must = [skill for skill in requirements.must_have_skills if norm_skill(skill)]
        nice = [skill for skill in requirements.nice_to_have_skills if norm_skill(skill)]
        matched = [skill for skill in must + nice if norm_skill(skill) in have]
        missing = [skill for skill in must if norm_skill(skill) not in have]
        must_coverage = (len(must) - len(missing)) / len(must) if must else 1.0
        nice_hits = sum(1 for skill in nice if norm_skill(skill) in have)
        skills = (
            0.8 * must_coverage + 0.2 * (nice_hits / len(nice))
            if nice else must_coverage
        )
        experience = (
            min(1.0, resume.total_years_experience / requirements.years_experience_min)
            if requirements.years_experience_min else 1.0
        )

        human = (
            f"JOB: {requirements.title} at {requirements.company}\n"
            f"Must-have: {', '.join(must)}\n"
            f"Responsibilities:\n- " + "\n- ".join(requirements.responsibilities[:12]) +
            f"\n\nCANDIDATE ({resume.total_years_experience} yrs): {resume.headline or ''}\n"
            f"{facts_block(resume)}"
        )
        fit = await structured(self.deps.llm_fast, FitAssessment, MATCH_SYSTEM, human)

        total = 100 * (0.45 * skills + 0.20 * experience + 0.35 * fit.semantic_fit)
        return {
            "match": {
                "total": round(total, 1),
                "skills": round(skills, 3),
                "experience": round(experience, 3),
                "semantic": fit.semantic_fit,
                "matched_skills": matched,
                "missing_must_haves": missing,
                "strengths": fit.strengths,
                "gaps": fit.gaps,
                "rationale": fit.rationale,
            }
        }

    async def decision(self, state: AgentState) -> dict:
        """Apply deterministic, auditable application gates."""
        settings = self.settings
        match, requirements = state["match"], state["requirements"]
        job = current_job(state)
        reasons: list[str] = []
        company = (requirements.get("company") or job["company"]).strip().lower()
        if company in {name.lower() for name in settings.blacklist_companies}:
            reasons.append("blacklisted_company")
        if match["total"] < settings.match_threshold:
            reasons.append(f"score_below_threshold:{match['total']}<{settings.match_threshold}")
        if len(match["missing_must_haves"]) > settings.max_missing_must_haves:
            reasons.append("too_many_missing_must_haves")
        if (
            settings.min_salary
            and requirements.get("salary_max")
            and requirements["salary_max"] < settings.min_salary
        ):
            reasons.append("salary_below_floor")
        if not reasons and await self.deps.repo.applications_today() >= settings.daily_apply_cap:
            reasons.append("daily_cap_reached")
        return {"decision": {"action": "skip" if reasons else "apply", "reasons": reasons}}