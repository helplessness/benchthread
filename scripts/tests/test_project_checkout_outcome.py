from types import SimpleNamespace as NS

import pytest

from tcode_agent.guard.deterministic_verifiers import verify_criterion_deterministic
from tcode_agent.runtime.evidence_ledger import EvidenceLedger
from tcode_agent.task.outcome_planner import OutcomeContractPlanner


@pytest.mark.parametrize("origin,state,expected", [
    ("https://github.com/octocat/Hello-World.git", "completed", "pass"),
    ("https://github.com/another/Hello-World", "completed", "uncertain"),
    (None, "completed", "uncertain"),
    ("https://github.com/octocat/Hello-World", "error", "uncertain"),
])
@pytest.mark.parametrize("prompt", ["帮我从 GitHub 下载 octocat 的 Hello-World 项目。",
                                    "把 https://github.com/octocat/Hello-World 拉到这里，然后运行测试"])
def test_verify_published_checkout(monkeypatch, origin, state, expected, prompt):
    monkeypatch.setenv("TCODE_DESKTOP_MANAGED", "1")
    contract = OutcomeContractPlanner._compile_agent_decision(prompt, minimum_level="V2",
        decision={"published_origin":"https://github.com/octocat/Hello-World",
                  "success_criteria":[prompt], "requires_tool_evidence":True}).contract
    criterion = next(c for c in contract.success_criteria if c.parameters.get("published_origin"))
    assert any(c.description == prompt for c in contract.success_criteria)
    def result(state, metadata):
        call = NS(tool_name="exec", state=state, arguments={}, metadata=metadata)
        return NS(role="tool", content="下载成功", tool_calls=[call])
    ledger = EvidenceLedger.from_messages([
        result("error", {}), result(state, {"sandbox": True, "published_checkout":
            {"origin": origin, "path": "Hello-World"} if origin else None}),
    ])
    assert verify_criterion_deterministic("下载成功", criterion, contract, ledger).status == expected
    assert OutcomeContractPlanner.plan(prompt + "然后运行测试").reason_code != "project_checkout"


@pytest.mark.parametrize("page,code,expected", [
    ("product", "browser_dom_action_failed", "pass"),
    ("different", "browser_dom_action_failed", "fail"),
    ("product", "browser_login_required", "fail"),
])
def test_read_only_browser_recovery(page, code, expected):
    plan = OutcomeContractPlanner._compile_agent_decision("查看商品尺寸", minimum_level="V2",
        decision={"level":"V2", "requires_tool_evidence":True, "browser_read_only":True})
    criterion = plan.contract.success_criteria[-1]
    # Previously stored browser-only contracts still use their original verifier.
    criterion.parameters = {"require_all":True, "browser_read_only":True}
    criterion.verifier_refs = ["tool_success"]
    def message(target, error):
        call = NS(tool_name="browser", state="error" if error else "completed",
                  metadata={"desktop_browser":True, "browser_session_id":"tab", "error_code":error,
                            "url":"https://example.com/" + target}, arguments={"action":"click" if error else "text"})
        return NS(role="tool", content='{"text":"width 22 inches"}' if not error else "", tool_calls=[call])
    ledger = EvidenceLedger.from_messages([message("product",code),message(page,"")])
    assert verify_criterion_deterministic("22 inches",criterion,plan.contract,ledger).status == expected


@pytest.mark.parametrize("states,risk,expected", [
    (["error", "completed"], "low", "uncertain"),  # needs semantic attribution, not a pass
    (["completed", "error"], "low", "uncertain"),
    (["completed", "error"], "medium", "uncertain"),
    (["error"], "low", "fail"),
    (["pending"], "low", "fail"),
    (["error", "completed"], "high", "fail"),
])
def test_generic_request_keeps_proof_boundary(states, risk, expected):
    contract = OutcomeContractPlanner._compile_agent_decision("执行请求并交付结果", minimum_level="V2",
        decision={"requires_tool_evidence": True, "risk_level":risk}).contract
    ledger = EvidenceLedger.from_messages([NS(role="tool", content="observed", tool_calls=[
        NS(tool_name="example", state=status, metadata={}, arguments={})]) for status in states])
    assert verify_criterion_deterministic("完成", contract.success_criteria[-1], contract, ledger).status == expected


def test_conflicting_review_cannot_pass(monkeypatch):
    import asyncio
    from tcode_agent.guard import outcome_guard
    async def judge(*args, **kwargs):
        return NS(content='{"verdict":"pass","confidence":0.99,"unsupported_claims":["结论与回执矛盾"]}')
    monkeypatch.setattr(outcome_guard, "verification_chat", judge)
    contract = OutcomeContractPlanner._compile_agent_decision("核对交付", minimum_level="V2", decision={}).contract
    result = asyncio.run(outcome_guard._semantic_validation("已完成",contract,EvidenceLedger(),provider=object()))
    assert result.verdict == "repair"
