from unittest.mock import MagicMock, patch

import pytest

from src.observability import compute_cost, write_llm_call


def test_compute_cost_sonnet():
    # sonnet: $3.00/M input, $15.00/M output
    # 1000 input + 500 output = 0.003 + 0.0075 = 0.0105
    assert compute_cost("claude-sonnet-4-6", 1000, 500) == pytest.approx(0.0105)


def test_compute_cost_opus():
    # opus: $15.00/M input, $75.00/M output
    # 1000 input + 500 output = 0.015 + 0.0375 = 0.0525
    assert compute_cost("claude-opus-4-7", 1000, 500) == pytest.approx(0.0525)


def test_compute_cost_haiku():
    # haiku: $0.80/M input, $4.00/M output
    # 1000 input + 500 output = 0.0008 + 0.002 = 0.0028
    assert compute_cost("claude-haiku-4-5-20251001", 1000, 500) == pytest.approx(0.0028)


def test_write_llm_call_persists_correct_cost():
    fake_usage = MagicMock(input_tokens=1000, output_tokens=500)
    mock_db = MagicMock()

    with patch("src.observability.get_session") as mock_get_session:
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        write_llm_call("claude-sonnet-4-6", "test_agent", "sess-1", fake_usage, 250)

    mock_db.add.assert_called_once()
    row = mock_db.add.call_args[0][0]
    assert row.model == "claude-sonnet-4-6"
    assert row.agent == "test_agent"
    assert row.session_id == "sess-1"
    assert row.input_tokens == 1000
    assert row.output_tokens == 500
    assert row.latency_ms == 250
    assert row.cost_usd == pytest.approx(compute_cost("claude-sonnet-4-6", 1000, 500))
    mock_db.commit.assert_called_once()
