import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.scripts import paper_replay_batch


def test_load_symbols_merges_cli_and_file_with_comments(tmp_path):
    symbols_file = tmp_path / "symbols.txt"
    symbols_file.write_text(
        """
        # sample pool
        sh603893
        sz.300724  # 捷佳伟创
        sh603893
        """,
        encoding="utf-8",
    )

    symbols = paper_replay_batch._load_symbols(["sh.603986"], str(symbols_file))

    assert paper_replay_batch._normalize_symbols(symbols) == ["sh.603986", "sh.603893", "sz.300724"]


def test_windows_from_dates_and_explicit_windows():
    args = paper_replay_batch.parse_args(
        [
            "--symbol",
            "sh.603893",
            "--date",
            "2026-04-24",
            "2026-04-27",
            "--start-time",
            "13:30:00",
            "--end-time",
            "14:00:00",
        "--window",
        "2026-04-28 10:00:00",
        "2026-04-28 10:30:00",
        "--min-expected-edge-after-cost",
        "20.5",
        "--expected-edge-atr-multiple",
        "3.5",
        "--first-leg-confirmation-bars",
        "1",
        "--second-leg-confirmation-bars",
        "2",
        "--min-bars-before-window-end-for-first-leg",
        "8",
    ]
    )

    assert paper_replay_batch._windows_from_args(args) == [
        ("2026-04-28 10:00:00", "2026-04-28 10:30:00"),
        ("2026-04-24 13:30:00", "2026-04-24 14:00:00"),
        ("2026-04-27 13:30:00", "2026-04-27 14:00:00"),
    ]
    assert args.min_expected_edge_after_cost == 20.5
    assert args.expected_edge_atr_multiple == 3.5
    assert args.first_leg_confirmation_bars == 1
    assert args.second_leg_confirmation_bars == 2
    assert args.min_bars_before_window_end_for_first_leg == 8


@pytest.mark.anyio
async def test_run_replay_batch_runs_each_window_and_builds_report(monkeypatch):
    calls = []

    async def fake_run_replay_pool(**kwargs):
        calls.append(kwargs)
        label = kwargs["run_label"]
        return [
            {
                "run_id": f"run_{label}_{symbol}",
                "symbol": symbol,
                "steps": 2,
                "filled_count": 0,
                "closed_t_count": 0,
                "t_closure_rate": 0.0,
                "realized_pnl": 0.0,
                "feature_cache": {"hits": 0, "misses": 2},
            }
            for symbol in kwargs["symbols"]
        ]

    reports = []

    def fake_build_decision_report(**kwargs):
        reports.append(kwargs)
        return {"run_count": len(kwargs["run_ids"]), "decision_count": 0}

    monkeypatch.setattr(paper_replay_batch, "run_replay_pool", fake_run_replay_pool)
    monkeypatch.setattr(paper_replay_batch, "build_decision_report", fake_build_decision_report)

    result = await paper_replay_batch.run_replay_batch(
        symbols=["sh603893", "sz300724"],
        windows=[("2026-04-24 13:30:00", "2026-04-24 14:00:00"), ("2026-04-27 13:30:00", "2026-04-27 14:00:00")],
        run_label="strict",
        detail_source="tdx_1m_replay",
        kline_source="qmt",
        adjustflag="3",
        min_expected_edge_after_cost=20.5,
        expected_edge_atr_multiple=3.5,
        first_leg_confirmation_bars=1,
        second_leg_confirmation_bars=2,
        min_bars_before_window_end_for_first_leg=8,
    )

    assert [call["run_label"] for call in calls] == ["strict_w1", "strict_w2"]
    assert calls[0]["symbols"] == ["sh.603893", "sz.300724"]
    assert calls[0]["detail_source"] == "tdx_1m_replay"
    assert calls[0]["min_expected_edge_after_cost"] == 20.5
    assert calls[0]["expected_edge_atr_multiple"] == 3.5
    assert calls[0]["first_leg_confirmation_bars"] == 1
    assert calls[0]["second_leg_confirmation_bars"] == 2
    assert calls[0]["min_bars_before_window_end_for_first_leg"] == 8
    assert len(result["summaries"]) == 4
    assert result["run_ids"] == [
        "run_strict_w1_sh.603893",
        "run_strict_w1_sz.300724",
        "run_strict_w2_sh.603893",
        "run_strict_w2_sz.300724",
    ]
    assert reports[0]["run_ids"] == result["run_ids"]
