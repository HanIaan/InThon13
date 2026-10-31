import json
import logging
from datetime import date

import httpx
import pytest

from app.ingestion.seoul import sdot
from app.ingestion.seoul.client import (
    SeoulApiError,
    SeoulOpenDataClient,
    parse_api_response,
    parse_file_list,
)


def test_parse_json_payload(fixtures_dir):
    total, rows = parse_api_response("IotVdata017", (fixtures_dir / "api_iotvdata017_sample.json").read_text(encoding="utf-8"))
    assert total == 879 and len(rows) == 5 and rows[0]["SN"] == "V02Q1940935"


def test_xml_error_even_for_json_request(fixtures_dir):
    with pytest.raises(SeoulApiError) as e:
        parse_api_response("IotVdata017", (fixtures_dir / "api_error_335.xml").read_text(encoding="utf-8"))
    assert e.value.code == "ERROR-335"


def test_no_data_is_empty():
    assert parse_api_response("IotVdata017", '{"RESULT":{"CODE":"INFO-200","MESSAGE":"해당하는 데이터가 없습니다."}}') == (0, [])


def test_invalid_key_raises():
    with pytest.raises(SeoulApiError) as e:
        parse_api_response("X", '{"RESULT":{"CODE":"INFO-100","MESSAGE":"인증키가 유효하지 않습니다."}}')
    assert e.value.code == "INFO-100"


def test_parse_file_list(fixtures_dir):
    files = parse_file_list((fixtures_dir / "dataset_page_sample.html").read_text(encoding="utf-8"))
    names = {f.seq: f.filename for f in files}
    assert names["100000001"] == "서울시 도시데이터 센서(S-DoT) 환경정보 설치 위치정보.xlsx"
    assert names["20261026"] == "S-DoT_NATURE_2026.10.12-10.18.csv"


def _mock_client(handler, key="SECRETKEY123"):
    return SeoulOpenDataClient(key, http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_pagination_and_filters(caplog):
    calls = []

    def handler(req: httpx.Request):
        parts = req.url.path.strip("/").split("/")
        calls.append(parts)
        start, end = int(parts[3]), int(parts[4])
        total = 2300
        rows = [{"SN": f"S{i}"} for i in range(start, min(end, total) + 1)]
        return httpx.Response(200, text=json.dumps({"IotVdata017": {"list_total_count": total, "RESULT": {"CODE": "INFO-000"}, "row": rows}}))

    caplog.set_level(logging.DEBUG)
    with _mock_client(handler) as c:
        rows = list(c.api_rows("IotVdata017", "Jung-gu", "2026-10-30"))
    assert len(rows) == 2300
    assert [(p[3], p[4]) for p in calls] == [("1", "1000"), ("1001", "2000"), ("2001", "3000")]
    assert calls[0][5:] == ["Jung-gu", "2026-10-30"]
    assert "SECRETKEY123" not in caplog.text


def test_sample_key_stops_after_five():
    seen = []

    def handler(req):
        seen.append(req.url.path)
        return httpx.Response(200, text=json.dumps({"S": {"list_total_count": 100, "RESULT": {"CODE": "INFO-000"}, "row": [{}] * 5}}))

    c = SeoulOpenDataClient(None, http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert c.is_sample and len(list(c.api_rows("S"))) == 5
    assert seen == ["/sample/json/S/1/5/"]


def test_retry_on_5xx(monkeypatch):
    monkeypatch.setattr("app.ingestion.seoul.client.time.sleep", lambda s: None)
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        if n["i"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, text='{"S":{"list_total_count":0,"RESULT":{"CODE":"INFO-000"},"row":[]}}')

    with _mock_client(handler) as c:
        assert c.api_page("S", 1, 5) == (0, [])
    assert n["i"] == 3


def test_download_rejects_html_and_caches(tmp_path):
    def handler(req):
        assert req.method == "POST" and b"seq=42" in req.content
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html>error</html>")

    with _mock_client(handler) as c:
        with pytest.raises(SeoulApiError):
            c.download_dataset_file("OA-1", "42", tmp_path / "f.csv")
        cached = tmp_path / "g.csv"
        cached.write_text("x")
        assert c.download_dataset_file("OA-1", "42", cached) == cached  # no request for cached file


# ------------------------------------------------------------- real network (skipped by default)

@pytest.mark.network
def test_network_sdot_sample_key():
    with SeoulOpenDataClient(None) as c:
        total, rows = c.api_page("IotVdata017", 1, 5)
    assert total > 0 and len(rows) == 5 and "AVG_NIS" in rows[0]


@pytest.mark.network
def test_network_dataset_file_lists():
    with SeoulOpenDataClient(None) as c:
        assert sdot.latest_weekly_period(c.list_dataset_files(sdot.INF_ID)) >= (date(2026, 10, 12), date(2026, 10, 18))
        files = c.list_dataset_files("OA-14991")
    assert any(f.filename == "LOCAL_PEOPLE_DONG_202607.zip" for f in files)
