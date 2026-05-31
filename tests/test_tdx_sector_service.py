from pathlib import Path

from server.services import tdx_sector_service as service


def test_tdx_sector_context_reads_exact_sector_mapping(tmp_path, monkeypatch):
    root = tmp_path / "tdx"
    hq_cache = root / "T0002" / "hq_cache"
    hq_cache.mkdir(parents=True)
    (hq_cache / "tdxhy.cfg").write_text(
        "1|605196|T0706|||X300305\r\n"
        "1|688698|T0706|||X320502\r\n"
        "0|002158|T0707|||X320206\r\n",
        encoding="gbk",
    )
    (hq_cache / "tdxzs3.cfg").write_text(
        "电气设备|880446|2|1|1|T0706\r\n"
        "工程机械|880447|2|1|1|T0707\r\n"
        "电力设备|881260|12|1|0|X30\r\n"
        "电网设备|881268|12|1|0|X3003\r\n"
        "线缆部件及其他|881273|12|1|1|X300305\r\n"
        "机械设备|881292|12|1|0|X32\r\n"
        "通用设备|881294|12|1|0|X3202\r\n"
        "制冷空调设备|881300|12|1|1|X320206\r\n"
        "自动化设备|881313|12|1|0|X3205\r\n"
        "工业控制设备|881315|12|1|1|X320502\r\n",
        encoding="gbk",
    )
    (hq_cache / "tdxzsbase.cfg").write_text(
        "1|881273|0|0|0|0|0|20260522|1|1.94|0|0|0|1.69|4.73|13.84|0|35.20|0|0|-0.90|-1.17|-1.29|-0.35|0|0\r\n"
        "1|881315|0|0|0|0|0|20260522|1|2.25|0|0|0|12.89|14.06|10.76|0|24.09|0|0|0.57|2.42|4.55|6.73|0|0\r\n",
        encoding="gbk",
    )
    (hq_cache / "infoharbor_block.dat").write_text(
        "#GN_一带一路,762,880594,20130912,20260520,,\n"
        "1#605196,1#688698,\n"
        "#GN_智能电网,210,880600,20130912,20260520,,\n"
        "1#605196,\n"
        "#GN_工业互联,180,880601,20130912,20260520,,\n"
        "1#605196,0#002158,\n"
        "#GN_人形机器,80,880602,20130912,20260520,,\n"
        "1#688698,\n"
        "#FG_昨日较强,99,880999,20130912,20260520,,\n"
        "1#605196,1#688698,\n",
        encoding="gbk",
    )
    monkeypatch.setattr(service, "TDX_ROOT", str(root))
    service._resolve_tdx_root.cache_clear()
    service._read_tdxhy.cache_clear()
    service._read_tdxzs.cache_clear()
    service._read_tdxzsbase.cache_clear()

    htl = service.get_tdx_sector_context("sh.605196")
    wc = service.get_tdx_sector_context("sh.688698")

    assert htl["primary_sector"]["name"] == "线缆部件及其他"
    assert htl["primary_sector"]["index_code"] == "881273"
    assert htl["tdx_industry"]["path"] == ["电气设备"]
    assert htl["daily_stats"]["ret_5"] == -1.17
    assert [item["name"] for item in htl["concept_themes"]] == ["工业互联", "智能电网", "一带一路"]
    assert wc["primary_sector"]["name"] == "工业控制设备"
    assert wc["primary_sector"]["path"] == ["机械设备", "自动化设备", "工业控制设备"]
    assert [item["name"] for item in wc["concept_themes"]] == ["人形机器", "一带一路"]
