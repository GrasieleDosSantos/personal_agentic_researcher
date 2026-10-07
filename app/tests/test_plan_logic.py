from src.plan_logic import (
    FALLBACK_PLAN,
    FINAL_REQUIRED,
    REQUIRED_FIRST,
    REQUIRED_SECOND,
    coerce_plan,
    ensure_contract,
    select_agent,
)


# === select_agent ===
def test_prefix_wins_over_keywords():
    assert select_agent("Writer agent: Draft a summary of the research") == "writer_agent"
    assert select_agent("Editor agent: review the research findings") == "editor_agent"
    assert select_agent("research agent: write down sources") == "research_agent"


def test_keyword_fallback_without_prefix():
    assert select_agent("Revise the draft based on research gaps") == "editor_agent"
    assert select_agent("Draft an outline from the research") == "writer_agent"
    assert select_agent("Search arXiv for related papers") == "research_agent"


def test_unroutable_step_returns_none():
    assert select_agent("Do something unrelated") is None


def test_contract_steps_are_routable():
    assert select_agent(REQUIRED_FIRST) == "research_agent"
    assert select_agent(REQUIRED_SECOND) == "research_agent"
    assert select_agent(FINAL_REQUIRED) == "editor_agent"


# === ensure_contract ===
def test_contract_order_enforced():
    steps = ensure_contract(
        [
            "Writer agent: Draft the report.",
            "Editor agent: Review the draft.",
        ]
    )
    assert steps[0] == REQUIRED_FIRST
    assert steps[1] == REQUIRED_SECOND
    assert steps[-1] == FINAL_REQUIRED
    assert "Writer agent: Draft the report." in steps


def test_unroutable_steps_dropped():
    steps = ensure_contract(["Do something unrelated", "Writer agent: Draft it."])
    assert "Do something unrelated" not in steps
    assert "Writer agent: Draft it." in steps


def test_empty_plan_uses_fallback():
    assert ensure_contract([]) == FALLBACK_PLAN
    assert ensure_contract(["Do something unrelated"]) == FALLBACK_PLAN


def test_generic_arxiv_step_removed():
    steps = ensure_contract(["Research agent: Run a generic arXiv search on the topic."])
    assert "Research agent: Run a generic arXiv search on the topic." not in steps


# === coerce_plan ===
def test_coerce_json_list():
    assert coerce_plan('["a", "b"]') == ["a", "b"]


def test_coerce_python_literal_list():
    assert coerce_plan("['a', 'b']") == ["a", "b"]


def test_coerce_fenced_block():
    assert coerce_plan('```json\n["a", "b"]\n```') == ["a", "b"]
    assert coerce_plan("```python\n['a', 'b']\n```") == ["a", "b"]


def test_coerce_truncates_to_seven():
    assert len(coerce_plan(str([f"s{i}" for i in range(10)]))) == 7


def test_coerce_garbage_returns_empty():
    assert coerce_plan("not a list") == []
    assert coerce_plan('{"a": 1}') == []
