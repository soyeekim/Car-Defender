"""Prompt 파일 로더 및 버전 관리.

Prompt는 `prompts/<agent>/<name>_<version>.md` 파일로 관리한다.
버전은 `config/prompt_versions.json`의 `<agent_short>_<name>` 키로 선택한다.

플레이스홀더는 `{{name}}` 형식을 사용한다. Prompt 본문에 JSON 예시가 많아
`str.format`의 중괄호 충돌을 피하기 위함이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from settings import PROMPTS_DIR, get_settings

_AGENT_SHORT = {
    "video_agent": "video",
    "master_agent": "master",
    "document_agent": "document",
}
_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
_USER_TAG_PATTERN = re.compile(
    r"</?\s*(USER_MESSAGE|USER_CASE_DESCRIPTION|FOCUS|CASE_STATE|RETRIEVED_CASES|VERIFIED_CASE_STATE|FAULT_ASSESSMENT"
    r"|SIMILAR_CASES|OPPONENT_CLAIM|INCIDENT_REPORT|PREVIOUS_DRAFT|REVISION_REQUEST)\s*>",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Prompt:
    agent: str
    name: str
    version: str
    text: str
    path: Path

    @property
    def version_id(self) -> str:
        return f"{self.agent}/{self.name}_{self.version}"

    @property
    def task(self) -> str:
        return f"{_AGENT_SHORT.get(self.agent, self.agent)}_{self.name}"

    def render(self, **values: Any) -> str:
        return render_template(self.text, **values)

    def placeholders(self) -> list[str]:
        return sorted(set(_PLACEHOLDER.findall(self.text)))


def render_template(template: str, **values: Any) -> str:
    def _replace(match: re.Match) -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError(f"Prompt placeholder '{key}'에 대한 값이 없습니다.")
        value = values[key]
        return "" if value is None else str(value)

    return _PLACEHOLDER.sub(_replace, template)


def sanitize_user_text(text: Optional[str]) -> str:
    """사용자 입력은 데이터로만 취급한다. Prompt 태그 흉내를 무력화한다."""
    if not text:
        return ""
    cleaned = _USER_TAG_PATTERN.sub(lambda m: m.group(0).replace("<", "‹").replace(">", "›"), text)
    return cleaned.strip()


def wrap_user_text(text: Optional[str], tag: str = "USER_MESSAGE") -> str:
    return f"<{tag}>\n{sanitize_user_text(text)}\n</{tag}>"


def resolve_version(agent: str, name: str, version: Optional[str] = None) -> str:
    if version:
        return version
    key = f"{_AGENT_SHORT.get(agent, agent)}_{name}"
    return get_settings().prompt_versions.get(key, "v1")


@lru_cache(maxsize=128)
def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_prompt(agent: str, name: str, version: Optional[str] = None) -> Prompt:
    resolved = resolve_version(agent, name, version)
    path = PROMPTS_DIR / agent / f"{name}_{resolved}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Prompt 파일을 찾을 수 없습니다: {path}")
    return Prompt(agent=agent, name=name, version=resolved, text=_read(path), path=path)


def list_prompts() -> list[Prompt]:
    prompts = []
    for agent_dir in sorted(PROMPTS_DIR.iterdir()):
        if not agent_dir.is_dir() or agent_dir.name not in _AGENT_SHORT:
            continue
        for path in sorted(agent_dir.glob("*_v*.md")):
            stem = path.stem
            name, _, version = stem.rpartition("_")
            prompts.append(
                Prompt(agent=agent_dir.name, name=name, version=version, text=_read(path), path=path)
            )
    return prompts
