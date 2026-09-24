"""Ollama 本地模型客户端(嵌入 + 对话)。

设计(稳定性加固):
- 同步客户端 OllamaClient:FastAPI 的 def 端点运行于线程池,FastAPI/Starlette
  会把同步 StreamingResponse 生成器放入线程池迭代 —— 全程不阻塞事件循环。
- 全局串行信号量:嵌入锁 / 对话锁各一 —— 同一时刻只允许一个嵌入批次与
  一个对话流,防止本地 GPU(8GB)并发推理把显存挤爆导致 Ollama 无响应。
- 连接/读取超时、显式 keep_alive;模型缺失与连接失败给出友好提示。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Iterable, Iterator

import httpx

from .config import settings

_EMBED_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=60.0, pool=10.0)
_CHAT_TIMEOUT = httpx.Timeout(connect=5.0, read=1800.0, write=60.0, pool=10.0)

# 全局串行闸:本地单 GPU 场景,并发推理会挤爆显存 → Ollama 长时间无响应。
# 单用户本地:串行不会带来明显体验损失,却可根治 OOM。
_embed_semaphore = threading.BoundedSemaphore(1)
_chat_semaphore = threading.BoundedSemaphore(1)
_LOCK_WAIT = 600.0  # 排队等待上限(秒);上传大文档嵌入与提问可能互相等待


class OllamaError(RuntimeError):
    pass


class OllamaUnavailable(OllamaError):
    """Ollama 进程不可达(区别于模型/业务错误,用于熔断提示)。"""


class OllamaBusy(OllamaError):
    """已有任务在占用推理闸,超时仍未轮到(前端提示稍后重试,不卡页面)。"""


def _conn_err(base_url: str, exc: Exception) -> OllamaUnavailable:
    return OllamaUnavailable(
        f"无法连接 Ollama({base_url}): {exc}\n请确认 Ollama 已启动(ollama serve 或本目录 start.bat)。"
    )


class OllamaClient:
    """同步客户端。"""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.ollama_host).rstrip("/")

    # ---------- 基础 ----------
    def ping(self, timeout: float = 3.0) -> bool:
        try:
            r = httpx.get(f"{self.base_url}/api/version", timeout=timeout)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def list_models(self) -> list[str]:
        try:
            resp = httpx.get(f"{self.base_url}/api/tags", timeout=10.0)
        except httpx.HTTPError:
            return []
        if resp.status_code != 200:
            return []
        return [m.get("name", "") for m in resp.json().get("models", [])]

    def model_available(self, model: str) -> bool:
        names = self.list_models()
        return model in names or any(n.split(":")[0] == model for n in names)

    def ensure_model(self, model: str) -> None:
        if not self.ping():
            raise OllamaUnavailable(f"Ollama 未运行({self.base_url}),无法校验模型。")
        if not self.model_available(model):
            raise OllamaError(f"模型 '{model}' 未安装。请先运行: ollama pull {model}")

    # ---------- 嵌入 ----------
    def embed(self, texts: Iterable[str], model: str | None = None) -> list[list[float]]:
        """批量嵌入(全局串行:一次仅一个嵌入批次,防并发挤爆显存)。"""
        model = model or settings.embed_model
        text_list = [t for t in texts if t and t.strip()]
        if not text_list:
            return []
        if not _embed_semaphore.acquire(timeout=_LOCK_WAIT):
            raise OllamaBusy("嵌入服务繁忙(已有任务在占用模型),请稍候重试。")
        try:
            return self._embed_unlocked(text_list, model)
        finally:
            _embed_semaphore.release()

    def _embed_unlocked(self, text_list: list[str], model: str) -> list[list[float]]:
        try:
            with httpx.Client(timeout=_EMBED_TIMEOUT) as client:
                resp = client.post(f"{self.base_url}/api/embed", json={"model": model, "input": text_list})
        except httpx.HTTPError as e:
            raise _conn_err(self.base_url, e) from e
        if resp.status_code != 200:
            raise OllamaError(f"Ollama /api/embed 返回 {resp.status_code}: {resp.text[:300]}")
        emb = resp.json().get("embeddings") or []
        if len(emb) != len(text_list):
            emb = []
            for t in text_list:
                try:
                    with httpx.Client(timeout=_EMBED_TIMEOUT) as c2:
                        r2 = c2.post(f"{self.base_url}/api/embed", json={"model": model, "input": t})
                except httpx.HTTPError as e:
                    raise _conn_err(self.base_url, e) from e
                if r2.status_code != 200:
                    raise OllamaError(f"Ollama /api/embed 返回 {r2.status_code}")
                one = (r2.json().get("embeddings") or [[]])[0]
                emb.append(one)
        if emb:
            settings.embed_dim = len(emb[0])
        return emb

    def embed_one(self, text: str, model: str | None = None) -> list[float]:
        out = self.embed([text], model=model)
        return out[0] if out else []

    # ---------- 对话(流式) ----------
    def chat_stream(self, messages: list[dict], model: str | None = None,
                    temperature: float | None = None, max_tokens: int | None = None,
                    keep_alive: str | None = None) -> Iterator[str]:
        """流式生成(全局串行:一次仅一个对话流;生成期间新请求排队)。"""
        if not _chat_semaphore.acquire(timeout=_LOCK_WAIT):
            raise OllamaBusy("已有回答正在生成,请稍候再试。")
        try:
            yield from self._chat_stream_unlocked(messages, model, temperature, max_tokens, keep_alive)
        finally:
            _chat_semaphore.release()

    def _chat_stream_unlocked(self, messages: list[dict], model: str | None,
                              temperature: float | None, max_tokens: int | None,
                              keep_alive: str | None) -> Iterator[str]:
        model = model or settings.chat_model
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "keep_alive": keep_alive or settings.ollama_keep_alive,
            "options": {
                "temperature": settings.temperature if temperature is None else temperature,
                "num_predict": settings.max_tokens if max_tokens is None else max_tokens,
            },
        }
        try:
            with httpx.Client(timeout=_CHAT_TIMEOUT) as client:
                with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as resp:
                    if resp.status_code != 200:
                        body = resp.read().decode("utf-8", "replace")
                        raise OllamaError(f"Ollama /api/chat 返回 {resp.status_code}: {body[:300]}")
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if obj.get("error"):
                            raise OllamaError(str(obj["error"]))
                        delta = obj.get("message", {}).get("content", "")
                        if delta:
                            yield delta
                        if obj.get("done"):
                            return
        except httpx.HTTPError as e:
            raise _conn_err(self.base_url, e) from e

    def chat(self, messages: list[dict], model: str | None = None,
             temperature: float | None = None, max_tokens: int | None = None) -> str:
        parts: list[str] = []
        started = time.time()
        for chunk in self.chat_stream(messages, model=model, temperature=temperature, max_tokens=max_tokens):
            parts.append(chunk)
            if time.time() - started > 1500:
                break
        return "".join(parts)


ollama = OllamaClient()
