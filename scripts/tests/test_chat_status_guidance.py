import asyncio
import time
from contextlib import asynccontextmanager, closing
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tcode_agent.runtime.guidance_store import GuidanceSQLiteStore
from tcode_agent.runtime.agent_backend import NormalizedRunInput
from tcode_agent.runtime.guidance import (
    EXTERNAL_RESTART_CANCELLING, EXTERNAL_RESTART_READY, GuidanceMode, GuidanceSafePoint, GuidanceStaleTurn, OwnerScope,
)
from tcode_agent.runtime.run_coordinator import AgentRunStore, RunConflictError
from tcode_agent.server import _shared, chat_control_api
from tcode_agent.server import auth
from tcode_agent.server.guidance_runtime import GuidanceRuntime
from tcode_agent.server.schemas import ChatGuideRequest
from tcode_agent.session.events import SessionEventStore
from tcode_agent.session.manager import Session, SessionManager
from tcode_agent.session.store_sqlite import SessionSQLiteStore


@pytest.mark.parametrize(("counts", "running", "should_drain"), [
    ((0, 0, 0), False, False),
    ((1, 0, 0), False, False),  # Native steer waits for an active turn.
    ((0, 1, 0), False, True),
    ((1, 0, 1), False, True),  # External Harness restart-turn guidance.
    ((0, 1, 0), True, False),
])
def test_chat_status_only_starts_a_guidance_worker_for_recoverable_work(
    tmp_path, monkeypatch, counts, running, should_drain,
):
    server = import_module("tcode_agent.server.app")
    workdir = str(tmp_path)
    session = Session(id="status-guidance", tenant_id="tenant", user_id="user",
                      workspace_id="workspace", workdir=workdir)
    monkeypatch.setattr(SessionManager, "get", AsyncMock(return_value=session))
    monkeypatch.setattr(server, "_workspace_context_from_request", Mock())
    monkeypatch.setattr(server, "_session_owner_kwargs", lambda _: {})
    monkeypatch.setattr(server, "_guidance_store_workdir", lambda: workdir)
    monkeypatch.setattr(_shared, "_guidance_store_workdir", lambda: workdir)
    monkeypatch.setattr(server.ServerState, "guidance_tasks", {})
    monkeypatch.setattr(server.ServerState, "is_running", lambda _: running)
    monkeypatch.setattr(GuidanceSQLiteStore, "pending_counts", Mock(return_value=dict(zip(
        ("pending_steer_count", "pending_queue_count", "pending_restart_turn_count"), counts,
    ))))

    async def slow_drain(_session_id):
        # Keep the worker alive while model selection inspects the same busy state.
        await asyncio.Event().wait()

    monkeypatch.setattr(_shared, "_drain_session_guidance_v2", slow_drain)

    async def run():
        await server.chat_status(session.id, request=None)
        task = server.ServerState.get_guidance_task(session.id)
        if not any(counts):
            assert not _shared._is_chat_session_busy(session.id)
        assert bool(task) is should_drain

    asyncio.run(run())


def seed_interrupted_steer(workdir, owner, session_id, *, persisted=True, expired=True, live_turn=False,
                           phase="applying"):
    """Leave the same SQLite state as an exit during the chosen guidance phase."""
    store = GuidanceSQLiteStore
    session = Session(id=session_id, workdir=workdir, tenant_id=owner.tenant_id,
                      user_id=owner.user_id, workspace_id=owner.workspace_id)
    SessionSQLiteStore.upsert(vars(session), workdir)
    scope = dict(owner=owner, workdir=workdir)
    store.register_turn_owner(
        session_id=session_id, turn_id="turn-old", owner_id="old-process",
        phase="streaming", lease_seconds=30, **scope,
    )
    item, _ = store.accept(
        session_id=session_id, client_request_id="steer", target_turn_id="turn-old",
        mode=GuidanceMode.STEER, message="已接收的补充说明", file_refs=[],
        output_format=None, outcome_contract=None, apply_policy="next_safe_point",
        descendant_policy="preserve", supersedes_guidance_id="", received_phase="streaming", **scope,
    )
    classified = store.claim_next_classification(owner_id="classifier", lease_seconds=30, **scope)
    assert classified.id == item.id
    item = classified
    if phase != "classifying":
        item = store.complete_classification(
            guidance_id=item.id, claim_token=classified.claim_token, expected_version=classified.version,
            contract_revision={"classification": "REFINEMENT"}, outcome_contract=None, effect="replan", **scope,
        )
    if phase in {"applying", "deferred"}:
        [item] = store.claim_for_turn(
            session_id=session_id, turn_id="turn-old", owner_id="old-process",
            safe_point=GuidanceSafePoint.BEFORE_FINALIZE, **scope,
        )
        if phase == "deferred":
            item = store.mark_deferred(
                guidance_id=item.id, claim_token=item.claim_token, expected_version=item.version,
                reason="question", **scope,
            )
        else:
            [item] = store.mark_applying(items=[item], **scope)
    if persisted:
        SessionSQLiteStore.upsert({**vars(session), "messages": [{
            "id": "durable-guidance", "role": "user", "content": item.message,
            "metadata": {"guidance": {"id": item.id}},
        }]}, workdir)
    with closing(SessionSQLiteStore._connect(workdir)) as conn:
        if expired and item.claim_token:
            conn.execute("UPDATE guidance_items SET claim_lease_expires_at = ? WHERE id = ?",
                         (time.time() - 1, item.id))
        if not live_turn:
            conn.execute("UPDATE guidance_turn_owners SET lease_expires_at = ? WHERE session_id = ?",
                         (time.time() - 1, session_id))
        conn.commit()
    return session, item


@pytest.mark.parametrize(("persisted", "expired", "live_turn", "runtime_running", "recovered"), [
    (True, True, False, False, True),
    (False, True, False, False, True),
    (True, False, False, False, False),
    (True, True, True, False, False),
    (True, True, False, True, False),
])
def test_idle_status_acknowledges_only_expired_guidance_with_a_saved_message(
    tmp_path, monkeypatch, persisted, expired, live_turn, runtime_running, recovered,
):
    server = import_module("tcode_agent.server.app")
    workdir = str(tmp_path)
    owner = OwnerScope("tenant", "user", "workspace")
    store = GuidanceSQLiteStore
    session, applying = seed_interrupted_steer(
        workdir, owner, "crashed", persisted=persisted, expired=expired, live_turn=live_turn,
    )
    peer, peer_item = seed_interrupted_steer(workdir, owner, "other-session")
    monkeypatch.setattr(SessionManager, "get", AsyncMock(return_value=session))
    monkeypatch.setattr(server, "_workspace_context_from_request", Mock())
    monkeypatch.setattr(server, "_session_owner_kwargs", lambda _: {})
    monkeypatch.setattr(server, "_guidance_store_workdir", lambda: workdir)
    monkeypatch.setattr(_shared, "_guidance_store_workdir", lambda: workdir)
    monkeypatch.setattr(server.ServerState, "turn_tasks", {})
    monkeypatch.setattr(server.ServerState, "guidance_tasks", {})
    start_worker = Mock(side_effect=AssertionError("status recovery must not replay guidance"))
    monkeypatch.setattr(server, "_ensure_session_guidance_drain_v2", start_worker)

    async def run():
        if runtime_running:
            monkeypatch.setitem(server.ServerState.turn_tasks, session.id, asyncio.current_task())
        for _ in range(2):
            result = await server.chat_status(session.id, request=None)
            assert result["pending_guidance_count"] == (0 if recovered else 1)
            assert result["chat_running"] is runtime_running
            assert _shared._is_chat_session_busy(session.id) is not recovered

    asyncio.run(run())
    start_worker.assert_not_called()
    [item] = store.list_for_session(session_id=session.id, owner=owner, workdir=workdir)
    assert item.status.value == (("applied" if persisted else "failed") if recovered else "applying")
    assert item.public_projection()["failure_code"] == ("expired_turn_unapplied" if recovered and not persisted else "")
    assert bool(item.applied_at) == (recovered and persisted)
    assert item.version == applying.version + int(recovered)
    [other] = store.list_for_session(session_id=peer.id, owner=owner, workdir=workdir)
    assert other.status.value == "applying" and other.version == peer_item.version
    with closing(SessionSQLiteStore._connect(workdir)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ? AND guidance_id = ?",
            (session.id, item.id),
        ).fetchone()[0] == int(persisted)


@pytest.fixture
def isolated_guidance_status(tmp_path, monkeypatch):
    server = import_module("tcode_agent.server.app")
    workdir = str(tmp_path)
    owner = OwnerScope("tenant", "user", "workspace")
    scope = dict(owner=owner, workdir=workdir)
    session, item = seed_interrupted_steer(workdir, owner, "idle-guidance")
    monkeypatch.setattr(SessionManager, "get", AsyncMock(return_value=session))
    monkeypatch.setattr(server, "_workspace_context_from_request", Mock())
    monkeypatch.setattr(server, "_session_owner_kwargs", lambda _: {})
    monkeypatch.setattr(server, "_guidance_store_workdir", lambda: workdir)
    monkeypatch.setattr(_shared, "_guidance_store_workdir", lambda: workdir)
    for field in ("turn_tasks", "guidance_tasks", "_running", "active_chat_request_ids", "recent_chat_request_ids"):
        monkeypatch.setattr(server.ServerState, field, {})
    return server, session, item, scope


def test_recovery_rechecks_foreground_after_waiting_for_admission(isolated_guidance_status, monkeypatch):
    server, session, item, scope = isolated_guidance_status
    sid = session.id
    async def run():
        lock = _shared._delegated_session_lock(f"goal-control:{sid}")
        waiting = asyncio.Event()

        @asynccontextmanager
        async def observe_lock(key):
            assert key == f"goal-control:{sid}"
            waiting.set()
            async with lock:
                yield

        monkeypatch.setattr(server, "_delegated_session_lock", observe_lock)
        async with lock:
            poll = asyncio.create_task(server.chat_status(sid, None))
            await asyncio.wait_for(waiting.wait(), timeout=3)
            assert server.ServerState.claim_chat_turn(sid, "new-turn", asyncio.current_task()) == "accepted"
        result = await poll
        assert result["chat_running"] and _shared._is_chat_session_busy(sid)
        [unreconciled] = GuidanceSQLiteStore.list_for_session(session_id=sid, **scope)
        assert unreconciled.status.value == "applying" and unreconciled.version == item.version
        server.ServerState.finish_chat_turn(sid, asyncio.current_task())
        assert (await server.chat_status(sid, None))["pending_guidance_count"] == 0

    asyncio.run(run())


def test_idle_recovery_does_not_wait_for_detached_child_or_locked_database(isolated_guidance_status, monkeypatch):
    server, session, _, scope = isolated_guidance_status
    monkeypatch.setattr(_shared, "_descendant_session_active", lambda sid: sid == session.id)

    async def run():
        with closing(SessionSQLiteStore._connect(scope["workdir"])) as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = await server.chat_status(session.id, None)
            assert result["pending_steer_count"] == 1  # Next poll can retry a busy writer.
            conn.rollback()
        result = await server.chat_status(session.id, None)
        assert result["background_running"] and not result["chat_running"]
        assert result["pending_guidance_count"] == 0
        assert not _shared._is_chat_session_busy(session.id)

    asyncio.run(run())


def test_legacy_recovery_keeps_queue_replay_and_leaves_native_steer_to_idle_status(isolated_guidance_status):
    server, session, steer, scope = isolated_guidance_status
    store = GuidanceSQLiteStore
    sid = session.id
    queue, _ = store.accept(
        session_id=sid, client_request_id="queued", target_turn_id="",
        mode=GuidanceMode.QUEUE, message="queued request", file_refs=[],
        output_format=None, outcome_contract=None, apply_policy="next_turn",
        descendant_policy="preserve", supersedes_guidance_id="", received_phase="streaming", **scope,
    )
    claimed = store.claim_next_queue(
        session_id=sid, new_turn_id="queued-turn", owner_id="old-process", lease_seconds=10, **scope,
    )
    assert claimed.id == queue.id
    with closing(SessionSQLiteStore._connect(scope["workdir"])) as conn:
        conn.execute("UPDATE guidance_items SET claim_lease_expires_at = ? WHERE id = ?", (time.time() - 1, queue.id))
    [retried] = store.reconcile_expired_claims(**scope)
    assert retried.id == queue.id and retried.status.value == "pending" and retried.target_turn_id == ""
    [untouched] = store.list_for_session(session_id=sid, statuses=["applying"], **scope)
    assert untouched.id == steer.id and untouched.version == steer.version
    store.cancel_pending(guidance_id=queue.id, expected_version=retried.version, session_id=sid, **scope)
    # The old implementation could already have put a native steer back into pending.
    with closing(SessionSQLiteStore._connect(scope["workdir"])) as conn:
        conn.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
        conn.execute(
            "UPDATE guidance_items SET status = 'pending', claim_token = '', claim_lease_expires_at = NULL WHERE id = ?",
            (steer.id,),
        )
    assert asyncio.run(server.chat_status(sid, None))["pending_guidance_count"] == 0
    [failed] = store.list_for_session(session_id=sid, statuses=["failed"], **scope)
    assert failed.id == steer.id and failed.failure_code == "expired_turn_unapplied"


@pytest.mark.parametrize(("phase", "turn_evidence", "expired_claim", "persisted", "final_status"), [
    ("classifying", "expired", True, False, "failed"),
    ("classifying", "expired", False, False, "classifying"),
    ("pending", "expired", True, False, "failed"),
    ("deferred", "expired", True, False, "failed"),
    ("pending", "fenced", True, False, "failed"),
    ("pending", "last_turn", True, False, "failed"),
    ("pending", "live", True, False, "pending"),
    ("pending", "absent", True, False, "pending"),
    ("pending", "other_turn", True, False, "pending"),
    ("applying", "absent", True, True, "applying"),
    ("deferred", "expired", True, True, "applied"),
])
def test_early_guidance_recovery_requires_proof_of_the_exact_old_turn(
    isolated_guidance_status, monkeypatch, phase, turn_evidence, expired_claim, persisted, final_status,
):
    server, _, _, scope = isolated_guidance_status
    store = GuidanceSQLiteStore
    # Give this fixture a clean classification scope; the helper's original row is already applying.
    sid = "interrupted-early"
    session, item = seed_interrupted_steer(
        scope["workdir"], scope["owner"], sid, phase=phase, expired=expired_claim,
        persisted=persisted, live_turn=turn_evidence in {"live", "fenced"},
    )
    monkeypatch.setattr(SessionManager, "get", AsyncMock(return_value=session))
    verify_no_replay = Mock(side_effect=AssertionError("status recovery must not launch a worker"))
    monkeypatch.setattr(server, "_ensure_session_guidance_drain_v2", verify_no_replay)
    with closing(SessionSQLiteStore._connect(scope["workdir"])) as conn:
        if turn_evidence in {"absent", "last_turn", "other_turn"}:
            conn.execute("DELETE FROM guidance_turn_owners WHERE session_id = ?", (sid,))
        if turn_evidence == "fenced":
            conn.execute("UPDATE guidance_turn_owners SET completion_fenced = 1 WHERE session_id = ?", (sid,))
        if turn_evidence in {"last_turn", "other_turn"}:
            conn.execute(
                "UPDATE sessions SET metadata_json = ? WHERE id = ?",
                ('{"last_turn":{"state":"interrupted","turn_id":"%s"}}' %
                 ("turn-old" if turn_evidence == "last_turn" else "different-turn"), sid),
            )
    if phase == "classifying" and expired_claim:
        # The existing classifier may wake for unrelated guidance; it must not take this one.
        assert store.claim_next_classification(owner_id="other-classifier", lease_seconds=30, **scope) is None
    terminal = final_status in {"applied", "failed"}
    for _ in range(2):
        status = asyncio.run(server.chat_status(sid, None))
        assert status["pending_guidance_count"] == int(not terminal)
    [resolved] = store.list_for_session(session_id=sid, **scope)
    assert resolved.status.value == final_status and resolved.version == item.version + int(terminal)
    assert resolved.failure_code == ("expired_turn_unapplied" if final_status == "failed" else "")
    assert resolved.target_turn_id == "turn-old"
    verify_no_replay.assert_not_called()
    with closing(SessionSQLiteStore._connect(scope["workdir"])) as conn:
        count = conn.execute("SELECT COUNT(*) FROM messages WHERE session_id = ?", (sid,)).fetchone()[0]
    assert count == int(persisted)


def test_classifier_finishing_after_the_turn_changes_cannot_publish_a_cancel(isolated_guidance_status):
    _, _, _, scope = isolated_guidance_status
    sid = "late-classification"
    _, item = seed_interrupted_steer(
        scope["workdir"], scope["owner"], sid, phase="classifying",
        expired=False, live_turn=True, persisted=False,
    )
    # Let the real classifier loop take this initially unclaimed record.
    with closing(SessionSQLiteStore._connect(scope["workdir"])) as conn:
        conn.execute(
            "UPDATE guidance_items SET claim_token='', claim_lease_expires_at=NULL WHERE id=?", (item.id,),
        )

    async def classify(_item):
        with closing(SessionSQLiteStore._connect(scope["workdir"])) as conn:
            conn.execute("DELETE FROM guidance_turn_owners WHERE session_id=?", (sid,))
        GuidanceSQLiteStore.register_turn_owner(
            session_id=sid, turn_id="new-turn", owner_id="new-run", phase="preparing", lease_seconds=30, **scope,
        )
        return {"classification": "CANCEL"}, None, "turn_cancel_requested"

    interrupt = AsyncMock()
    descendants = AsyncMock()
    asyncio.run(GuidanceRuntime()._classification_loop(
        classifier=classify, cancel_turn=interrupt, post_classify=descendants, **scope,
    ))
    interrupt.assert_not_awaited()
    descendants.assert_not_awaited()
    [failed] = GuidanceSQLiteStore.list_for_session(session_id=sid, **scope)
    assert failed.status.value == "failed" and failed.failure_code == "expired_turn_unapplied"
    assert failed.target_turn_id == "turn-old"


def test_cancel_callback_checks_target_again_after_awaiting_descendants(monkeypatch):
    sid = "active-guidance"
    item = SimpleNamespace(
        session_id=sid, target_turn_id="turn-old",
        runtime_id="tcode-native", descendant_policy="cancel_all",
    )
    engine = SimpleNamespace(_active_guidance_turn_id="turn-old")
    monkeypatch.setattr(chat_control_api.ServerState, "agents", {sid: SimpleNamespace(_engine=engine)})
    monkeypatch.setattr(chat_control_api.ServerState, "get_turn_task", lambda _: asyncio.current_task())
    cancel = AsyncMock()
    monkeypatch.setattr(chat_control_api.ServerState, "cancel_session", cancel)
    cancel_worker = Mock(return_value=False)
    monkeypatch.setattr(_shared, "cancel_worker_task", cancel_worker)
    monkeypatch.setattr(_shared, "cancel_team_background_tasks", AsyncMock(return_value={}))

    async def broken_interleaving(*_args, **_kwargs):
        await asyncio.sleep(0)
        engine._active_guidance_turn_id = "new-turn"
        return [SimpleNamespace(id="new-child")]

    monkeypatch.setattr(_shared, "_list_descendant_sessions_recursive", broken_interleaving)
    with pytest.raises(GuidanceStaleTurn):
        asyncio.run(chat_control_api._cancel_classified_guidance(item, session_owner={}))
    cancel.assert_not_awaited()
    cancel_worker.assert_not_called()

    engine._active_guidance_turn_id = "turn-old"
    monkeypatch.setattr(_shared, "_list_descendant_sessions_recursive", AsyncMock(return_value=[]))
    asyncio.run(chat_control_api._cancel_classified_guidance(item, session_owner={}))
    cancel.assert_awaited_once_with(sid, clear_guidance=False)


@pytest.mark.parametrize("replace", [False, True])
def test_guidance_cancels_only_the_snapshotted_child_task(monkeypatch, replace):
    parent = "review-parent"
    child = Session(id="review-child", parent_id=parent)
    state = _shared.ServerState
    tasks = {}
    monkeypatch.setattr(state, "turn_tasks", tasks)
    monkeypatch.setattr(state, "guidance_tasks", {})
    monkeypatch.setattr(state, "agents", {})
    monkeypatch.setattr(state, "sessions", {})
    monkeypatch.setattr(_shared, "_list_descendant_sessions_recursive", AsyncMock(return_value=[child]))
    monkeypatch.setattr(_shared, "cancel_worker_task", Mock(return_value=False))

    async def run():
        old = asyncio.create_task(asyncio.Event().wait())
        new = asyncio.create_task(asyncio.Event().wait())
        tasks[child.id] = old

        async def cancel_team(team_id, **_kwargs):
            if team_id == child.id and replace:
                await asyncio.sleep(0)
                tasks[child.id] = new
            return {}

        monkeypatch.setattr(_shared, "cancel_team_background_tasks", cancel_team)
        try:
            cancel = _shared._cancel_runtime_subtree_for_interrupt_guidance(
                parent, clear_guidance=False, ensure_current=lambda: None,
            )
            if replace:
                with pytest.raises(GuidanceStaleTurn):
                    await cancel
            else:
                await cancel
            await asyncio.sleep(0)
            assert not new.cancelled()
            assert old.cancelled() == (not replace)
        finally:
            old.cancel()
            new.cancel()
            await asyncio.gather(old, new, return_exceptions=True)

    asyncio.run(run())


def test_guidance_team_cleanup_preserves_unlisted_and_replaced_workers(monkeypatch):
    from tcode_agent.team import coordinator

    child = Session(id="worker", parent_id="team")
    workers = [SimpleNamespace(session_id=sid) for sid in ("unlisted", child.id)]
    monkeypatch.setattr(coordinator.WorkerRegistry, "list_for_team", lambda _: workers)
    cancel = Mock()
    monkeypatch.setattr(coordinator, "cancel_worker_task", cancel)
    monkeypatch.setattr(_shared.ServerState, "turn_tasks", {})
    active = {}
    monkeypatch.setattr(coordinator, "_active_tasks", active)
    turns = {child.id: _shared._guidance_child_turn(child)}

    async def run():
        active[child.id] = asyncio.current_task()
        with pytest.raises(GuidanceStaleTurn):
            await coordinator.cancel_team_background_tasks(
                "team", worker_in_scope=lambda wid: _shared._guidance_worker_in_scope(wid, {child.id: child}, turns),
            )
        cancel.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize("changed", [
    "parent", "delegation", "child_turn", "new_chat_task", "new_worker_task", "same_turn_finished",
])
def test_child_metadata_write_revalidates_inside_the_mutation(monkeypatch, changed):
    item = SimpleNamespace(id="guidance-old", session_id="parent", runtime_id="tcode-native", target_turn_id="old")
    engine = SimpleNamespace(_active_guidance_turn_id="old")
    child = Session(id="child", parent_id=item.session_id, metadata={
        "delegation_scope": {"parent_turn_id": "old"}, "existing": "preserved",
        "active_turn": {"turn_id": "child-old"},
    })
    monkeypatch.setattr(chat_control_api.ServerState, "agents", {item.session_id: SimpleNamespace(_engine=engine)})
    tasks = {}
    monkeypatch.setattr(chat_control_api.ServerState, "turn_tasks", tasks)
    worker_tasks = {}
    monkeypatch.setattr("tcode_agent.team.coordinator._active_tasks", worker_tasks)

    async def mutate(_child_id, update, **_scope):
        await asyncio.sleep(0)  # Let the next turn arrive while the mutation waits for the session lock.
        if changed == "parent":
            engine._active_guidance_turn_id = "new"
        elif changed == "delegation":
            child.metadata["delegation_scope"]["parent_turn_id"] = "new"
        elif changed == "child_turn":
            child.metadata["active_turn"] = {"turn_id": "child-new"}
        elif changed == "new_chat_task":
            tasks[child.id] = asyncio.current_task()
        elif changed == "new_worker_task":
            worker_tasks[child.id] = asyncio.current_task()
        else:
            child.metadata["last_turn"] = child.metadata.pop("active_turn")
            tasks.pop(child.id)
        update(child)

    monkeypatch.setattr(SessionManager, "mutate", mutate)

    async def write():
        old_child_task = asyncio.create_task(asyncio.Event().wait())
        tasks.update({item.session_id: asyncio.current_task(), child.id: old_child_task})
        try:
            await chat_control_api._mark_guidance_child_superseded(
                item, child.id, expected_turn=chat_control_api._guidance_child_turn(child), session_owner={},
            )
        finally:
            old_child_task.cancel()
            await asyncio.gather(old_child_task, return_exceptions=True)

    if changed == "same_turn_finished":
        asyncio.run(write())
        assert child.metadata["superseded_by_guidance_id"] == item.id
    else:
        with pytest.raises(GuidanceStaleTurn):
            asyncio.run(write())
        assert "superseded_context" not in child.metadata
    assert child.metadata["existing"] == "preserved"


@pytest.mark.parametrize("active", ["source", "replacement", None])
def test_external_cancel_never_falls_back_to_the_current_session(active, monkeypatch):
    item = SimpleNamespace(
        runtime_id="external-test", tenant_id="tenant", user_id="user",
        session_id="session", source_run_id="source", descendant_policy="cancel_all",
    )
    coordinator = chat_control_api.run_coordinator
    monkeypatch.setattr(coordinator.store, "active_for_session", Mock(return_value={"run_id": active} if active else None))
    cancel = AsyncMock(return_value=True)
    monkeypatch.setattr(coordinator, "cancel", cancel)
    local_cancel = AsyncMock()
    subtree = AsyncMock()
    monkeypatch.setattr(chat_control_api.ServerState, "cancel_session", local_cancel)
    monkeypatch.setattr(chat_control_api, "_cancel_runtime_subtree_for_interrupt_guidance", subtree)
    action = chat_control_api._cancel_classified_guidance(item, session_owner={})
    if active == "source":
        asyncio.run(action)
        cancel.assert_awaited_once_with(owner_key="tenant:user", session_id="session", run_id="source")
    else:
        with pytest.raises(GuidanceStaleTurn):
            asyncio.run(action)
        cancel.assert_not_awaited()
    local_cancel.assert_not_awaited()
    subtree.assert_not_awaited()


@pytest.mark.parametrize("outcome", ["accepted", "refused", "withdrawn"])
def test_external_classification_does_not_publish_a_restart_before_exact_cancel_confirmation(
    isolated_guidance_status, monkeypatch, outcome,
):
    _, session, _, scope = isolated_guidance_status
    sid = session.id
    coordinator = chat_control_api.run_coordinator
    active = {"run_id": "source"}
    monkeypatch.setattr(coordinator.store, "active_for_session", lambda *_: active or None)
    monkeypatch.setattr(chat_control_api, "_guidance_store_workdir", lambda: scope["workdir"])
    monkeypatch.setattr(chat_control_api.harness_api.harness_handoff_store, "get_state",
                        Mock(return_value={"current_runtime_id": "external-test"}))
    monkeypatch.setattr(chat_control_api, "guidance_mode_for_runtime", AsyncMock(return_value="restart_turn"))
    monkeypatch.setattr(chat_control_api, "_list_descendant_sessions_recursive", AsyncMock(return_value=[]))
    monkeypatch.setattr(auth, "session_result_verification_settings", AsyncMock(return_value={"enabled": False}))
    async def plan(*_args, **_kwargs):
        if outcome == "withdrawn":
            [classifying] = GuidanceSQLiteStore.list_for_session(
                session_id=sid, statuses=["classifying"], **scope,
            )
            GuidanceSQLiteStore.cancel_pending(
                session_id=sid, guidance_id=classifying.id, expected_version=classifying.version, **scope,
            )
        return SimpleNamespace(
            classification="REFINEMENT", needs_user_confirmation=False, can_apply=False, revised_contract=None,
            to_dict=lambda **_: {"classification": "REFINEMENT"},
        )

    monkeypatch.setattr(chat_control_api.OutcomeContractRevisionPlanner, "plan", plan)
    schedule, drain = Mock(), Mock()
    monkeypatch.setattr(chat_control_api.guidance_runtime, "ensure_workdir", Mock())
    monkeypatch.setattr(chat_control_api.guidance_runtime, "schedule_classification", schedule)
    monkeypatch.setattr(chat_control_api, "_ensure_session_guidance_drain_v2", drain)

    async def cancel(**kwargs):
        assert kwargs["run_id"] == "source"
        assert GuidanceSQLiteStore.list_for_session(session_id=sid, **scope)[-1].effect == EXTERNAL_RESTART_CANCELLING
        assert GuidanceSQLiteStore.pending_counts(session_id=sid, **scope)["pending_restart_turn_count"] == 0
        assert GuidanceSQLiteStore.claim_next_restart_turn(
            session_id=sid, new_turn_id="premature", owner_id="worker", **scope,
        ) is None
        active.clear()
        return outcome == "accepted"

    cancel_call = AsyncMock(side_effect=cancel)
    monkeypatch.setattr(coordinator, "cancel", cancel_call)

    async def run():
        response = await chat_control_api._guide_chat_v2(
            payload=ChatGuideRequest(session_id=sid, message="fixture", mode="steer"),
            message="fixture", persisted=session, runtime=None, session_owner=vars(scope["owner"]),
        )
        await GuidanceRuntime()._classification_loop(**schedule.call_args.kwargs)
        return response["guidance_id"]

    guidance_id = asyncio.run(run())
    [updated] = [item for item in GuidanceSQLiteStore.list_for_session(session_id=sid, **scope) if item.id == guidance_id]
    if outcome == "accepted":
        assert updated.status.value == "pending" and updated.effect == EXTERNAL_RESTART_READY
        drain.assert_called_once_with(sid)
    elif outcome == "withdrawn":
        assert updated.status.value == "cancelled"
        cancel_call.assert_not_awaited()
        drain.assert_not_called()
    else:
        assert updated.status.value == "failed" and updated.failure_code == "expired_turn_unapplied"
        drain.assert_not_called()


@pytest.mark.parametrize("replacement", [False, True])
def test_external_restart_requires_the_cancelled_source_to_still_be_latest(tmp_path, replacement):
    store = AgentRunStore(tmp_path / "runs.sqlite")
    owner_key = "tenant:user"
    sid = "session"

    def reserve(run_id, parent=""):
        return store.reserve(
            owner_key=owner_key, workspace_id="workspace",
            run_input=NormalizedRunInput(thread_id=sid, run_id=run_id, runtime_id="external-test", messages=[]),
            request_digest=run_id, client_request_id=run_id, guidance_source_run_id=parent,
        )

    reserve("source")
    store.update(owner_key, sid, "source", status="cancelled", last_sequence=1)
    if replacement:
        reserve("newer")
        store.update(owner_key, sid, "newer", status="completed", last_sequence=1)
    else:
        assert reserve("guided", "source")[0] == "new"
        store.update(owner_key, sid, "guided", status="completed", last_sequence=1)
    # Also reject a repeated restart after its own first run has finished.
    with pytest.raises(RunConflictError) as error:
        reserve("guided", "source")
    assert error.value.code == "guidance_source_run_stale"


@pytest.mark.parametrize("state", ["legacy", "interrupted", "confirmed"])
def test_external_guidance_worker_only_dispatches_a_confirmed_restart(
    isolated_guidance_status, monkeypatch, state,
):
    _, session, item, scope = isolated_guidance_status
    sid = session.id
    with closing(SessionSQLiteStore._connect(scope["workdir"])) as conn:
        conn.execute("UPDATE guidance_items SET runtime_id='external-test', source_run_id='source' WHERE id=?", (item.id,))
        if state == "interrupted":
            conn.execute("UPDATE guidance_items SET effect=? WHERE id=?", (EXTERNAL_RESTART_CANCELLING, item.id))
            conn.execute("DELETE FROM messages WHERE session_id=? AND guidance_id=?", (sid, item.id))
        else:
            conn.execute(
                "UPDATE guidance_items SET status='pending', effect=?, claim_token='', claim_lease_expires_at=NULL WHERE id=?",
                (EXTERNAL_RESTART_READY if state == "confirmed" else "contract_revised", item.id),
            )
    monkeypatch.setattr(_shared.ServerState, "session_locks", {})
    monkeypatch.setattr(chat_control_api.guidance_runtime, "ensure_workdir", Mock())
    dispatch = AsyncMock(return_value={"status": "completed"})
    monkeypatch.setattr(_shared, "_run_session_turn_background", dispatch)
    monkeypatch.setattr(_shared, "_guidance_message_persisted", AsyncMock(return_value=state == "confirmed"))
    asyncio.run(_shared._drain_session_guidance_v2(sid))
    [updated] = GuidanceSQLiteStore.list_for_session(session_id=sid, **scope)
    if state == "confirmed":
        assert dispatch.await_args.kwargs["guidance_source_run_id"] == "source"
        assert updated.status.value == "applied"
    else:
        dispatch.assert_not_awaited()
        assert updated.status.value == "failed" and updated.failure_code == "expired_turn_unapplied"


@pytest.mark.parametrize(("archive", "terminal", "expected"), [
    (False, True, "failed"),
    (True, True, "failed"),
    (False, False, "pending"),
])
def test_older_turn_history_is_used_when_the_latest_turn_has_changed(
    isolated_guidance_status, monkeypatch, archive, terminal, expected,
):
    server, _, _, scope = isolated_guidance_status
    sid = "historical-guidance"
    session, item = seed_interrupted_steer(
        scope["workdir"], scope["owner"], sid, phase="pending", persisted=False,
    )
    monkeypatch.setattr(SessionManager, "get", AsyncMock(return_value=session))
    no_replay = Mock()
    monkeypatch.setattr(server, "_ensure_session_guidance_drain_v2", no_replay)
    owner = {key: getattr(session, key) for key in ("tenant_id", "user_id", "workspace_id")}
    old = {"turn_id": item.target_turn_id, "state": "interrupted" if terminal else "streaming"}
    new = {"turn_id": "new-turn", "state": "completed"}
    for evidence, ended in ((old, terminal), (new, True)):
        SessionEventStore.append(
            session_id=sid, event_type="turn.state", payload=evidence,
            metadata={"terminal": ended}, owner=owner, workdir=scope["workdir"],
        )
    if archive:
        SessionEventStore.compact(sid, snapshot=vars(session), owner=owner, workdir=scope["workdir"])
        assert all(event["event_type"] != "turn.state" for event in SessionEventStore.list(sid, workdir=scope["workdir"]))
    session.metadata = {"last_turn": new}
    SessionSQLiteStore.upsert(vars(session), scope["workdir"])
    with closing(SessionSQLiteStore._connect(scope["workdir"])) as conn:
        conn.execute("DELETE FROM guidance_turn_owners WHERE session_id=?", (sid,))
    status = asyncio.run(server.chat_status(sid, None))
    [result] = GuidanceSQLiteStore.list_for_session(session_id=sid, **scope)
    assert result.status.value == expected and status["pending_steer_count"] == int(not terminal)
    assert result.failure_code == ("expired_turn_unapplied" if terminal else "")
    no_replay.assert_not_called()


@pytest.mark.parametrize("live_count", [1, 501])
def test_guidance_list_keeps_active_older_items_and_shows_recent_failure_after_500(
    isolated_guidance_status, monkeypatch, live_count,
):
    _, session, item, scope = isolated_guidance_status
    monkeypatch.setattr(chat_control_api, "_workspace_context_from_request", Mock())
    monkeypatch.setattr(chat_control_api, "_session_owner_kwargs", lambda _: {})
    monkeypatch.setattr(chat_control_api, "_guidance_store_workdir", lambda: scope["workdir"])
    now = time.time()
    with closing(SessionSQLiteStore._connect(scope["workdir"])) as conn:
        conn.execute(
            "UPDATE guidance_items SET sequence=?, status='failed', failure_code='expired_turn_unapplied' WHERE id=?",
            (live_count + 501, item.id),
        )
        conn.executemany(
            """INSERT INTO guidance_items(id,session_id,tenant_id,user_id,workspace_id,client_request_id,
               sequence,mode,status,message,created_at,updated_at)
               VALUES(?, ?, 'tenant', 'user', 'workspace', ?, ?, 'queue', ?, 'fixture', ?, ?)""",
            [(f"history-{i}", session.id, f"request-{i}", i, "pending" if i <= live_count else "applied", now, now)
             for i in range(1, live_count + 501)],
        )
    listing = asyncio.run(chat_control_api.chat_guidance_queue(session.id, None))
    assert len(listing["items"]) == 500 + live_count
    assert [row["id"] for row in listing["items"] if row["status"] == "pending"] == [
        f"history-{i}" for i in range(1, live_count + 1)
    ]
    assert listing["pending_queue_count"] == live_count
    assert listing["items"][-1]["id"] == item.id
    assert listing["items"][-1]["failure_code"] == "expired_turn_unapplied"
    after_cancel = asyncio.run(chat_control_api.delete_chat_guidance(session.id, "history-1", None))
    assert after_cancel["pending_queue_count"] == live_count - 1 and after_cancel["items"][-1]["id"] == item.id
