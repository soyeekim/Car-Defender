"""백엔드(server/) 연동 진입점 — `AGENT_IMPL=ai.agent.real:RealAgent`.

ai/ 내부 모듈은 ai/ 를 import root로 쓴다(`from agents.master_agent import ...`). 백엔드는 저장소 루트를
root로 두고 `ai.agent.real` 을 import 하므로, 여기서 ai/ 를 sys.path 에 넣어 두 경로가 같은 모듈을 보게 한다.
(ai/agents/ 는 3-Agent 구현, ai/agent/ 는 백엔드 계약 어댑터다.)
"""

from __future__ import annotations

import sys
from pathlib import Path

_AI_ROOT = str(Path(__file__).resolve().parents[1])
if _AI_ROOT not in sys.path:
    sys.path.insert(0, _AI_ROOT)
