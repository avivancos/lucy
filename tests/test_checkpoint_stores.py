import os

from lucy.checkpoint.postgres import PostgresCheckpointStore
from lucy.checkpoint.redis import RedisCheckpointStore
from lucy.clock import ManualClock
from lucy.drivers import CascadedTurnDriver, GraphTurnDriver
from lucy.evals import SyntheticCallScenario, SyntheticTurn
from lucy.graph import default_agent_graph
from lucy.harness import ConversationHarness
from lucy.llm import LocalLlmSimulator, ScriptedLlmTurn, UsageReport
from lucy.providers import default_model_registry
from lucy.settings import LatencyBudgets
from lucy.testing import check_checkpoint_store

POSTGRES_URL_ENV = "LUCY_DATABASE_URL"
REDIS_URL_ENV = "LUCY_REDIS_URL"
POSTGRES_THREAD_ID = "checkpoint-conformance-postgres"
REDIS_THREAD_ID = "checkpoint-conformance-redis"
TEST_REDIS_TTL_SECONDS = 120
DURABLE_THREAD_ID = "checkpoint-durable-heard-state"


async def test_postgres_checkpoint_store_passes_conformance():
    store = PostgresCheckpointStore(os.environ[POSTGRES_URL_ENV])
    try:
        await store.clear_thread(POSTGRES_THREAD_ID)
        await check_checkpoint_store(store, thread_id=POSTGRES_THREAD_ID)
    finally:
        await store.clear_thread(POSTGRES_THREAD_ID)
        await store.close()


async def test_redis_checkpoint_store_passes_conformance_and_sets_ttl():
    store = RedisCheckpointStore(
        os.environ[REDIS_URL_ENV],
        ttl_seconds=TEST_REDIS_TTL_SECONDS,
    )
    try:
        await store.clear_thread(REDIS_THREAD_ID)
        await check_checkpoint_store(store, thread_id=REDIS_THREAD_ID)
        ttl = await store.thread_ttl(REDIS_THREAD_ID)
        assert 0 < ttl <= TEST_REDIS_TTL_SECONDS
    finally:
        await store.clear_thread(REDIS_THREAD_ID)
        await store.close()


async def test_postgres_resume_after_barge_in_uses_only_heard_assistant_text():
    store = PostgresCheckpointStore(os.environ[POSTGRES_URL_ENV])
    clock = ManualClock()
    simulator = LocalLlmSimulator(
        [
            ScriptedLlmTurn(
                tokens=["This complete answer must not survive the interruption."],
                usage=UsageReport(4, 8),
            )
        ],
        clock,
        token_interval_ms=0,
    )
    inner = CascadedTurnDriver(
        simulator,
        default_model_registry(),
        "openai",
        "gpt-5",
        clock,
        LatencyBudgets(),
        min_flush_chars=1,
    )
    graph = default_agent_graph(inner).compile(checkpointer=store)
    driver = GraphTurnDriver(
        graph,
        session_id="durable-call-1",
        thread_id=DURABLE_THREAD_ID,
        clock=clock,
    )
    scenario = SyntheticCallScenario(
        name="durable_barge_in",
        objective="resume from heard state",
        turns=[SyntheticTurn(speaker="caller", text="Tell me the policy")],
        expected_outcome="completed",
    )
    try:
        await store.clear_thread(DURABLE_THREAD_ID)
        result = await ConversationHarness(session_id="durable-call-1").run(
            scenario,
            None,
            driver=driver,
            clock=clock,
            barge_in_turns={0},
        )
        heard = result.turn_records[0].assistant_text
        assert heard
        assert heard != "This complete answer must not survive the interruption."

        followup_simulator = LocalLlmSimulator(
            [
                ScriptedLlmTurn(
                    tokens=["The second call continues the same thread."],
                    usage=UsageReport(5, 7),
                )
            ],
            clock,
            token_interval_ms=0,
        )
        followup_inner = CascadedTurnDriver(
            followup_simulator,
            default_model_registry(),
            "openai",
            "gpt-5",
            clock,
            LatencyBudgets(),
            min_flush_chars=1,
        )
        followup_graph = default_agent_graph(followup_inner).compile(checkpointer=store)
        resumed = await GraphTurnDriver.resume(
            followup_graph,
            session_id="durable-call-2",
            thread_id=DURABLE_THREAD_ID,
            store=store,
            clock=clock,
        )

        assert [line.text for line in resumed.state.transcript] == [
            "Tell me the policy",
            heard,
        ]
        assert resumed.state.turns == 1

        async for _ in resumed.run_turn("Continue from before", []):
            pass

        assert [line.text for line in resumed.state.transcript] == [
            "Tell me the policy",
            heard,
            "Continue from before",
            "The second call continues the same thread.",
        ]
        latest = await store.load_latest(DURABLE_THREAD_ID)
        assert latest is not None
        assert latest.session_id == "durable-call-2"
        assert latest.thread_id == DURABLE_THREAD_ID
        assert latest.state.turns == 2
    finally:
        await store.clear_thread(DURABLE_THREAD_ID)
        await store.close()
