import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.services.llm_service import _loads_lenient_json_object


def test_lenient_json_object_parser_repairs_common_model_drift():
    raw = """```json
{
  "primary_path_id": "B",
  "path_inferences": [
    {"id": "path-B", "probability": 40%,}
  ],
}
```"""

    parsed = _loads_lenient_json_object(raw)

    assert parsed["primary_path_id"] == "B"
    assert parsed["path_inferences"][0]["probability"] == "40%"


def test_lenient_json_object_parser_keeps_non_empty_duplicate_rows():
    raw = """{
      "rows": [{"name": "飞荣达", "direction": "SELL"}],
      "broker": "THS",
      "rows": []
    }"""

    parsed = _loads_lenient_json_object(raw)

    assert parsed["rows"] == [{"name": "飞荣达", "direction": "SELL"}]
