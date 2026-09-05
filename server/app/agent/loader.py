import asyncio
import importlib
import inspect
import logging

from pydantic import BaseModel

from app.agent.base import (
    Agent,
    AnalyzeInput,
    AnalyzeResult,
    ChatInput,
    ChatResult,
    ExplainInput,
    ExplainResult,
    JudgeInput,
    JudgeResult,
    WriteInput,
    WriteResult,
)
from app.config import get_settings


def load_agent_class(path: str) -> type:
    if ":" not in path:
        raise ImportError(f"AGENT_IMPL 형식은 'pkg.module:Class' 이어야 해요: {path!r}")
    module_name, _, class_name = path.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as e:
        raise ImportError(f"AGENT_IMPL 모듈을 찾을 수 없어요: {module_name}") from e
    cls = getattr(module, class_name, None)
    if cls is None:
        raise ImportError(f"AGENT_IMPL 클래스를 찾을 수 없어요: {path}")
    return cls


log = logging.getLogger(__name__)

_RESULT_TYPES = {"analyze": AnalyzeResult, "chat": ChatResult, "judge": JudgeResult, "write": WriteResult, "explain": ExplainResult}


class AgentAdapter:
    """AI 담당 구현체를 감싼다. 동기 함수면 스레드에서 돌리고, 반환값을 Pydantic으로 검증한다.

    계약 (자세히는 docs/agent-interface.md):
    - `judge` 는 `basis.precedents[].body_text`(H37 판례 팝업 본문)를 채워서 돌려줘야 한다.
      백엔드는 그 값을 판정에 저장하고, E-2(`GET /precedents/{id}`)는 저장값만 읽는다.
    - `explain` 은 선택 구현이고 백엔드는 부르지 않는다. 판정 시점에 `body_text` 가 비면 채울 기회가 없다.
    """

    def __init__(self, impl) -> None:
        self._impl = impl

    async def _call(self, name: str, inp: BaseModel):
        fn = getattr(self._impl, name, None)
        if fn is None:
            raise NotImplementedError(f"Agent에 {name}() 이 없어요")
        if inspect.iscoroutinefunction(fn):
            raw = await fn(inp)
        else:
            raw = await asyncio.to_thread(fn, inp)
        result_type = _RESULT_TYPES[name]
        if isinstance(raw, result_type):
            return raw
        if isinstance(raw, BaseModel):
            raw = raw.model_dump()
        return result_type.model_validate(raw)

    async def analyze(self, inp: AnalyzeInput) -> AnalyzeResult:
        return await self._call("analyze", inp)

    async def chat(self, inp: ChatInput) -> ChatResult:
        return await self._call("chat", inp)

    async def judge(self, inp: JudgeInput) -> JudgeResult:
        return await self._call("judge", inp)

    async def write(self, inp: WriteInput) -> WriteResult:
        return await self._call("write", inp)

    async def explain(self, inp: ExplainInput) -> ExplainResult:
        return await self._call("explain", inp)


_agent: Agent | None = None


def get_agent() -> Agent:
    global _agent
    if _agent is None:
        path = get_settings().agent_impl
        cls = load_agent_class(path)
        _agent = AgentAdapter(cls())
        # 기동 로그에서 어떤 구현체가 붙었는지 바로 보이게 한다 (.env 의 Mock 이 명령줄 환경변수로 덮였는지 확인용)
        kind = "MOCK (정해진 시나리오로 답함)" if path == "app.agent.mock:MockAgent" else "REAL"
        log.info("agent impl: %s → %s.%s [%s]", path, cls.__module__, cls.__name__, kind)
    return _agent


def reset_agent() -> None:
    global _agent
    _agent = None
