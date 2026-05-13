from server.engines.structure.czsc_evidence import build_czsc_evidence


def test_build_czsc_evidence_compacts_shadow_structure():
    result = build_czsc_evidence(
        {
            "adapter_version": "czsc_adapter.v1",
            "levels": {
                "day": {
                    "state_hint": "above_zg",
                    "last_bi_dir": "up",
                    "price_vs_center": {"position": "above_zg"},
                    "active_zhongshu": {
                        "zg": 12,
                        "zd": 10,
                        "zz": 11,
                        "gg": 13,
                        "dd": 9,
                        "begin_date": "2026-01-01",
                        "end_date": "2026-01-05",
                    },
                    "stats": {"fx_count": 5, "bi_count": 4, "bi_zs_count": 1},
                }
            },
        }
    )

    assert result["available"] is True
    assert result["levels"]["day"]["active_center"]["zg"] == 12
    assert result["levels"]["day"]["counts"] == {"fx": 5, "bi": 4, "zs": 1}
