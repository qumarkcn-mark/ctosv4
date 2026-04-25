import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.domain.contracts import RadarContract as RadarContractShape
from server.domain.enums import AdjustmentFlag, PlanStatus, PlanType, RadarMode, StructureProvider
from server.domain.models import LevelStructure, RadarContract, RiskPlan


def test_domain_enums_keep_contract_values():
    assert RadarMode.EMPTY.value == "EMPTY"
    assert PlanType.ENTRY.value == "ENTRY"
    assert PlanStatus.WATCHING.value == "WATCHING"
    assert StructureProvider.BAOSTOCK.value == "baostock"
    assert AdjustmentFlag.FRONT.value == "2"


def test_level_structure_from_dict_accepts_adapter_shape():
    level = LevelStructure.from_dict(
        {
            "level": "day",
            "price": "20.5",
            "state": "UPWARD_LEAVING",
            "zg": 20,
            "zd": 18,
            "patterns": ["二买"],
            "zoushi_type": {"type": "盘整"},
            "active_zhongshu": {"zg": 20, "zd": 18},
            "detail_bis": [{"is_up": True}],
            "zhongshus": [{"zg": 20, "zd": 18}],
        }
    )

    assert level.level == "day"
    assert level.price == 20.5
    assert level.patterns == ["二买"]
    assert level.bis == [{"is_up": True}]
    assert level.bi_zhongshus == [{"zg": 20, "zd": 18}]


def test_radar_contract_dataclass_and_typed_shape_are_importable():
    risk = RiskPlan(invalid_if="跌破结构止损", stop_reference={"level": "5", "field": "zg", "value": 18})
    radar = RadarContract(
        api_version="radar.v1",
        symbol="sh.600519",
        mode=RadarMode.EMPTY,
        structure={"levels": {}},
        strategy={},
        plans=[],
        freshness={"is_stale": False},
        disclaimer="仅供参考，不构成投资建议",
    )
    typed_shape = RadarContractShape(
        api_version="radar.v1",
        symbol="sh.600519",
        mode="EMPTY",
        plans=[],
        disclaimer="仅供参考，不构成投资建议",
    )

    assert risk.stop_reference["level"] == "5"
    assert radar.mode == RadarMode.EMPTY
    assert typed_shape["api_version"] == "radar.v1"
