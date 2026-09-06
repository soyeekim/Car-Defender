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
| `agents/master_agent.py` | 멀티턴 orchestration (LLM 판단 + 코드 레벨 guardrail). `defer_conclusions=True`면 판정·문서를 직접 하지 않고 `REQUEST_ASSESSMENT` / `REQUEST_DOCUMENT`로 넘긴다(서버 모드) |
| `service/interface.py` | CLI/단독 사용 인터페이스 `create_case / chat / assess_fault / generate_incident_report / generate_rebuttal` |
| `agent/real.py` | **백엔드 계약 구현체** `AGENT_IMPL=ai.agent.real:RealAgent` (`analyze / chat / judge / write / explain`) — 아래 "백엔드 연동" 참고 |
| `agent/codec.py`, `agent/cache.py`, `agent/presenters.py` | Case State ↔ `facts` 직렬화, judge 상세 임시 보관(/tmp), 화면용 해요체 문구·계약 모델 매핑 |
| `eval/` | Collision Pair Accuracy 등 지표, Gemini vs GPT Frames 비교 실험 |
| `telemetry.py` | 호출 로그 (agent, model, prompt_version, latency, token_usage → `logs/agent_runs.jsonl`) |
| `demo.py` | CLI PoC |

`rag/pdf_index.py`의 Parent-Child 인덱스(심의번호 기준)는 이전 코드에서 이어받아 그대로 쓴다. `agents/`(복수)는 3-Agent 구현, `agent/`(단수)는 백엔드 계약 어댑터다.

## 백엔드(server/) 연동

백엔드는 `server/docs/agent-interface.md` 계약대로 `AGENT_IMPL`이 가리키는 클래스를 같은 프로세스에서 부른다. 별도 서버·HTTP 없음.

```bash
cd server
cp .env.example .env     # AGENT_IMPL=ai.agent.real:RealAgent, OPENAI_API_KEY, GEMINI_API_KEY 채우기 (ai/.env 가 있으면 그 값도 읽는다)
.venv/bin/python -m pip install -e ".[dev]" -r ../ai/requirements.txt
AGENT_IMPL=ai.agent.real:RealAgent PYTHONPATH=.. .venv/bin/python -m uvicorn app.main:app --port 8000
# 계약 테스트 (실제 API·영상)
AGENT_CONTRACT_IMPL=ai.agent.real:RealAgent AGENT_CONTRACT_VIDEO=../ai/data/mp4/bb_1_220804_vehicle_116_067.mp4 .venv/bin/python -m pytest tests/test_agent_contract.py -v
# 프론트 없이 챗봇 써 보기 (서버를 띄운 채 다른 터미널에서; /help 로 명령어 확인)
.venv/bin/python scripts/chat_cli.py --video ../ai/data/mp4/bb_1_220804_vehicle_116_067.mp4
```

심의사례 PDF 텍스트는 `rag/pdf_layout.py` 가 글자 좌표(`pdftotext -bbox-layout`)로 '사례 개요' 표(2단)와 주장 내용(2단)을 칸 단위로 다시 읽는다 — 일반 추출은 왼쪽 라벨(사고내용·참고·인정기준)을 문장 중간에 끼워 넣어 226건 중 209건의 사고내용이 잘려 있었다(2026-09-06 수정, INDEX_VERSION 5). 단어 중간 줄바꿈은 `common/korean_text.py` 의 낱말 사전으로 붙인다.

인정기준·회전교차로 비정형기준 PDF 의 도표는 `rag/chart_layout.py` 가 같은 방식으로 읽는다(INDEX_VERSION 6): 배지(차3-2·회전-3·보1)를 찾아 제목 띠, A·B 역할, 변형별 기본비율("(가) A 40 : B 60"), 수정요소 행("- A 현저한 과실 +10"), 옛 도표 번호(舊 216)를 표 구조로 뽑고, 세로 라벨("과실비율 조정예시")·머리글·그림 글자는 버린다. `rag/case_documents.parse_standard_parent` 가 이를 `role_a/role_b`, `chart_variants`, `chart_modifiers`(쪽·항목·±값) 로 구조화한다. 일반 추출 때는 도표 180건 중 165건의 제목이 머리글이었고 수정요소 행에 세로 글자가 섞여 있었다.

**도표 경로 판정(심의사례가 없을 때)** — `rag/tool.py` 는 도표도 리랭커 검증(`min_case_relevance`)을 넘어야 기준으로 삼고, 넘는 것이 없으면 `tier="none"` 으로 끝낸다(엉뚱한 표를 기준값으로 잡지 않는다). 기준이 도표이면 `assessment/chart_calculator.py` 가 판정한다: agent(프롬프트 `chart_assessment`)는 어느 도표·변형이 맞는지, 사건의 나·상대가 A·B 중 누구인지, 확인된 사실(VIDEO/USER_CONFIRMED)로 적용되는 수정요소 행(id)만 고르고, 코드가 기본비율에 그 행의 ±값을 더하고 빼서(0~100 고정) 나·상대로 옮긴다. 계산식은 `FaultAssessment.calculation`("기본 (나) A 30 : B 70 → B 중대한 과실 +20 → A 10 : B 90 → 나(B) 90 : 상대(A) 10")에 남아 판정 카드·팝업에 그대로 보인다. 참고 기준이 하나도 없으면 일반 원칙만으로 `provisional`(신뢰도 ≤ 0.4) 판정을 내고 사례 번호를 인용하지 않는다.

인덱스를 다시 만들 때(`python build_rag_index.py --force`) 글이 그대로인 Child 는 기존 임베딩 벡터를 재사용하므로, 도표만 고쳐도 심의사례 검색 결과는 수치까지 그대로다.

심의사례 팝업 그림: `cd ai && python build_case_images.py` 가 원본 PDF 3종에서 표를 잘라 `data/rag_index/images/<심의번호|도표번호>.png` 와 `manifest.json` 을 만든다
(`rag/case_images.py`, poppler-utils 필요). 백엔드는 manifest 만 읽어 `GET /api/v1/precedents/{id}/image` 로 내려준다 — RAG 인덱스처럼 **Docker 빌드 전에 한 번 만들어 둔다**.
프론트 명세: `server/docs/precedent-image-spec.md`.

| 계약 메서드 | 여기서 하는 일 |
| --- | --- |
| `analyze(video_path, description)` | `MasterAccidentAgent.create_case` — 영상 1차 분석 → Agent가 부족한 점을 추론해 2차 분석 → 설명에서 사실 추출 → 첫 질문 하나(`questions[0]`). 물을 것이 없으면 유사 심의사례까지 `summary_text`에 담고 `questions: []`(백엔드가 바로 judge). `facts["_case_state"]`에 Case State 전체를 직렬화해 돌려준다. |
| `chat(messages, new_message, facts, verdict…)` | `facts["_case_state"]`를 복원하고 대화를 한 턴 진행. 판정 준비가 끝나면 `next_action: verdict`(기존 판정이 있으면 `rejudge`), 문서 요청은 `create_report` / `create_rebuttal`. `fact_updates["_case_state"]`로 상태를 다시 저장한다. |
| `judge(messages, facts, previous_verdict)` | 검색된 심의사례(없으면 검색) + 사실을 종합해 판정(`MasterAccidentAgent.assess`). 판정 카드 `summary`, 재판정 `change_reason`, `basis.chart`, 판례 팝업 `body_text`(해요체)를 채운다. 상세는 `/tmp/car_defender_agent/`에 남겨 다음 chat/write가 되살린다. |
| `write(kind, …)` | report: 명세서 3.1의 4개 섹션(일시·장소/사고 경위/영상 분석 결과/주장 요지) + `caveat` + `page_count`, `revision_request`/`previous_sections` 반영. rebuttal: 메일 본문 `body`(5000자 이내), `report_sections` 참고. |

단위 테스트(네트워크 없음): `python -m pytest tests/test_server_adapter.py`.

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
  → Agent가 더 물을 것이 없으면 "추가로 알려주실 정황이 있나요?" (열린 질문 1회) → 없으면 "예상 과실비율을 판정해 드릴까요?" 제안 (READY_FOR_RAG)
  → 사용자가 "예상 과실비율 판정해줘"(또는 "네")라고 하면 그때 유사 심의사례 검색 (심의사례 PDF 우선, 최대 3개) + 종합 판정을 한 턴에
  → 답변/모름/없음 → Agent가 "판정 준비 완료" 판단 → 심의사례 + 사고 사실 종합 판정
  → 후속 질문 답변 / 새 중요 사실이면 재판정 → 사건경위서 → 반박의견서
```

- 사용자에게 보이는 글은 모두 해요체다(`prompts/master_agent/system_v2.md` [TONE]). 사건경위서만 제출 문서라 합니다체·객관 서술, 반박의견서 메일 본문은 정중한 합니다체.
- 반박의견서 메일은 **본인 가입 보험사** 담당자에게 보내는 글이다. 본문은 `document/rebuttal.py::build_mail_body` 가 코드로 조립한다 — 주장 비율(제시된 비율 / 본인 주장 비율 / 요청) → 근거(영상 확인 사실 · 유사 심의사례 3건 요약 · 수정요소) → 결론 → 첨부. 확정된 사실과 검색된 심의사례만 쓰고, "불명확하니 확인이 필요하다"류의 문장은 넣지 않는다(받는 쪽이 확인할 수 없는 내용). LLM 은 검토용 10개 섹션만 쓴다(`prompts/document_agent/rebuttal_opinion_v3.md`).
- 질문은 한 턴에 하나(`MAX_QUESTIONS_PER_TURN=1`). 미리 정해진 질문 목록은 없다. Agent(LLM)가 사건 상태와 영상 미확인 항목만 보고 "이 사고 구조에서 판정에 영향을 주는데 확인되지 않은 요소"를 먼저 추론(`reasoning`)한 뒤 하나를 고르고, "확인 이유"를 함께 보여준다 (`prompts/master_agent/followup_question_v6.md`, `case_review_questions_v4.md`). 추론 과정은 응답 `data.agent_reasoning`과 `logs/agent_runs.jsonl`에 남는다. 코드는 guardrail만 담당한다: 영상 소유 관계는 반드시 먼저, 주관 질문·중복·영상으로 재확인 가능한 항목 금지, LLM 호출 실패 시에만 코드 후보(`DEFAULT_QUESTIONS`)로 대체. Agent가 "더 물을 것이 없다"고 판단하면 억지로 채우지 않는다. 심의사례 검색 전 Agent 질문은 `MAX_FACT_QUESTION_ROUNDS`(3)회까지. Agent 가 재분석만 고르고 질문을 비웠는데 재분석 예산이 없으면 "재분석 불가"를 알리고 사용자에게 물을 것을 다시 고르게 한다(`followup_question_v7`: 신호·우선권 / 진입·속도 / 등화·차선 / 운전자 상태 / 도로 환경 / 사고 후 사실 여섯 갈래를 훑고, 사용자만 아는 것을 하나 묻는다).
- 영상 호출 최소화(가이드 63절): 1차 분석에서 충돌 pair가 확정되면 **Master Agent가 1차 결과를 읽고 "무엇이 부족한지" 추론해 2차 분석 지시서를 쓰고**(`prompts/master_agent/video_gap_plan_v1.md`: 다시 볼 항목 + 시간 구간 + 목표 필드 + 충돌 직전 서술 보완), Video Agent가 그 지시서로 **딱 한 번** 더 본다. 지시서에서 이미 CONFIRMED이거나 화각 밖인 항목은 코드가 제거하고, LLM 실패 시 코드 규칙(`build_factor_sweep_focus`)이 대신 지시서를 만든다. 그래서 일반적인 사건은 영상 호출 2회가 모두 첫 턴에 끝나고, 대화 중에는 영상을 다시 부르지 않는다. 2차 분석의 상세 서술은 Case State·판정·사건경위서 입력에 포함된다. `VIDEO_FACTOR_SWEEP=false`로 끌 수 있다.
- focus 재분석은 전체 영상을 다시 보내지 않고 충돌 시각 기준으로 잘라 보낸다(`video/clip.py`, ffmpeg 재인코딩이라 시작점이 정확하다). 충돌 순간만 보면 되는 재분석(충돌 차량 조합·부위)은 앞뒤 1초(`VIDEO_CLIP_IMPACT_PRE_ROLL/POST_ROLL`), 과실 요소(방향지시등·차선·진입 순서·제동)를 보는 재분석은 충돌 4초 전부터 1초 후까지(`VIDEO_CLIP_FACTOR_PRE_ROLL/POST_ROLL`) — 그 사실들은 충돌 몇 초 전에 찍힌다. 모델은 클립 기준(00:00.0부터)으로 시각을 적고, 코드가 지시서·이전 결과의 시각을 클립 기준으로 넣었다가 응답의 시각을 원본 기준으로 되돌린다(`shift_data`). 클립이 영상 전체와 같으면 자르지 않는다. `VIDEO_CLIP_FOCUS=false` 로 끌 수 있다.
- 대화 중 미확인 요소가 화면에 찍히는 것(노면 실선/점선, 정지선, 본인 방향 신호, 차로 위치, 제동, 충돌 부위)이면 사용자에게 묻지 않는다. sweep이 이미 본 주제면 그 결과를 쓰고, 새 쟁점이면 Agent가 `video_recheck_targets`로 focus 재분석을 요청한다(사건당 `MASTER_MAX_RECHECKS`=1회). 코드 guardrail(`case/questions.py::classify_video_topic`)이 LLM이 잘못 물으려 해도 재분석/폐기로 돌리고, 영상 분석이 "화각 밖/보이지 않음"이라 명시했거나 재분석 후에도 미확인이면 그때 사용자에게 묻는다.
- 심의사례는 사용자가 판정을 요청한 시점에 찾아 한 줄씩 보여주고 바로 판정한다 (사례를 먼저 늘어놓고 검토 질문을 이어가던 CASE_REVIEW 단계는 2026-09-06 에 없앴다). 판정 카드의 '근거'와 심의사례 팝업이 같은 사례를 보여준다.
- 직전 질문에 답을 못 받으면(딴 얘기·되묻기) 한 번 더 묻고, 두 번째도 답이 없으면 미확인으로 두고 넘어간다 (`MAX_ASK_COUNT`=2).
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
