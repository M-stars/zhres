"""LLM 客户端测试：dry-run 无网络、mock 重试、降级、解析、断点续跑。

真实网络测试标记 live：无 ZHRES_API_KEY 时自动跳过。
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from zhres import llm
from zhres.errors import LlmError
from zhres.project import Entry
from zhres.translate import translate_project

CONFIG = {
    "llm": {
        "enabled": True,
        "base_url": "https://api.example.com/v1",
        "model": "test-model",
        "api_key_env": "ZHRES_TEST_KEY",
        "batch_size": 2,
        "temperature": 0.2,
        "timeout_seconds": 5,
        "max_retries": 3,
    }
}


def _entries(*sources, kind="menu_item"):
    return [Entry(id=f"RT_MENU/1/1033/{i}", resource_type="menu",
                  type_id=4, resource_name=1, language=1033,
                  context={"kind": kind}, source=s)
            for i, s in enumerate(sources)]


class TestParseAnswer:
    def test_plain_json(self):
        out = llm._parse_answer('{"translations":[{"id":"a","translation":"甲"}]}')
        assert out == {"a": "甲"}

    def test_json_fence(self):
        out = llm._parse_answer(
            '```json\n{"translations":[{"id":"a","translation":"甲"}]}\n```')
        assert out == {"a": "甲"}

    def test_invalid_json(self):
        with pytest.raises(LlmError):
            llm._parse_answer("抱歉我不会")


class TestRetryAndDegrade:
    def test_retry_on_5xx_then_success(self, monkeypatch):
        monkeypatch.setenv("ZHRES_TEST_KEY", "sk-test")
        sleeps = []
        monkeypatch.setattr(llm, "_sleep", lambda s: sleeps.append(s))
        calls = []

        class FakeResp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout):
            calls.append(json.loads(req.data.decode("utf-8")))
            if len(calls) < 3:
                raise urllib.error.HTTPError(
                    req.full_url, 500, "boom", {}, io.BytesIO(b"server err"))
            items = json.loads(calls[-1]["messages"][1]["content"])["items"]
            return FakeResp(json.dumps({
                "choices": [{"message": {"content": json.dumps(
                    {"translations": [
                        {"id": i["id"], "translation": "文件"} for i in items
                    ]})}}]}).encode())

        monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
        out = llm.translate_batch(_entries("File"), CONFIG)
        assert out == {"RT_MENU/1/1033/0": "文件"}
        assert sleeps == [2, 4], "指数退避 2s/4s"

    def test_4xx_fails_immediately(self, monkeypatch):
        monkeypatch.setenv("ZHRES_TEST_KEY", "sk-test")
        sleeps = []
        monkeypatch.setattr(llm, "_sleep", lambda s: sleeps.append(s))

        def fake_urlopen(req, timeout):
            raise urllib.error.HTTPError(
                req.full_url, 401, "unauthorized", {}, io.BytesIO(b"bad key"))

        monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(LlmError, match="HTTP 401"):
            llm.translate_batch(_entries("File"), CONFIG)
        assert sleeps == [], "4xx 不重试"

    def test_response_format_degrade(self, monkeypatch):
        monkeypatch.setenv("ZHRES_TEST_KEY", "sk-test")
        monkeypatch.setattr(llm, "_sleep", lambda s: None)
        payloads = []

        class FakeResp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout):
            payload = json.loads(req.data.decode("utf-8"))
            payloads.append(payload)
            if len(payloads) == 1:
                assert "response_format" in payload
                raise urllib.error.HTTPError(
                    req.full_url, 400, "bad", {}, io.BytesIO(
                        b"response_format unsupported"))
            assert "response_format" not in payload, "降级后应去掉该参数"
            items = payload["messages"][1]["content"]
            data = json.loads(items)
            return FakeResp(json.dumps({
                "choices": [{"message": {"content": json.dumps(
                    {"translations": [{"id": i["id"], "translation": "译"}]
                     for i in data["items"]})}}]}).encode())

        monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
        out = llm.translate_batch(_entries("File", "Exit"), CONFIG)
        assert out["RT_MENU/1/1033/0"] == "译"
        assert len(payloads) == 2

    def test_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("ZHRES_TEST_KEY", raising=False)
        with pytest.raises(LlmError, match="ZHRES_TEST_KEY"):
            llm.translate_batch(_entries("File"), CONFIG)


class TestDedupeAndPipeline:
    def test_same_source_sent_once(self, monkeypatch):
        monkeypatch.setenv("ZHRES_TEST_KEY", "sk-test")
        monkeypatch.setattr(llm, "_sleep", lambda s: None)
        seen_items = []

        class FakeResp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout):
            payload = json.loads(req.data.decode("utf-8"))
            items = json.loads(payload["messages"][1]["content"])["items"]
            seen_items.append(items)
            return FakeResp(json.dumps({
                "choices": [{"message": {"content": json.dumps(
                    {"translations": [{"id": i["id"], "translation": "译-" + i["source"]}
                                      for i in items]})}}]}).encode())

        monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
        out = llm.translate_batch(_entries("Open", "Open", "Open"), CONFIG)
        assert len(seen_items) == 1 and len(seen_items[0]) == 1, "同文只送一次"
        assert len(out) == 3, "结果回填所有同文条目"

    def test_translate_project_with_mock_llm(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZHRES_TEST_KEY", "sk-test")
        monkeypatch.setattr(llm, "_sleep", lambda s: None)

        class FakeResp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout):
            payload = json.loads(req.data.decode("utf-8"))
            items = json.loads(payload["messages"][1]["content"])["items"]
            return FakeResp(json.dumps({
                "choices": [{"message": {"content": json.dumps(
                    {"translations": [{"id": i["id"],
                                       "translation": "译" + i["source"]}
                                      for i in items]})}}]}).encode())

        monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
        project_entries = _entries("File", "Exit", "Help")
        saved = []

        result = translate_project(
            type("P", (), {"entries": project_entries})(),  # 鸭子类型工程
            llm=llm.make_llm_filler(CONFIG),
            batch_size=2,
            save_callback=lambda: saved.append(1))
        assert result.llm_hits == 3
        assert saved, "断点续跑：每批成功即回调保存"
        assert project_entries[0].translation == "译File"

    def test_dry_run_no_network(self, monkeypatch):
        def explode(*a, **kw):
            raise AssertionError("dry-run 不得发起网络请求")

        monkeypatch.setattr(llm.urllib.request, "urlopen", explode)
        # make_llm_filler(dry_run=True) 返回 None → translate 不调用 LLM
        assert llm.make_llm_filler(CONFIG, dry_run=True) is None
        project_entries = _entries("File")
        result = translate_project(
            type("P", (), {"entries": project_entries})(),
            llm=None, dry_run=True)
        assert result.llm_hits == 0


@pytest.mark.live
class TestLive:
    def test_real_endpoint_smoke(self, monkeypatch):
        """真实 key 烟雾测试：设置 ZHRES_API_KEY 与 ZHRES_LIVE=1 才运行。"""
        import os
        if not os.environ.get("ZHRES_API_KEY") or \
                not os.environ.get("ZHRES_LIVE"):
            pytest.skip("未设置 ZHRES_API_KEY / ZHRES_LIVE（默认跳过）")
        config = dict(CONFIG)
        config["llm"] = dict(CONFIG["llm"], base_url="https://api.openai.com/v1",
                             api_key_env="ZHRES_API_KEY")
        out = llm.translate_batch(_entries("File", "Exit"), config)
        assert out
