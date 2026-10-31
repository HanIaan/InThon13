"""HTTP access to 서울 열린데이터광장 (data.seoul.go.kr).

Two verified access paths (checked 2026-10-31):

1. Open API  ``http://openapi.seoul.go.kr:8088/{KEY}/json/{SERVICE}/{start}/{end}/{filters...}``
   - Needs a personal key (SEOUL_OPEN_API_KEY). The public key ``sample`` only allows
     rows 1..5 (ERROR-335). Max 1,000 rows per request (ERROR-336).
   - S-DoT ``IotVdata017`` and 생활인구 ``SPOP_LOCAL_RESD_DONG`` only expose the most recent
     1 / 2 months respectively.
2. Dataset file download (no key): POST ``https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do``
   with form ``infId=<OA-id>&infSeq=3&seq=<file seq>``. The seq of every file is listed on the
   dataset page as ``downloadFile('<seq>')``.
"""
from __future__ import annotations

import html
import json
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree as ET

import logging

import httpx

from .common import log

# httpx logs full request URLs at INFO; Open API URLs contain the key -> keep those loggers quiet.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

OPEN_API_BASE = "http://openapi.seoul.go.kr:8088"
DATASET_PAGE = "https://data.seoul.go.kr/dataList/{inf_id}/S/1/datasetView.do"
FILE_DOWNLOAD = "https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do?&useCache=false"
SAMPLE_KEY = "sample"
PAGE_SIZE = 1000
SAMPLE_PAGE_SIZE = 5
USER_AGENT = "sensory-map-ingestion/0.1 (+research; data.seoul.go.kr open data)"


class SeoulApiError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class DatasetFile:
    seq: str
    filename: str


def _retrying(fn, *, attempts: int = 4, backoff: float = 1.5):
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and status < 500 and status != 429:
                raise
            last = exc
            wait = backoff ** i
            log.warning("request failed (%s), retry %d/%d in %.1fs", exc, i + 1, attempts, wait)
            time.sleep(wait)
    assert last is not None
    raise last


def parse_api_payload(service: str, payload: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    """Return (list_total_count, rows). INFO-200 (no data) -> (0, []). Other errors raise."""
    body = payload.get(service)
    result = (body or payload).get("RESULT") or {}
    code = str(result.get("CODE", ""))
    if code == "INFO-200":
        return 0, []
    if body is None or code not in ("", "INFO-000"):
        raise SeoulApiError(code or "UNKNOWN", str(result.get("MESSAGE", "")).strip())
    return int(body.get("list_total_count", 0)), list(body.get("row") or [])


def parse_api_response(service: str, text: str) -> tuple[int, list[dict[str, Any]]]:
    """JSON body, or the XML ``<RESULT>`` the server returns for errors even when JSON was requested."""
    body = text.strip()
    if body.startswith("<"):
        root = ET.fromstring(body)
        result = root if root.tag == "RESULT" else root.find("RESULT")
        if result is None:
            raise SeoulApiError("UNKNOWN", body[:200])
        code = (result.findtext("CODE") or "").strip()
        if code == "INFO-200":
            return 0, []
        raise SeoulApiError(code or "UNKNOWN", (result.findtext("MESSAGE") or "").strip())
    return parse_api_payload(service, json.loads(body))


def parse_file_list(page_html: str) -> list[DatasetFile]:
    """Extract (seq, filename) from a dataset page's file list."""
    out: dict[str, DatasetFile] = {}
    for m in re.finditer(r'title="([^"]+)"\s+onclick="javascript:downloadFile\(\'([^\']+)\'\);"', page_html):
        name, seq = html.unescape(m.group(1)), m.group(2)
        out.setdefault(seq, DatasetFile(seq=seq, filename=name))
    return list(out.values())


class SeoulOpenDataClient:
    def __init__(self, api_key: str | None = None, *, timeout: float = 60.0, http: httpx.Client | None = None):
        self.api_key = api_key or None
        self._http = http or httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True)

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------------------------------------------------------- Open API

    @property
    def key(self) -> str:
        return self.api_key or SAMPLE_KEY

    @property
    def is_sample(self) -> bool:
        return self.api_key is None

    def api_page(self, service: str, start: int, end: int, *filters: str) -> tuple[int, list[dict[str, Any]]]:
        parts = [OPEN_API_BASE, self.key, "json", service, str(start), str(end), *[quote(str(f), safe="") for f in filters]]
        url = "/".join(parts) + "/"

        def call():
            r = self._http.get(url)
            r.raise_for_status()
            return r.text

        # never log the key
        log.debug("GET %s/%s/%d-%d/%s", service, "sample" if self.is_sample else "***", start, end, "/".join(filters))
        return parse_api_response(service, _retrying(call))

    def api_rows(self, service: str, *filters: str, max_rows: int | None = None) -> Iterator[dict[str, Any]]:
        """Iterate all rows of a service query with pagination (1,000 rows per call; sample key: 5 total)."""
        size = SAMPLE_PAGE_SIZE if self.is_sample else PAGE_SIZE
        start, seen = 1, 0
        while True:
            total, rows = self.api_page(service, start, start + size - 1, *filters)
            for row in rows:
                yield row
                seen += 1
                if max_rows is not None and seen >= max_rows:
                    return
            start += size
            if not rows or start > total or self.is_sample:
                return

    # ---------------------------------------------------------- file download

    def list_dataset_files(self, inf_id: str) -> list[DatasetFile]:
        def call():
            r = self._http.get(DATASET_PAGE.format(inf_id=inf_id))
            r.raise_for_status()
            return r.text

        return parse_file_list(_retrying(call))

    def download_dataset_file(self, inf_id: str, seq: str, dest: Path, *, inf_seq: str = "3", overwrite: bool = False) -> Path:
        """Download one dataset file (no key). Cached: an existing non-empty file is reused."""
        if dest.exists() and dest.stat().st_size > 0 and not overwrite:
            log.info("cached %s", dest.name)
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        form = {"infId": inf_id, "seqNo": "", "seq": seq, "infSeq": inf_seq}

        def call():
            with self._http.stream("POST", FILE_DOWNLOAD, data=form, timeout=600.0) as r:
                r.raise_for_status()
                ctype = r.headers.get("content-type", "")
                if "text/html" in ctype:
                    raise SeoulApiError("DOWNLOAD", f"no file for {inf_id} seq={seq} (got HTML)")
                with tmp.open("wb") as fh:
                    for chunk in r.iter_bytes(1 << 20):
                        fh.write(chunk)

        log.info("downloading %s seq=%s -> %s", inf_id, seq, dest.name)
        _retrying(call)
        tmp.replace(dest)
        return dest
