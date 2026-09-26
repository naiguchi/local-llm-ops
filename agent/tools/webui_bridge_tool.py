"""
title: local-llm-ops Bridge
author: local-llm-ops
description: ホスト Mac の AWS CLI / DB トンネル / 参照 SQL（host.docker.internal:3091）
version: 1.1.0
"""

import json
import urllib.error
import urllib.request
from typing import Optional

from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        BRIDGE_URL: str = Field(default="http://host.docker.internal:3091")

    def __init__(self):
        self.valves = self.Valves()

    def _get(self, path: str) -> str:
        url = f"{self.valves.BRIDGE_URL.rstrip('/')}{path}"
        with urllib.request.urlopen(url, timeout=60) as resp:
            return resp.read().decode("utf-8")

    def _post(self, path: str, payload: dict) -> str:
        url = f"{self.valves.BRIDGE_URL.rstrip('/')}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            return json.dumps(
                {"ok": False, "http_status": exc.code, "error": detail},
                ensure_ascii=False,
            )

    def list_aws_profiles(self) -> str:
        """利用可能な AWS プロファイル名を一覧する（秘密は含まない）。"""
        return self._get("/aws/profiles")

    def run_aws(
        self,
        profile: str,
        args_csv: str,
        confirm: bool = False,
    ) -> str:
        """ホストで AWS CLI を実行する。args_csv はカンマ区切り（例: sts,get-caller-identity）。confirm=False は計画のみ。True で実実行。"""
        args = [a.strip() for a in args_csv.split(",") if a.strip()]
        return self._post(
            "/aws/run",
            {"profile": profile, "args": args, "confirm": bool(confirm)},
        )

    def list_db_connections(self) -> str:
        """許可された DB 接続ターゲット一覧（profiles/db.json）。"""
        return self._get("/db/connections")

    def open_db_tunnel(
        self,
        connection: str,
        confirm: bool = False,
        close: bool = False,
    ) -> str:
        """AWS SSM+SSH で DB トンネルを開く/閉じる。まず confirm=False、承認後 True。connection は db.json の name。"""
        return self._post(
            "/db/tunnel",
            {
                "connection": connection,
                "confirm": bool(confirm),
                "close": bool(close),
            },
        )

    def run_sql(
        self,
        connection: str,
        sql: str,
        confirm: bool = False,
    ) -> str:
        """トンネル経由で参照 SQL（SELECT/COUNT 等）を実行。confirm=False は計画のみ。個人一覧は避け件数集計を優先。"""
        return self._post(
            "/db/sql",
            {
                "connection": connection,
                "sql": sql,
                "confirm": bool(confirm),
            },
        )
