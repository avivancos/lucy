from lucy.settings import LatencyBudgets


def test_defaults_match_adr_0011_table():
    b = LatencyBudgets()
    assert b.endpoint_silence_ms == 150
    assert b.stt_final_ms == 60
    assert b.control_transport_ms == 10
    assert b.graph_dispatch_ms == 10
    assert b.llm_first_clause_ms == 380
    assert b.tts_first_byte_ms == 150
    assert b.gateway_pacing_ms == 30
    assert b.turn_total_ms == 800
    assert b.max_tool_rounds_per_turn == 3


def test_env_var_overrides_a_budget(monkeypatch):
    monkeypatch.setenv("LUCY_BUDGET_TURN_TOTAL_MS", "500")
    assert LatencyBudgets().turn_total_ms == 500


def test_explicit_argument_wins_over_default():
    assert LatencyBudgets(turn_total_ms=650).turn_total_ms == 650
