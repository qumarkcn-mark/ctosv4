import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.db.database import SCHEMA
from server.engines.decision.intraday_t_features import IntradayTFeatures
from server.engines.execution.paper_models import PaperAccount, PaperPosition, PaperRiskConfig
from server.engines.execution.paper_replay import ReplayStep, replay_intraday_t_steps
from server.engines.execution.paper_store import save_replay_result


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO users (id, openid, nickname) VALUES (1, 'dev_user', '开发者')")
    return conn


def account():
    return PaperAccount(
        paper_account_id="paper_1",
        user_id=1,
        cash=100000.0,
        positions={
            "sh.603893": PaperPosition(
                symbol="sh.603893",
                total_qty=1000,
                available_qty=400,
                protected_base_qty=300,
                avg_cost=10.0,
                last_price=12.0,
            )
        },
    )


def feature(as_of, side, direction):
    return IntradayTFeatures(
        symbol="sh.603893",
        as_of=as_of,
        level_chain={"L0": "30", "L1": "5", "L2": "1"},
        paths={"main": "PULLBACK_IN_UPTREND"},
        latest_event={"side": side, "code": "S1" if side == "sell" else "B1", "bars_since_event": 1},
        divergence={"direction": direction, "strength": 0.8},
        freshness={"is_stale": False},
    )


def test_save_replay_result_persists_account_intents_fills_and_metrics():
    conn = make_conn()
    start_account = account()
    result = replay_intraday_t_steps(
        start_account,
        [
            ReplayStep(
                features=feature("2026-04-29 10:30:00", "sell", "top"),
                next_bar={"date": "2026-04-29 10:31:00", "open": 12.0, "high": 12.2, "low": 11.8, "close": 12.1, "volume": 10000},
            ),
            ReplayStep(
                features=feature("2026-04-29 10:35:00", "buy", "bottom"),
                next_bar={"date": "2026-04-29 10:36:00", "open": 11.5, "high": 11.8, "low": 11.3, "close": 11.7, "volume": 10000},
            ),
        ],
        PaperRiskConfig(),
    )

    save_replay_result(
        conn,
        run_id="run_1",
        start_account=start_account,
        result=result,
        symbol="sh.603893",
        config={"default_t_qty": 100},
    )

    run = conn.execute("SELECT * FROM paper_replay_runs WHERE run_id='run_1'").fetchone()
    assert run["status"] == "COMPLETED"
    assert '"closed_t_count": 1' in run["metrics_json"]

    assert conn.execute("SELECT COUNT(*) FROM paper_intents").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM paper_decisions").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 2
    assert conn.execute(
        """
        SELECT COUNT(*)
          FROM paper_decisions d
          JOIN paper_fills f ON f.run_id = d.run_id AND f.intent_id = d.intent_id
         WHERE d.run_id = 'run_1'
        """
    ).fetchone()[0] == 2

    position = conn.execute(
        "SELECT * FROM paper_positions WHERE paper_account_id='paper_1' AND symbol='sh.603893'"
    ).fetchone()
    assert position["total_qty"] == 1000
    assert position["protected_base_qty"] == 300


def test_save_replay_result_scopes_intents_and_fills_per_run():
    conn = make_conn()
    start_account = account()
    result = replay_intraday_t_steps(
        start_account,
        [
            ReplayStep(
                features=feature("2026-04-29 10:30:00", "sell", "top"),
                next_bar={"date": "2026-04-29 10:31:00", "open": 12.0, "high": 12.2, "low": 11.8, "close": 12.1, "volume": 10000},
            ),
            ReplayStep(
                features=feature("2026-04-29 10:35:00", "buy", "bottom"),
                next_bar={"date": "2026-04-29 10:36:00", "open": 11.5, "high": 11.8, "low": 11.3, "close": 11.7, "volume": 10000},
            ),
        ],
        PaperRiskConfig(),
    )

    for run_id in ["run_1", "run_2"]:
        save_replay_result(
            conn,
            run_id=run_id,
            start_account=start_account,
            result=result,
            symbol="sh.603893",
            config={"default_t_qty": 100},
        )

    assert conn.execute("SELECT COUNT(*) FROM paper_intents").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 4
    assert conn.execute(
        """
        SELECT COUNT(*)
          FROM paper_decisions d
          JOIN paper_fills f ON f.run_id = d.run_id AND f.intent_id = d.intent_id
        """
    ).fetchone()[0] == 4

    fill_ids = [
        row["fill_id"]
        for row in conn.execute("SELECT fill_id FROM paper_fills ORDER BY fill_id").fetchall()
    ]
    assert fill_ids == [
        "run_1:fill_paper_intent_1",
        "run_1:fill_paper_intent_2",
        "run_2:fill_paper_intent_1",
        "run_2:fill_paper_intent_2",
    ]
