# Car-Defender AI

`car_defender_ai_development_guide.md`의 3-Agent 구조(Master Accident Agent / Video Analysis Agent / Document Agent + Similar Case RAG Tool)를 프론트·백엔드와 독립적으로 구현한 모듈이다.

```text
                         User
                           │
                           ▼
                ┌────────────────────┐
                │ Master Accident    │  agents/master_agent.py  (GPT)
                │ Agent              │  Case State 소유 · 질문 · 판정 · 라우팅
                └─────────┬──────────┘
                          │
          ┌───────────────┼────────────────┐
          ▼               ▼                ▼
 ┌────────────────┐  ┌──────────────┐  ┌────────────────┐
 │ Video Analysis │  │ Similar Case │  │ Document Agent │
 │ Agent          │  │ RAG Tool     │  │ (GPT)          │
 │ Gemini / GPT   │  │ rag/tool.py  │  │ agents/        │
 │ agents/video_  │  └──────────────┘  │ document_agent │
 │ agent.py       │                    └────────────────┘
 └────────────────┘
```

## 디렉터리

| 경로 | 역할 |
| --- | --- |
| `settings.py` | `.env` 기반 모델·임계값·경로 설정 (하드코딩 금지) |
| `config/prompt_versions.json` | Prompt 버전 선택 |
| `prompts/{video_agent,master_agent,document_agent}/*_v1.md` | 5요소(Role/Goal/Allowed Inputs/Rules/Output Schema) System·Task Prompt |
| `prompts/loader.py` | Prompt 로딩, `{{placeholder}}` 렌더링, 사용자 입력 태그 무력화 |
| `state/case_state.py` | Case State 스키마 (Fact source 분리: video / user / rag / model_inference) |
| `state/updater.py` | 영상 → State, 사용자 사실 → State, 충돌 기록, 판정 무효화, 차량 역할 매핑 |
| `video/schemas.py` | backend 공통 Video Result (vehicles / collision_window / collision_pair / pair_scores / timeline …) |
| `video/base.py` | `BaseVideoAnalyzer`, `get_video_analyzer(backend)` |
| `video/gemini_analyzer.py` | Path A — Gemini Native Video (MP4 직접 입력) |
| `video/gpt_frames_analyzer.py` | Path B — GPT Vision Frames (0.5s 샘플링 → 충돌 구간 0.1s dense 재추출) |
| `video/frame_sampler.py` | ffmpeg 기반 timestamped 프레임 추출 |
| `video/validation.py` | Fast Path 검증, Completion Gate(4조건 + score), focus target 생성, 사용자 확인 질문 |
| `video/merge.py` | 1차 + focus 재분석 결과 병합 |
| `video/cache.py` | video_hash + model + prompt_version 캐시 |
| `video/tracking.py` | (선택) YOLO+ByteTrack CV tracking fallback |
| `agents/video_agent.py` | Fast Path → Focus Re-analysis → CV → 사용자 객관적 확인 정책 |
| `case/extractor.py` | 사용자 메시지 → 객관적 사실 추출 (의견·의도 추정 제외, 규칙 기반 fallback) |
| `case/sufficiency.py` | 정보 충분성 (deterministic 체크리스트 + LLM 보강) |
| `case/questions.py` | 객관적 추가 질문 생성 (턴당 ≤3, 주관 질문 필터) |
| `rag/case_documents.py` | 심의번호 기준 Case-level Document (사고내용/결정비율/기본비율/쟁점/결정이유/수정요소/metadata) |
| `rag/query_builder.py` | structured + detailed RAG query |
| `rag/retriever.py` | hybrid(dense+lexical) 두 query RRF 융합 + metadata 필터 |
| `rag/reranker.py` | 사고 구조 요소별 LLM 검증 (matched/different factors, primary reference) |
| `rag/tool.py` | `SimilarCaseRagTool.search(state)` — 심의사례 PDF 우선 검색·검증 → 없을 때만 인정기준 PDF fallback, 최종 최대 3개 (`RAG_FINAL_TOP_K`) |
| `assessment/fault_ratio.py` | 판정 전제조건, 예상 과실비율(근거·범위·신뢰도·불확실성), provisional 처리 |
| `document/package.py` | Master → Document 전달용 검증 패키지 |
| `document/incident_report.py`, `document/rebuttal.py` | 사건경위서 / 반박의견서 |
| `document/grounding.py` | 근거 없는 속도 수치·의도 추정·미제공 심의번호 제거 |
| `agents/master_agent.py` | 멀티턴 orchestration (LLM 판단 + 코드 레벨 guardrail) |
| `service/interface.py` | `create_case / chat / assess_fault / generate_incident_report / generate_rebuttal` |
| `eval/` | Collision Pair Accuracy 등 지표, Gemini vs GPT Frames 비교 실험 |
| `telemetry.py` | 호출 로그 (agent, model, prompt_version, latency, token_usage → `logs/agent_runs.jsonl`) |
| `demo.py` | CLI PoC |

레거시 Intake Agent 코드(`agent/`, `schemas/`, `models/gemini_video.py`, `rag/pipeline.py`, `main.py`, `vision_intake.py`)는 그대로 유지되며, 새 구조는 `rag/pdf_index.py`의 Parent-Child 인덱스를 재사용한다.

## 실행

```bash
cd ai
cp .env.example .env   # OPENAI_API_KEY, GEMINI_API_KEY 입력
pip install -r requirements.txt

# RAG 인덱스 (최초 1회, data/text/*.pdf 필요)
python build_rag_index.py
python -c "from rag.case_documents import build_case_documents; build_case_documents()"

# PoC CLI
python demo.py --video data/mp4/bb_1_220804_vehicle_116_067.mp4 --description "회전교차로에서 택시랑 사고났어."
#   /assess  /cases  /report  /rebuttal  /state  /video  exit
python demo.py --video ... --backend gpt_frames      # Video Path B
python demo.py --video ... --no-llm                  # 규칙 기반 오프라인 점검
```

Python API:

```python
from service.interface import create_case, chat, assess_fault, generate_incident_report, generate_rebuttal

res = create_case("sample.mp4", "사거리에서 직진하다가 사고났어.")   # → AgentResponse(action="ASK_USER", ...)
res = chat(res.case_id, "내 차 블랙박스야.")                       # → SHOW_FAULT_ASSESSMENT
assessment = assess_fault(res.case_id)                              # → FaultAssessment
report = generate_incident_report(res.case_id)                      # → DocumentResult
rebuttal = generate_rebuttal(res.case_id, opponent_claim="상대 보험사는 50:50 주장")
```

## 대화 흐름 (Master Agent)

```text
영상 분석 → 영상으로 알 수 없는 객관적 질문(소유 관계·일시) → 답변 반영
  → 유사 심의사례 검색 (심의사례 PDF 우선, 최대 3개) → 사례 제시 + 차이·수정요소 확인 질문 (CASE_REVIEW)
  → 답변/모름/없음 → Agent가 "판정 준비 완료" 판단 → 심의사례 + 사고 사실 종합 판정
  → 후속 질문 답변 / 새 중요 사실이면 재판정 → 사건경위서 → 반박의견서
```

- 질문은 한 턴에 하나(`MAX_QUESTIONS_PER_TURN=1`). 미리 정해진 질문 목록은 없다. Agent(LLM)가 사건 상태와 영상 미확인 항목만 보고 "이 사고 구조에서 판정에 영향을 주는데 확인되지 않은 요소"를 먼저 추론(`reasoning`)한 뒤 하나를 고르고, "확인 이유"를 함께 보여준다 (`prompts/master_agent/followup_question_v4.md`, `case_review_questions_v2.md`). 추론 과정은 응답 `data.agent_reasoning`과 `logs/agent_runs.jsonl`에 남는다. 코드는 guardrail만 담당한다: 영상 소유 관계는 반드시 먼저, 주관 질문·중복·영상으로 재확인 가능한 항목 금지, LLM 호출 실패 시에만 코드 후보(`DEFAULT_QUESTIONS`)로 대체. Agent가 "더 물을 것이 없다"고 판단하면 억지로 채우지 않는다. 심의사례 검색 전 Agent 질문은 `MAX_FACT_QUESTION_ROUNDS`(2)회까지.
- 영상 호출 최소화(가이드 63절): 1차 분석에서 충돌 pair가 확정되면 **Master Agent가 1차 결과를 읽고 "무엇이 부족한지" 추론해 2차 분석 지시서를 쓰고**(`prompts/master_agent/video_gap_plan_v1.md`: 다시 볼 항목 + 시간 구간 + 목표 필드 + 충돌 직전 서술 보완), Video Agent가 그 지시서로 **딱 한 번** 더 본다. 지시서에서 이미 CONFIRMED이거나 화각 밖인 항목은 코드가 제거하고, LLM 실패 시 코드 규칙(`build_factor_sweep_focus`)이 대신 지시서를 만든다. 그래서 일반적인 사건은 영상 호출 2회가 모두 첫 턴에 끝나고, 대화 중에는 영상을 다시 부르지 않는다. 2차 분석의 상세 서술은 Case State·판정·사건경위서 입력에 포함된다. `VIDEO_FACTOR_SWEEP=false`로 끌 수 있다.
- 대화 중 미확인 요소가 화면에 찍히는 것(노면 실선/점선, 정지선, 본인 방향 신호, 차로 위치, 제동, 충돌 부위)이면 사용자에게 묻지 않는다. sweep이 이미 본 주제면 그 결과를 쓰고, 새 쟁점이면 Agent가 `video_recheck_targets`로 focus 재분석을 요청한다(사건당 `MASTER_MAX_RECHECKS`=1회). 코드 guardrail(`case/questions.py::classify_video_topic`)이 LLM이 잘못 물으려 해도 재분석/폐기로 돌리고, 영상 분석이 "화각 밖/보이지 않음"이라 명시했거나 재분석 후에도 미확인이면 그때 사용자에게 묻는다.
- 심의사례 제시 후 검토 질문은 최대 `MAX_REVIEW_ROUNDS`(3)회이며, Agent가 더 물을 것이 없다고 판단하면 바로 판정한다.
- 판정 기준값(anchor) = 가장 유사한 심의사례의 결정비율. 현재 사건에서 VIDEO/USER_CONFIRMED로 확인된 수정요소가 있을 때만 벗어나며, 확인되지 않은 차이는 범위(possible_range)로만 제시한다. 코드 레벨 guardrail(`assessment/fault_ratio.py::_enforce_anchor`)이 이를 강제한다.
- 사용자가 "몇 대 몇이야"처럼 명시적으로 요청하면 검토 단계를 건너뛰고 즉시(필요 시 provisional로) 판정한다.

## Structured Output 메모

- OpenAI: `response_format=json_schema(strict=false)` → 실패 시 `json_object` + prompt schema. 항상 로컬 pydantic 검증 + 1회 repair.
- Gemini: `response_json_schema`는 schema 복잡도 제한이 있어 전체 Video schema(약 250 properties)를 400으로 거부한다(개별 sub-schema는 통과). 기본값은 JSON mode + prompt schema + 로컬 검증(`GEMINI_REMOTE_SCHEMA=false`)이며, 켜 두면 거부 시 자동으로 prompt schema로 전환된다.
- Video schema는 `LenientModel`(null 키 제거) 기반이다. Gemini가 비당사 차량의 Observation 필드를 `null`로 보내는 일이 잦은데, 이를 거부하면 repair 재호출(약 30초)이 매번 발생했다. 파싱 실패 원문은 `logs/raw_responses/`에 남는다.
- 충돌 pair 검증에 실패한 1차 영상 결과는 캐시하지 않는다(나쁜 결과 고정 방지).

## 테스트 / 평가

```bash
cd ai
python -m pytest -q                       # 네트워크 없이 Fake client로 전체 흐름 검증
python -m eval.compare_backends --dataset data/eval/video_annotations.json --backends gemini_native gpt_frames
```

`eval/dataset_schema.py`의 annotation 형식(collision_pair, collision_timestamp_sec, road_type …)으로 20~50개 영상을 라벨링하면 Collision Pair Accuracy / Identity Consistency / Timestamp Error / Latency를 backend별로 비교한다.
