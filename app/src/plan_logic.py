import ast
import json
import re
from typing import List, NamedTuple, Optional

# === Model defaults ===
DEFAULT_PLANNER_MODEL = "openai:o4-mini"
DEFAULT_AGENT_MODEL = "openai:gpt-4.1-mini"

# === Agents ===
AGENTS = ("research_agent", "writer_agent", "editor_agent")

HISTORY_LABELS = {
    "research_agent": "🔍 Research",
    "writer_agent": "✍️ Draft",
    "editor_agent": "🧠 Feedback",
}


class HistoryEntry(NamedTuple):
    title: str
    agent: str
    output: str


_PREFIX_RE = re.compile(r"^\s*(research|writer|editor)\s+agent\b", re.IGNORECASE)

# Fallback keywords, checked in this order (editor/writer before research, so a
# step that merely mentions "research" is not routed to the research agent).
_KEYWORD_RULES = [
    ("editor_agent", re.compile(r"\b(edit\w*|revis\w*|review\w*|feedback|polish\w*)\b", re.IGNORECASE)),
    ("writer_agent", re.compile(r"\b(draft\w*|writ\w*|outline\w*)\b", re.IGNORECASE)),
    ("research_agent", re.compile(r"\b(research\w*|search\w*|collect\w*|find\w*)\b", re.IGNORECASE)),
]


def select_agent(step: str) -> Optional[str]:
    """Return the agent name for a plan step, or None if it cannot be routed."""
    match = _PREFIX_RE.match(step)
    if match:
        return f"{match.group(1).lower()}_agent"
    for agent, pattern in _KEYWORD_RULES:
        if pattern.search(step):
            return agent
    return None


# === Plan parsing ===
def clean_json_block(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    return raw.strip("` \n")


def coerce_plan(raw: str) -> List[str]:
    """Parse the planner output into a list of steps: JSON -> ast -> []."""
    s = clean_json_block(raw)
    # try strict JSON
    try:
        obj = json.loads(s)
        if isinstance(obj, list) and all(isinstance(x, str) for x in obj):
            return obj[:7]
    except json.JSONDecodeError:
        pass
    # try Python literal list
    try:
        obj = ast.literal_eval(s)
        if isinstance(obj, list) and all(isinstance(x, str) for x in obj):
            return obj[:7]
    except Exception:
        pass
    return []


# === Plan contract ===
REQUIRED_FIRST = """Research agent: Use Tavily to perform a broad web search and collect top relevant items
        (title, authors, year, venue/source, URL, DOI if available)."""
REQUIRED_SECOND = """Research agent: For each collected item, search on arXiv to find matching preprints/versions
        and record arXiv URLs (if they exist)."""
FINAL_REQUIRED = """Editor agent: Generate the final comprehensive Markdown report with inline citations and a
    complete References section with clickable links."""

FALLBACK_PLAN = [
    REQUIRED_FIRST,
    REQUIRED_SECOND,
    "Research agent: Synthesize and rank findings by relevance, recency, authority; deduplicate by title/DOI.",
    "Writer agent: Draft a structured outline based on the ranked evidence.",
    "Editor agent: Review for coherence, coverage, and citation completeness; request fixes.",
    FINAL_REQUIRED,
]


def ensure_contract(steps_list: List[str]) -> List[str]:
    """Enforce step ordering and drop steps that no agent can execute."""
    routable = []
    for s in steps_list:
        if not isinstance(s, str):
            continue
        if select_agent(s) is None:
            print(f"⚠️ Dropping unroutable plan step: {s}")
            continue
        routable.append(s)
    steps_list = routable

    if not steps_list:
        return list(FALLBACK_PLAN)
    # inject/replace first two if missing or out of order
    if steps_list[0] != REQUIRED_FIRST:
        steps_list = [REQUIRED_FIRST] + steps_list
    if len(steps_list) < 2 or steps_list[1] != REQUIRED_SECOND:
        # remove any generic arxiv step that is not tied to Tavily results
        steps_list = (
            [steps_list[0]]
            + [REQUIRED_SECOND]
            + [
                s
                for s in steps_list[1:]
                if "arXiv" not in s or "For each collected item" in s
            ]
        )
    # ensure final step requirement present
    if FINAL_REQUIRED not in steps_list:
        steps_list.append(FINAL_REQUIRED)
    return steps_list
