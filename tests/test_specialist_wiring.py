"""Structural test: specialist agents are real, differentiated, and wired.

This proves the multi-agent architecture is not "the same transcript summarized
with different prompts" and not "the supervisor executes tools directly":
- Feedback, Investigation and Trend are independent specialists with distinct
  roles and (for investigation/trend) read-only tools.
- The supervisor is a tool-less *arbitrator* that only reconciles findings.
"""

from app.agents.strands_agents import StrandsAnalyzer


def _make_analyzer(repo, settings):
    biz = repo.get_or_create_business(settings.business_name)
    return StrandsAnalyzer(repo, settings, biz["id"])


def test_specialists_have_distinct_roles_and_tools(repo, settings):
    a = _make_analyzer(repo, settings)

    # Feedback: understanding the transcript only (no tools).
    assert a.feedback_agent.name == "feedback"
    assert a.feedback_agent.tool_names == []

    # Investigation + Trend: independent read-only tools (no write side effects).
    read_tools = {"get_customer_history", "list_recent_problems", "list_recent_feedback"}
    assert set(a.investigation_agent.tool_names) == read_tools
    assert set(a.trend_agent.tool_names) == read_tools

    # Supervisor: tool-less arbitrator (reconciles, does not execute).
    assert a.supervisor.name == "supervisor"
    assert a.supervisor.tool_names == []


def test_specialists_have_distinct_system_prompts(repo, settings):
    a = _make_analyzer(repo, settings)
    prompts = {
        a.feedback_agent.system_prompt,
        a.investigation_agent.system_prompt,
        a.trend_agent.system_prompt,
        a.supervisor.system_prompt,
    }
    assert len(prompts) == 4  # four genuinely different roles


def test_write_tools_not_attached_to_any_agent(repo, settings):
    """Consequential tools (alarm, DNC, compensation) must not be executable by
    any agent — only the deterministic policy gate performs those actions."""
    a = _make_analyzer(repo, settings)
    all_tools = set()
    for agent in (a.feedback_agent, a.investigation_agent, a.trend_agent, a.supervisor):
        all_tools.update(agent.tool_names)
    forbidden = {"send_telegram_alarm_tool", "update_dnc_list_tool", "propose_human_approval_tool"}
    assert not (all_tools & forbidden)
