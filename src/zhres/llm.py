"""llm —— OpenAI 兼容 LLM 批量翻译客户端（可选增强，默认关闭）。

- 端点：``{base_url}/chat/completions``，仅用标准库 urllib.request
- 批处理：同 context_kind 的相邻条目一批；相同 source 只送一次，
  结果回填所有同文条目
- 重试：指数退避（2s/4s/8s），仅对网络错误与 5xx；4xx 立即失败并
  输出响应体摘要
- ``response_format``：默认携带 json_object；端点不支持（4xx 报错）时
  降级为不带该参数重试一次，纯文本 JSON 解析
- 断点续跑：每批成功即写回工程 JSON（由调用方回调完成，原子写）
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .errors import LlmError
from .project import Entry

# 内置系统提示词（任务书附录 A，逐字使用）
SYSTEM_PROMPT = """你是资深的软件界面本地化专家，任务是把 Windows 软件界面文本从英文翻译成简体中文。

规则：
1. 遵守 Windows 中文软件惯例：File→文件、Edit→编辑、Exit→退出、About→关于、OK→确定、Cancel→取消、Help→帮助。
2. 带有 & 的英文（如 E&xit）表示快捷键：译文采用 "中文(&X)" 格式，保留原快捷键字母。
3. 严格保留所有占位符（%s、%d、%1$s、{0}、\\n、\\t），一个都不能丢。
4. 菜单项和按钮用词简洁（动词优先）；对话框标题、提示语可用完整句；错误消息礼貌、清晰。
5. 不确定的专有名词保留英文原文，不要硬译产品名。
6. 只输出 JSON，不要输出任何解释。输入是 {"items":[{"id":...,"source":...,"context":...}]}，
   输出 {"translations":[{"id":...,"translation":...}]}，id 一一对应。"""

_BACKOFF = (2, 4, 8)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _chat(base_url: str, model: str, api_key: str, items: list[dict],
          temperature: float, timeout: int) -> str:
    """调用 /chat/completions，返回助手消息文本。"""
    payload = {
        "model": model,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",
             "content": json.dumps({"items": items}, ensure_ascii=False)},
        ],
    }
    return _post(base_url, model, api_key, payload, timeout)


def _post(base_url: str, model: str, api_key: str, payload: dict,
          timeout: int, allow_degrade: bool = True) -> str:
    """POST 一次请求（含重试）。返回助手消息文本。"""
    url = base_url.rstrip("/") + "/chat/completions"
    last_error: Exception | None = None
    for attempt in range(len(_BACKOFF) + 1):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as exc:
            resp_body = ""
            try:
                resp_body = exc.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            if exc.code == 400 and allow_degrade and \
                    "response_format" in payload:
                # 端点不支持 response_format：降级重试一次
                payload = {k: v for k, v in payload.items()
                           if k != "response_format"}
                return _post(base_url, model, api_key, payload, timeout,
                             allow_degrade=False)
            if exc.code >= 500 and attempt < len(_BACKOFF):
                last_error = LlmError(
                    f"LLM 服务端错误 {exc.code}（重试 {_BACKOFF[attempt]}s 后）："
                    f"{resp_body}")
                _sleep(_BACKOFF[attempt])
                continue
            raise LlmError(f"LLM 请求失败（HTTP {exc.code}）：{resp_body}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt < len(_BACKOFF):
                last_error = LlmError(
                    f"LLM 网络错误（重试 {_BACKOFF[attempt]}s 后）：{exc}")
                _sleep(_BACKOFF[attempt])
                continue
            raise LlmError(f"LLM 网络错误（已重试 {len(_BACKOFF)} 次）：{exc}") from exc
    raise LlmError(f"LLM 请求失败：{last_error}")


def _parse_answer(content: str) -> dict[str, str]:
    """解析助手返回的 JSON（容忍 ```json 围栏）。"""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LlmError(f"LLM 返回的不是合法 JSON：{content[:200]}") from exc
    out: dict[str, str] = {}
    for item in data.get("translations", []):
        if isinstance(item, dict) and "id" in item and "translation" in item:
            out[str(item["id"])] = str(item["translation"])
    return out


def translate_batch(entries: list[Entry], config: dict) -> dict[str, str]:
    """把一批条目送 LLM 翻译，返回 {entry_id: translation}。

    相同 source 只送一次；返回的 id→译文会回填到批内所有同文条目
    （由 make_llm_filler 的 fill 完成回填）。
    """
    llm_cfg = config.get("llm", {})
    base_url = llm_cfg.get("base_url", "https://api.openai.com/v1")
    model = llm_cfg.get("model", "gpt-4o-mini")
    temperature = llm_cfg.get("temperature", 0.2)
    timeout = llm_cfg.get("timeout_seconds", 60)
    api_key_env = llm_cfg.get("api_key_env", "ZHRES_API_KEY")

    import os
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        raise LlmError(
            f"未设置 API key：环境变量 {api_key_env} 为空（LLM 调用已取消）")

    items: list[dict] = []
    id_by_source: dict[str, list[str]] = {}
    for entry in entries:
        if entry.source in id_by_source:
            id_by_source[entry.source].append(entry.id)
            continue
        id_by_source[entry.source] = [entry.id]
        items.append({"id": entry.id, "source": entry.source,
                      "context": entry.context.get("kind", "")})
    content = _chat(base_url, model, api_key, items, temperature, timeout)
    answers = _parse_answer(content)

    # id → 译文（同文条目由调用方回填，这里保证每个送达 id 都有答案）
    result: dict[str, str] = {}
    for source, ids in id_by_source.items():
        found = next((answers[i] for i in ids if i in answers), None)
        if found is None:
            # 兼容端点改写 id：按顺序回退
            found = next((t for i, t in answers.items() if i not in ids),
                         None)
        if found is not None:
            for entry_id in ids:
                result[entry_id] = found
    return result


def make_llm_filler(config: dict, dry_run: bool = False):
    """构造 translate_project 需要的 llm 回调。dry_run 时不建网络客户端。"""
    if dry_run:
        return None  # dry-run 永远不产生网络请求
    from .translate import _make_batches

    batch_size = config.get("llm", {}).get("batch_size", 20)

    def fill(batch: list[Entry]) -> dict[str, str]:
        answers: dict[str, str] = {}
        for sub in _make_batches(batch, batch_size):
            answers.update(translate_batch(sub, config))
        return answers

    return fill
