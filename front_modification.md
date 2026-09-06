# 프론트 수정 요청 목록

AI·백엔드 쪽을 고치면서 프론트에도 손이 가야 하는 것을 여기에 모은다. 프론트 담당이 직접 고친다.
위가 최신이다. 끝난 항목은 `[x]`, 남은 항목은 `[ ]`.

기준 브랜치: `car-defender` 저장소 `main` (2026-09-06 확인)

---

## 2026-09-06 · 안내 카드(GuideCard) 문장별 줄바꿈

"온점 뒤 줄바꿈" 규칙이 서버 글에는 적용됐지만, 첫 안내 카드는 프론트에 하드코딩된 문장이라 예외로 남아 있었다 ("둘이" 가 윗줄, 나머지가 아랫줄로 잘림).
서버 상수(`app/content/texts.py`)도 같은 규칙으로 고쳤다 (영상 받음 안내, 반박의견서 잠금 카드, 발송 안내, 첨부 고지).

| 상태 | 위치 | 고칠 것 |
|---|---|---|
| [ ] | `src/features/workspace/messages/GuideCard.tsx` 27~29행 `<AiText>` | 문장마다 줄을 나눈다. 예: `안녕하세요, {APP_NAME}예요.{'\n'}사고 상황을 말로 설명하고, 블랙박스 영상을 올려 주세요.{'\n'}둘이 모이면 분석이 자동으로 시작돼요.` (`AiText` 가 `whitespace-pre-line` 이면 `\n` 으로 충분, 아니면 `<br />`) |
| [ ] | 같은 파일 `<AiNote>` | `영상은 이 사건 처리에만 쓰이며, 사건을 지우면 함께 지워집니다.` 뒤에 줄바꿈, 그 아래 `{VIDEO_LIMITS.acceptLabel}` |
| [ ] | `src/api/mock/index.ts` 341행 | 목 문구도 `영상 잘 받았어요.\n사고 상황을 …` 로 (서버와 동일) |

---

## 2026-09-06 · 반박의견서 메일 본문 — 제목 줄 굵게

메일 본문(`mail_body`, 반박의견서 카드와 미리보기)은 평문이라 서버가 굵기를 줄 수 없다. 대신 구조를 줄 규칙으로 고정했으니 프론트가 제목 줄만 굵게 그리면 된다.

- 번호 제목: `^\d+\. ` 로 시작하는 줄 (`1. 주장하는 과실비율`, `2. 주장 근거`, `3. 결론`) → **굵게**, 위에 빈 줄 하나 (서버가 넣어 준다)
- 소제목: `^[가-라]\. ` 로 시작하는 줄 (`가. 블랙박스 영상에서 확인된 사실`, `나. 유사 심의사례`, `다. 과실 수정요소`) → **굵게(semibold)**
- 항목: `^- ` 줄은 그대로, 들여쓴 `^  ` 줄(사고 개요·심의 이유·본 사고와의 공통점)은 `text-muted`
- 본문은 `whitespace-pre-line` 으로 그린다 (이미 적용된 규칙과 같다)

| 상태 | 위치 | 고칠 것 |
|---|---|---|
| [ ] | 반박의견서 카드 / 메일 미리보기 | 줄 단위로 나눠 위 정규식에 맞는 줄에 `font-semibold` |

---

## 2026-09-06 · 심의사례 팝업 · 판정 카드 — 인정기준 도표로 계산한 경우

심의사례가 하나도 맞지 않으면 AI가 **과실비율 인정기준 도표**의 기본비율에 확인된 수정요소를 더하고 빼서 판정한다. 이때 응답 형식은 그대로지만
글 안에 새 소제목과 계산식이 들어오므로 프론트가 알아볼 소제목 목록만 늘리면 된다.

### 서버가 주는 것

1. 심의사례 팝업 `bodyText` (`GET /cases/{id}/precedents`) — 도표일 때 문단 순서

```
과실비율 인정기준 도표 차12-1          ← 첫 문단(제목). 회전교차로 표는 "회전교차로 비정형기준 도표 회전-3"
사고 유형 / 차량 역할 / 사고 내용 / 적용 변형 또는 기본 과실비율 / 수정요소 / 계산 과정 / 내 사건과 비슷한 점 / 내 사건과 다른 점 / 판정에서의 역할
```

- **새 소제목 3개**: `차량 역할`(문단 2개: `A: 우측도로에서 직진`, `B: 좌측도로에서 직진`), `적용 변형`(변형이 둘 이상일 때, 문단마다 `(나) A 선진입 / B 후진입 → 기본 A:B = 30:70`), `계산 과정`(문단 1개, 예 `기본 (나) A 30 : B 70 → B 중대한 과실 +20 → A 10 : B 90 → 나(B) 90 : 상대(A) 10`).
- `기본 과실비율` 문단은 `A:B = 40:60` 형식으로 바뀌었다 (전에는 `40:60`).
- `수정요소` 문단은 행마다 `A 현저한 과실 +10` 꼴, 최대 12개.

2. 판정 카드 `basis.chart` — 도표로 계산했을 때 `note` 에 계산식이 그대로 온다 (`name` 은 `차12-1 · 우측도로 직진 대 좌측도로 직진(동일폭)`).
   심의사례로 판정했을 때는 전과 같다.
3. 참고 기준을 하나도 못 찾은 사건: `basis.chart = { name: "참고 기준 없음 · 일반 원칙", note: "꼭 맞는 심의사례나 인정기준 도표를 찾지 못해서 …" }`, `precedents` 는 빈 배열, 판정 `summary` 첫 문장이 "꼭 맞는 심의사례나 인정기준 도표를 찾지 못해서 일반 원칙으로만 본 임시 예상치예요." 이고 `provisional` 이다.

| 상태 | 위치 | 고칠 것 |
|---|---|---|
| [ ] | 심의사례 팝업 소제목 목록 (`CASE_LABELS`) | `'차량 역할', '적용 변형', '계산 과정'` 추가. 목록에 없으면 본문 문단으로 그려져 볼드가 빠진다 |
| [ ] | `계산 과정` 문단 | 한 줄이 길다 (`→` 로 이어진 계산식). 줄바꿈 허용하고 `font-mono` 또는 `→` 앞에서 줄바꿈해 주면 읽기 좋다 (선택) |
| [ ] | 판정 카드 근거 첫 줄 | `basis.chart.note` 가 길어질 수 있으니 말줄임 대신 줄바꿈 (`whitespace-pre-line`) |
| [ ] | `precedents` 가 빈 배열일 때 | 심의사례 탭에 "참고할 심의사례·도표가 없어 일반 원칙으로 낸 임시 예상치예요" 빈 상태 문구 |

---

## 2026-09-06 · 사건 현황판 — "확인된 사실" 칩

시안 오른쪽 패널의 "확인된 사실 5 / 6 · 남은 1개는 쟁점이에요" + 칩 목록. **영상 분석에서 확정된 사실만** 칩으로 만들고,
영상으로 확인하지 못한 과실 요소는 "확인 필요" 칩으로 붙는다. 사용자가 말한 사실은 넣지 않는다 (요청 범위).

### 서버가 주는 것 — `GET /cases/{id}` 응답에 `facts` 추가 (`case.updated` SSE 본문에도 같은 값)

```jsonc
"facts": {
  "confirmed": 5,
  "total": 6,
  "label": "확인된 사실 5 / 6 · 남은 1개는 쟁점이에요",   // 그대로 제목으로 쓰면 된다
  "items": [
    { "label": "회전교차로",        "source": "video",   "field": "road.road_type" },
    { "label": "2차로 직진",        "source": "video",   "field": "ego_vehicle.movement" },
    { "label": "상대 좌측 진입",    "source": "video",   "field": "other_vehicle.entry_direction" },
    { "label": "상대 1차로 차로 변경", "source": "video", "field": "other_vehicle.movement" },
    { "label": "점선 구간",         "source": "video",   "field": "road.lane_marking" },
    { "label": "상대 방향지시등 확인 필요", "source": "pending", "field": null }
  ]
}
```

- 영상 분석 전(분석 없음)에는 `facts: null`. 분석·대화·판정이 진행될 때마다 `case.updated` 로 새 값이 온다 — 패널은 그 본문으로 갱신하면 된다.
- `source` 는 지금 `video` 와 `pending` 두 가지만 온다. 나중에 사용자 진술을 넣기로 하면 `user` 가 추가된다 (범례의 "내가 말함"). 알 수 없는 값은 `video` 로 취급.
- 라벨은 16자 이내, 칩 개수는 영상 확정 최대 **12**(2026-09-06: 10 → 12, 영상 분석이 확정한 사실 문장을 줄인 라벨 칩이 추가됨 · `field: "video.confirmed_fact"`) + 확인 필요 최대 3.

| 상태 | 위치 | 고칠 것 |
|---|---|---|
| [ ] | `src/api/http/dto.ts` 사건 상세 DTO | `facts: { confirmed: number; total: number; label: string; items: { label: string; source: 'video' \| 'user' \| 'pending'; field: string \| null }[] } \| null` |
| [ ] | `src/api/http/map.ts` `toCase` | 매핑 |
| [ ] | `src/features/workspace/StatusPanel.tsx` | 제목 `label`, 범례(영상 = 보라 점, 내가 말함 = 초록 점, 확인 필요 = 노란 점), 칩 목록. `pending` 은 노란 점 + `text-warn` 계열, `video` 는 보라 점. `facts` 가 `null` 이면 이 블록을 그리지 않는다 |
| [ ] | SSE `case.updated` 처리 | 사건 상세를 교체할 때 `facts` 도 함께 교체되는지 확인 |
| [ ] | `src/api/mock/` | 목 사건에 `facts` 예시 넣기 |

---

## 2026-09-06 · 판정 카드 — "상대 보험사 주장 vs 카-디펜더 판정" 비교 막대

디자인 시안(h21)의 두 줄 막대(상대 보험사 주장 / 카-디펜더 판정)와 "↓ 상대 보험사 주장보다 내 과실이 30%p 낮게 나왔어요" 안내를 살린다.
9/3 축소 때 빠졌던 항목이지만 다시 넣기로 했다 (2026-09-06).

### 서버가 주는 것 — 판정 카드 payload (`message.type = "verdict"`) 와 `GET /cases/{id}/verdict`

```jsonc
{
  "ratio": { "mine": 40, "other": 60 },
  "opponentClaim": { "mine": 30, "other": 70 },          // 대화에서 상대 보험사 주장 비율을 말했을 때. 없으면 null
  "opponentClaimNote": "상대 보험사 주장보다 내 과실이 10%p 높게 나왔어요"   // 항상 온다 (아래 표)
}
```

| `opponentClaim` | `opponentClaimNote` | 화면 |
|---|---|---|
| `{mine, other}` | "상대 보험사 주장보다 내 과실이 N%p 낮게 나왔어요" / "…높게 나왔어요" / "상대 보험사 주장과 같은 비율이에요" | 막대 두 줄(주장 / 판정) + 아래 안내 한 줄. 낮으면 초록·↓, 높으면 경고색·↑, 같으면 중립 |
| `null` | "상대 보험사가 제시한 과실비율은 아직 없어요. 채팅으로 알려주시면 판정과 나란히 비교해 드릴게요." | 막대는 판정 한 줄만. 그 아래 이 문장을 `muted` 로 |

- 사용자가 **판정 뒤에** "상대 보험사가 30:70 이래요"라고 말하면 서버가 판정 행과 판정 카드 payload 를 갱신하고 **`message.updated`** 로 같은 `id` 의 카드를 다시 보낸다.
  프론트는 `message.updated` 를 받으면 그 카드를 교체한다 (경위서 카드와 같은 처리). 사이드 패널의 판정 요약(`GET /cases/{id}/verdict`)도 다시 받으면 같은 값이 온다.
- 비율 표기는 항상 `formatRatio()` — "나 30 : 상대 70".

| 상태 | 위치 | 고칠 것 |
|---|---|---|
| [ ] | `src/api/http/dto.ts` `VerdictPayloadDto` | `opponentClaim: {mine:number; other:number} \| null`, `opponentClaimNote: string` 추가 |
| [ ] | `src/api/http/map.ts` `toVerdict` | 두 필드 매핑 |
| [ ] | `src/features/workspace/messages/VerdictCard.tsx` | 비율 아래에 비교 막대 블록. `opponentClaim` 있으면 막대 2줄 + 안내, 없으면 안내 문장만. `RatioBar` 부품 재사용 |
| [ ] | `src/features/workspace/messages/MessageItem.tsx` (또는 SSE 처리) | `message.updated` 로 온 `verdict` 카드를 같은 `id` 로 교체하는지 확인 (지금은 `report_draft` 만 갱신한다면 `verdict` 도 포함) |
| [ ] | `src/api/mock/` | 목 판정에 `opponentClaim`, `opponentClaimNote` 넣기 |

---

## 2026-09-06 · 판정 카드 "근거" 첫 줄(인정기준 도표) 표기

카드 근거의 첫 줄은 `basis.chart` (백엔드 계약의 필수 필드)다. **심의사례를 따로 검색한 결과가 아니라, 기준으로 삼은 심의사례가 적용한
과실비율 인정기준 도표 번호**를 알려 주는 줄이다 (예: 심의사례 2018-047765 → 도표 266). 그 아래 심의사례 3건은 유사도 내림차순이고,
기준값으로 삼은 사례가 항상 첫 번째다.

| 상태 | 위치 | 고칠 것 |
|---|---|---|
| [ ] | `src/features/workspace/messages/VerdictCard.tsx` 76행 | "인정기준 도표 — " 접두어 뒤에 서버 `chart.name` 이 오는데, 예전 서버는 name 에도 "인정기준 도표 266 · …" 를 넣어 **"인정기준 도표 — 인정기준 도표 266 · …"** 로 겹쳐 보였다. 서버는 이제 name 을 `266 · 차대차 회전교차로 사고` 처럼 번호부터 보낸다 → 접두어는 그대로 두면 된다. `chartNo` 를 따로 그리는 칸(`src/api/http/map.ts` 127행 `chartNo: null`)은 이제 필요 없으니 지워도 된다 |
| [ ] | 같은 줄 | 이 줄이 판정 카드에 필요 없다고 판단되면 프론트에서 숨겨도 된다(서버는 계속 보낸다). 남길 때는 "참고 인정기준" 정도의 작은 라벨이 더 정확하다 |

---

## 2026-09-06 · 온점 뒤 줄바꿈 (가독성)

서버가 보내는 모든 글은 이제 **문장이 끝나는 자리(…요. / …다. / ? / !) 뒤에 줄바꿈(`\n`)** 이 들어 있다.
채팅 말풍선, 질문 카드, 판정 카드 요약, 재판정 사유, 사건경위서 4개 섹션, 반박의견서 본문 전부.
프론트는 **글자를 그리는 요소에 `whitespace-pre-line` 만 붙이면** 된다 (줄바꿈은 살리고 연속 공백은 접는다).

| 상태 | 위치 | 고칠 것 |
|---|---|---|
| [x] | `src/features/workspace/messages/AiMessage.tsx` `AiText` | 이미 `whitespace-pre-line` 있음 |
| [ ] | `src/features/workspace/messages/VerdictCard.tsx` 55행 `{verdict.conclusion}` | `<p className="whitespace-pre-line text-[15px] leading-[1.6] text-ink">` |
| [ ] | `src/features/documents/StatementDialog.tsx` 127행 `{section.body}` | `<p className="whitespace-pre-line text-[15px] leading-[1.8] text-ink">` |
| [ ] | 같은 파일 147행 `PrintableStatement` 의 `<p>{section.body}</p>` | `<p className="whitespace-pre-line">` (인쇄 화면도 문장별 줄) |
| [ ] | `src/features/workspace/dialogs/GroundDialogs.tsx` 심의사례 팝업 문단 `<p … text-[14px] leading-[1.6] text-ink>` | `whitespace-pre-line` 추가 (한 문단 안에 줄바꿈이 올 때 대비) |
| [ ] | `src/features/documents/RebuttalDialog.tsx` 170행 `<textarea rows={5}>` | textarea 는 줄바꿈을 원래 살린다. 다만 본문이 이제 20줄 안팎의 편지 형식이라 `rows={5}` 는 너무 작다 → `rows={16}` 정도 또는 내용에 맞춰 자동 확장 |
| [ ] | 재판정 사유(`changeReason`)를 그리는 곳이 있으면 | 같은 처리. (현재 카드에서 그리는 코드를 못 찾았다 — 없으면 무시) |

참고

- 사건경위서 PDF 는 서버가 `multi_cell` 로 그려서 줄바꿈이 그대로 나온다. 프론트가 할 일 없음.
- 반박의견서 본문은 "담당자님께, / 1. 주장하는 과실비율 / 2. 주장 근거 / 3. 결론 / 감사합니다." 구조의 여러 줄 글이다. 미리보기·발송 확인 화면이 따로 있으면 거기도 `whitespace-pre-line`.
- 백엔드를 다시 켠 뒤 **새로 만들어진 메시지·판정·문서부터** 줄바꿈이 들어 있다. 예전 사건의 저장된 글은 한 줄 그대로다.

확인 방법: 채팅에서 두 문장 이상인 답을 받아 보면 문장마다 줄이 바뀌어야 한다. 판정 카드 요약도 "예상 과실비율은 나 30 : 상대 70이에요." 다음 줄에 이유가 온다.

---

## 2026-09-06 · 심의사례 팝업 — 그림 + 구조화된 글

명세: `server/docs/precedent-image-spec.md` (API 필드, 글 구조, 디자인 규칙, 코드 예시)

| 상태 | 항목 |
|---|---|
| [x] | `PrecedentDto` 에 `imageUrl`, `imageCaption` 추가 (`src/api/http/dto.ts`) |
| [x] | 팝업에 그림 + 출처 표시, 서명 만료 시 그림만 접기 (`GroundDialogs.tsx` `PrecedentImage`) |
| [x] | `bodyText` 를 소제목 단위로 묶어 그리기, 소제목 볼드, 항목 여럿이면 점 목록 (`toSections`, `SectionBlock`) |
| [ ] | "사례 내용" / "내 사건과의 관계" 두 묶음 사이 구분선 + 묶음 제목, `판정에서의 역할` 은 연한 배경 상자 (명세 3.2 표) — 적용 여부 확인 |
| [ ] | 목(mock) 데이터의 `bodyText` 를 새 구조(소제목 문단)로 바꾸기 — 목에서도 같은 화면이 나오게 |

---

## 2026-09-06 · 대화 흐름 변경 (프론트 코드 변경 없음, 알아 둘 것)

- 사실 질문이 끝나면 "추가로 알려주실 사고 정황이 있나요? … 없으면 '없어요'" → "예상 과실비율을 판정해 드릴까요? … '예상 과실비율 판정해줘'" 순서로 묻는다. 둘 다 일반 텍스트 메시지(`message.created`)로 온다.
- 심의사례는 사용자가 판정을 요청한 뒤 판정 직전 메시지에 한 줄씩 나열되고, 판정 카드의 근거에도 같은 사례가 붙는다. 예전처럼 사례 목록이 먼저 따로 오지 않는다.
- 판정 제안에 "네" 라고만 답해도 판정이 시작된다. 입력창 placeholder 나 추천 답변 칩을 쓴다면 "예상 과실비율 판정해줘" / "없어요" 를 넣어 두면 좋다 (선택).

---

## 2026-09-05 · 로컬 개발 환경 (알아 둘 것)

- `.env.local` 의 `VITE_API_BASE=/api/v1` + `DEV_API_PROXY=http://localhost:8000` 조합으로 로컬 백엔드(실제 AI)에 붙는다. `frontend-guide.md` 참고.
- 배포 서버(`api.fairway.click`)는 아직 MockAgent 다. 실제 AI 화면을 보려면 로컬 백엔드를 쓴다.
