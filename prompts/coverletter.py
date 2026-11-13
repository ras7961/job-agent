from prompts.job_search import UNTRUSTED


COVER_SYSTEM = (
    "Write a concise, specific cover letter (150-350 words, no placeholders). Use only facts "
    "provided and cite the fact_ids you relied on in referenced_fact_ids. Do not invent numbers, "
    "employers or achievements. Plain, confident tone; no clichés. " + UNTRUSTED
)