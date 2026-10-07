from typing import List

from aisuite import Client

from .agents import (
    research_agent,
    writer_agent,
    editor_agent,
)
from .plan_logic import (
    DEFAULT_AGENT_MODEL,
    DEFAULT_PLANNER_MODEL,
    HISTORY_LABELS,
    HistoryEntry,
    coerce_plan,
    ensure_contract,
    select_agent,
)

client = Client()

AGENT_FUNCTIONS = {
    "research_agent": research_agent,
    "writer_agent": writer_agent,
    "editor_agent": editor_agent,
}


def planner_agent(topic: str, model: str = DEFAULT_PLANNER_MODEL) -> List[str]:
    prompt = f"""
You are a planning agent responsible for organizing a research workflow using multiple intelligent agents.

🧠 Available agents:
- Research agent: MUST begin with a broad **web search using Tavily** to identify only **relevant**
items (e.g., high-impact venues, seminal works, surveys, or recent comprehensive sources). The output of this step MUST
capture for each candidate: title, authors, year, venue/source, URL, and (if available) DOI.
- Research agent: AFTER the Tavily step, perform a **targeted arXiv search** ONLY for the candidates discovered in the
web step (match by title/author/DOI). If an arXiv preprint/version exists, record its arXiv URL and version info.
Do NOT run a generic arXiv search detached from the Tavily results.
- Writer agent: drafts based on research findings.
- Editor agent: reviews, reflects on, and improves drafts.

🎯 Produce a clear step-by-step research plan **as a valid Python list of strings** (no markdown, no explanations). 
Each step must be atomic, actionable, and assigned to one of the agents.
Maximum of 7 steps.

 ✅ Focus on meaningful research tasks (search, extract, rank, draft, revise).

🔚 The FINAL step MUST instruct the editor agent to generate a comprehensive Markdown report that:
- Uses all findings and outputs from previous steps
- Includes inline citations (e.g., [1], (Wikipedia/arXiv))
- Includes a References section with clickable links for all citations
- Is detailed and self-contained

Topic: "{topic}"
"""

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=1,
    )

    raw = response.choices[0].message.content.strip()
    return ensure_contract(coerce_plan(raw))


def executor_agent_step(
    step_title: str,
    history: List[HistoryEntry],
    prompt: str,
    model: str = DEFAULT_AGENT_MODEL,
):
    """
    Executes a step of the planning agent.
    Returns:
        - step_title (str)
        - agent_name (str)
        - output (str)
    """

    # build structured enriched context
    context = f"📘 User Prompt:\n{prompt}\n\n📜 History so far:\n"
    for i, entry in enumerate(history):
        label = HISTORY_LABELS.get(entry.agent, entry.agent)
        context += f"\n{label} (Step {i + 1}):\n{entry.output.strip()}\n"

    enriched_task = f"""{context}

    Your next task:
    {step_title}
    """

    agent_name = select_agent(step_title)
    if agent_name is None:
        raise ValueError(f"Unknown step type: {step_title}")

    content, _ = AGENT_FUNCTIONS[agent_name](prompt=enriched_task, model=model)
    return step_title, agent_name, content
