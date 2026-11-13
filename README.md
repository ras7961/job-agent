job-agent/
├── agents/
│   ├── cover_letter.py       # Cover-letter generation and grounding checks
│   ├── job_search.py         # Search, scrape coordination, and job analysis
│   ├── matcher.py            # Candidate/job scoring and application decisions
│   ├── resume_writer.py      # Resume parsing and tailored resume generation
│   └── scraper.py            # Playwright scraper implementation
├── data/
│   └── resume.py
├── db/
│   └── mongodb.py
├── graph/
│   ├── dependencies.py       # Settings and injectable integration contracts
│   ├── errors.py             # Retries, error classification, and node wrapper
│   ├── helpers.py            # Shared graph and LLM helpers
│   ├── models.py             # Validated job, resume, and output schemas
│   ├── state.py              # LangGraph state and initial-state factory
│   └── workflow.py           # Node composition, routing, graph, and runner
├── prompts/
│   ├── coverletter.py
│   ├── job_search.py
│   ├── matcher.py
│   └── resume.py
├── app.py
├── job_agent_graph.py        # Backwards-compatible imports
├── main.py
├── requirements.txt
└── .env

The workflow implementation is split by responsibility across `agents/`, `graph/`,
and `prompts/`. Existing imports from `job_agent_graph` remain available through
the compatibility module.
