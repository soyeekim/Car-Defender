# 심의사례 팝업 — 그림 + 구조화된 글

판정 카드의 "근거" 목록에서 심의사례를 누르면 뜨는 팝업(H37, `PrecedentDialog`) 명세.
백엔드·AI 쪽은 구현이 끝나 있다. **프론트가 할 일은 3절**이다.

| 바뀌는 것 | 지금 | 앞으로 |
|---|---|---|
| 팝업 본문 글 | 한 덩어리, 사례 내용과 내 사건 비교가 섞여 있음 | **사례 내용 → 내 사건과의 관계** 순서로 소제목별 정리, 문장마다 문단 |
| 그림 | 없음 | 원본 PDF 의 표(사례 개요 / 도표)를 잘라 낸 PNG 한 장 |
| API | `GET /precedents/{id}` 의 `bodyText` | 같은 API 에 `imageUrl` `imageCaption` 추가 + 그림 파일 API 신설 |

---

## 1. API

### 1.1 심의사례 조회 — 기존 API, 필드 2개 추가

```
GET /api/v1/precedents/{precedentId}?caseId={caseId}
Authorization: Bearer {accessToken}
```

```jsonc
{
  "precedentId": "2017-045140",
  "title": "심의사례 2017-045140",
  "bodyText": "사고 유형\n\n차대차 직진 대 좌회전 사고(맞은편) · 사거리 교차로(…)\n\n사고 내용\n\n청구차량이 … 충돌한 사고임.\n\n…",
  "imageUrl": "/api/v1/precedents/2017-045140/image?t=eyJhbGciOi…",
  "imageCaption": "출처: 손해보험협회 자동차사고 과실비율 인정기준 · 과실비율 심의사례"
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `bodyText` | string | 빈 줄(`\n\n`)로 나뉜 문단. 구조는 2절 |
| `imageUrl` | string \| null | **서명이 붙은 상대 URL.** `<img src>` 에 그대로 넣는다. 그림이 없으면 `null` |
| `imageCaption` | string \| null | 그림 아래 출처 한 줄. `imageUrl` 이 `null` 이면 `null` |

- `imageUrl` 은 영상의 `streamUrl` 과 같은 방식이다. `Authorization` 헤더를 붙이지 않는다 (img 태그는 헤더를 못 보낸다). `credentials` 도 필요 없다.
- 서명은 **10분** 유효하다. 팝업을 열 때마다 이 API 를 다시 부르므로(지금 동작) 따로 갱신할 필요는 없다.

### 1.2 그림 파일 — 신설. 프론트가 직접 부르지 않는다

```
GET /api/v1/precedents/{precedentId}/image?t={서명}
```

| 상태 | 언제 | 화면 반응 |
|---|---|---|
| `200 image/png` | 정상. `Cache-Control: private, max-age=600` | 그림 표시 |
| `401` / `403` | 서명이 없거나 틀리거나 만료 | 그림만 숨긴다 (`onError`). 글은 그대로 |
| `404` | 그 심의번호의 그림이 없음 | 위와 같음 (정상이라면 `imageUrl` 이 애초에 `null` 이라 일어나지 않는다) |

**프론트는 이 주소를 조립하지 않는다.** 1.1 응답의 `imageUrl` 만 쓴다.

---

## 2. `bodyText` 구조

문단 하나 = **소제목 한 줄** 또는 **문장 하나**. 소제목은 아래 목록에 있는 글자 그대로 온다 (앞뒤 공백·기호 없음).

| 소제목 | 내용 | 문단 수 |
|---|---|---|
| `사고 유형` | 사고 분류 · 장소 | 1 |
| `사고 내용` | 어떤 사고였는지 한 문장 | 1 |
| `쟁점` | 심의에서 다툰 점 | 1~3 |
| `과실비율` | `기본 80:20 → 결정 70:30 (A 청구차량 : B 피청구차량)` | 1 |
| `심의 이유` | 결정 이유. **문장마다 문단 하나** | 1~6 |
| `적용 수정요소` | 있을 때만 | 0~4 |
| `참고 인정기준` | `도표 213(나)` | 0~1 |
| `내 사건과 비슷한 점` | `항목: 내용` 꼴. 항목마다 문단 | 0~4 |
| `내 사건과 다른 점` | 위와 같음 | 0~3 |
| `판정에서의 역할` | "가장 비슷한 사례예요. 이 사례의 결정비율 70:30을 기준값으로 …" / "판정 근거로 함께 참고한 사례예요." / "비교를 위해 함께 살펴본 사례예요." | 1 |

앞 7개는 **그 심의사례의 내용**, 뒤 3개는 **내 사건과의 관계**다. 항상 이 순서로 온다.
인정기준 도표(심의번호가 `차1-1` `보1` `회전-4` 같은 것)일 때는 앞부분이 `사고 유형` `사고 내용` `기본 과실비율` `수정요소` 로 바뀌고, 첫 문단은 `과실비율 인정기준 도표 차1-1` 처럼 제목이다.

규칙

- 사례 내용(심의문)은 원문 그대로다(`~사고임.` `~결정함.`). 내 사건과의 관계 부분만 해요체다.
- `A`·`B` 는 **심의사례 속 청구·피청구 차량**이지 사용자·상대가 아니다. 그림 옆에 "나 30 : 상대 70" 을 다시 적지 않는다.

<details>
<summary><b>▶ 예시 — 2017-045140 의 bodyText 를 문단으로 나눈 것</b></summary>

```
사고 유형
차대차 직진 대 좌회전 사고(맞은편) · 사거리 교차로(상대 차량이 맞은편 방향에서 진입)

사고 내용
청구차량이 교차로의 신호가 좌회전신호에서 황색신호로 바뀌었음에도 좌회전하여 교차로에 진입하다가
우측도로에서 녹색신호로 바뀌자마자 직진하던 피청구차량과 충돌한 사고임.

쟁점
청구차량이 황색신호에 교차로 진입하였는지 여부
녹색신호에 직진한 피청구차량의 과실 유무

과실비율
기본 80:20 → 결정 70:30 (A 청구차량 : B 피청구차량)

심의 이유
청구차량이 황색신호로 바뀌었음에도 교차로에 꼬리물기식으로 진입하였던 점, 피청구차량은 교차로의 상황을
살피지 않고 녹색신호로 바뀌자마자 직진을 하여 교차로에 진입하였던 점 고려하여 결정함.

참고 인정기준
도표 213(나)

내 사건과 비슷한 점
사고 장소 유형: 사거리 교차로
차량 진행 방향: 직진 vs 좌회전
신호 조건: 황색 신호 관련

내 사건과 다른 점
상대 차량 진입 방향: 본 사건은 좌측 도로에서 직진 횡단 진입, 사례는 맞은편 좌회전 진입
상대 차량 신호 상태: 사례는 좌회전 신호 관련, 본 사건 상대 차량 신호 불명확

판정에서의 역할
가장 비슷한 사례예요. 이 사례의 결정비율 70:30을 기준값으로 삼아 내 사건의 예상 과실비율을 계산했어요.
```

</details>

---

## 3. 화면

### 3.1 팝업 구성

```
┌ 심의사례 2017-045140 ───────────────────────── × ┐
│                                                   │
│  ┌───────────────────────────────────────────┐    │
│  │  [사례 개요 표 그림 · 폭 100% · 모서리 둥글게]  │    │  ← imageUrl 이 있을 때만
│  └───────────────────────────────────────────┘    │
│  출처: 손해보험협회 …                (muted 12.5px) │  ← imageCaption
│                                                   │
│  ▌사례 내용 ──────────────────────────────────    │  ← 묶음 제목 (프론트 고정 문구)
│                                                   │
│  **사고 유형**                                     │  ← 소제목: 볼드
│  차대차 직진 대 좌회전 사고(맞은편) · 사거리 교차로  │
│                                                   │
│  **사고 내용**                                     │
│  청구차량이 … 충돌한 사고임.                        │
│                                                   │
│  **쟁점**                                          │
│  · 청구차량이 황색신호에 교차로 진입하였는지 여부    │  ← 여러 문단이면 점(·) 목록
│  · 녹색신호에 직진한 피청구차량의 과실 유무          │
│                                                   │
│  **과실비율**                                      │
│  기본 80:20 → 결정 70:30 (A 청구차량 : B 피청구차량) │
│  …                                                │
│  ─────────────────────────────────────────────    │  ← 구분선
│  ▌내 사건과의 관계                                 │
│                                                   │
│  **내 사건과 비슷한 점**                            │
│  · 사고 장소 유형: 사거리 교차로                    │
│  …                                                │
│  ┌ 판정에서의 역할 ─────────────────────────┐     │  ← 연한 배경 상자
│  │ 가장 비슷한 사례예요. 이 사례의 결정비율 … │     │
│  └──────────────────────────────────────────┘     │
│                                          [닫기]   │
└───────────────────────────────────────────────────┘
```

### 3.2 디자인 규칙

토큰은 전부 `src/styles/theme.css` 것만 쓴다. 간격은 4의 배수. 아래 값은 그 범위 안에서 정한 것이다.

| 요소 | 값 |
|---|---|
| 그림 | 폭 100%(팝업 480px), 비율 유지, `rounded-lg border border-line`, `loading="lazy"`. 오류·만료면 그림만 숨긴다 |
| 자리 흔들림 | 로딩 전 `aspect-[830/420]` 빈 상자 (선택) |
| 출처 | 그림 바로 아래, `text-[12.5px] text-muted`. 화면당 한 번. 참고용 고지(`<Disclaimer />`)와는 별개 |
| 묶음 제목 (`사례 내용` / `내 사건과의 관계`) | 프론트가 넣는 고정 문구. `text-[12.5px] font-medium text-muted uppercase-없음`, 왼쪽 2px 세로선(`border-l-2 border-primary pl-2`) |
| 두 묶음 사이 | `border-t border-line` 구분선 + 위아래 `py-4` |
| **소제목** (2절 목록의 문단) | **`text-[14px] font-semibold text-ink`**. 앞 소제목 묶음과 `mt-3` 간격, 자기 내용과는 `mt-1` |
| 본문 문단 | `text-[14px] leading-[1.6] text-ink`. 소제목 아래 문단이 2개 이상이면 각 문단 앞에 `·` (점 하나, `text-muted`) |
| `과실비율` 값 | 숫자만 `font-semibold`. 화살표(→)는 그대로 글자로 |
| `판정에서의 역할` | 소제목 대신 상자: `rounded-lg bg-bg-2 p-3`, 첫 줄에 소제목(볼드), 둘째 줄에 문장. 관계 묶음의 마지막 |
| `imageUrl` = `null` | `figure` 자체를 그리지 않는다. 빈 상자·"그림 없음" 문구 금지 |
| 스크롤 | 본문은 `.doc-scroll min-h-0`. 그림 포함 세로가 길어지므로 팝업 본문만 스크롤, 제목·[닫기]는 고정 |
| 범위 밖 | 클릭 확대, 새 창 열기, 그림 저장, 일치도 배지 |

소제목이 **볼드**인 이유: 소제목과 본문이 같은 굵기면 예전처럼 한 덩어리로 보인다. 색으로 구분하지 않고 굵기로 구분해서, 정보 글자를 `muted` 까지만 쓰는 규칙을 지킨다.

### 3.3 코드 — `src/features/workspace/dialogs/GroundDialogs.tsx`

`bodyText` 를 "소제목 + 그 아래 문단들" 묶음으로 바꾼 뒤 그린다.

<details>
<summary><b>▶ PrecedentDialog 에 넣는 예</b></summary>

```tsx
// props 추가: imageUrl?: string | null; imageCaption?: string | null

const CASE_LABELS = ['사고 유형', '사고 내용', '쟁점', '과실비율', '심의 이유', '적용 수정요소', '참고 인정기준', '기본 과실비율', '수정요소'];
const RELATION_LABELS = ['내 사건과 비슷한 점', '내 사건과 다른 점', '판정에서의 역할'];
const LABELS = new Set([...CASE_LABELS, ...RELATION_LABELS]);

type Section = { label: string | null; items: string[] };

/** "\n\n" 문단을 소제목 단위로 묶는다. 첫 소제목 앞의 문단(도표 제목 등)은 label=null */
function toSections(bodyText: string): Section[] {
  const out: Section[] = [];
  for (const para of bodyText.split('\n\n').map((p) => p.trim()).filter(Boolean)) {
    if (LABELS.has(para)) out.push({ label: para, items: [] });
    else if (out.length === 0 || out[out.length - 1].label === null) (out[out.length - 1] ?? out[out.push({ label: null, items: [] }) - 1]).items.push(para);
    else out[out.length - 1].items.push(para);
  }
  return out;
}

function SectionBlock({ section }: { section: Section }) {
  if (section.label === '판정에서의 역할') {
    return (
      <div className="rounded-lg bg-bg-2 p-3">
        <p className="text-[14px] font-semibold text-ink">{section.label}</p>
        <p className="mt-1 text-[14px] leading-[1.6] text-ink">{section.items.join(' ')}</p>
      </div>
    );
  }
  const bulleted = section.items.length > 1;
  return (
    <div className="flex flex-col gap-1">
      {section.label && <p className="mt-3 text-[14px] font-semibold text-ink">{section.label}</p>}
      {section.items.map((item) => (
        <p key={item.slice(0, 24)} className="text-[14px] leading-[1.6] text-ink">
          {bulleted && <span className="mr-1 text-muted">·</span>}
          {item}
        </p>
      ))}
    </div>
  );
}

function GroupTitle({ children }: { children: string }) {
  return <p className="border-l-2 border-primary pl-2 text-[12.5px] font-medium text-muted">{children}</p>;
}

// 본문
const sections = bodyText ? toSections(bodyText) : [];
const caseSections = sections.filter((s) => !RELATION_LABELS.includes(s.label ?? ''));
const relationSections = sections.filter((s) => RELATION_LABELS.includes(s.label ?? ''));

<div className="doc-scroll flex min-h-0 flex-col gap-4">
  {precedent && imageUrl && (
    <figure className="flex flex-col gap-2">
      <img
        src={new URL(imageUrl, API_BASE).toString()}   // API_BASE = client.ts 의 "…/api/v1". imageUrl 이 /api/v1/… 로 시작하므로 origin 만 남는다
        alt={`심의사례 ${precedent.no} 사례 개요`}
        className="w-full rounded-lg border border-line"
        loading="lazy"
        onError={(e) => { e.currentTarget.style.display = 'none'; }}
      />
      {imageCaption && <figcaption className="text-[12.5px] text-muted">{imageCaption}</figcaption>}
    </figure>
  )}

  {caseSections.length > 0 && (
    <section className="flex flex-col gap-1">
      <GroupTitle>사례 내용</GroupTitle>
      {caseSections.map((s, i) => <SectionBlock key={s.label ?? i} section={s} />)}
    </section>
  )}

  {relationSections.length > 0 && (
    <section className="flex flex-col gap-1 border-t border-line pt-4">
      <GroupTitle>내 사건과의 관계</GroupTitle>
      {relationSections.map((s) => <SectionBlock key={s.label!} section={s} />)}
    </section>
  )}
</div>
```

</details>

### 3.4 고칠 파일

| 파일 | 변경 |
|---|---|
| `src/api/http/dto.ts` | `PrecedentDto` 에 `imageUrl: string \| null; imageCaption: string \| null;` |
| `src/api/service.ts` · `src/api/http/index.ts` | `getPrecedentText` 가 `bodyText` 문자열만 돌려준다 → `{ bodyText, imageUrl, imageCaption }` 으로 (또는 `getPrecedent` 신설) |
| `src/pages/CaseWorkspacePage.tsx` | 받은 값을 `PrecedentDialog` 에 넘긴다 |
| `src/features/workspace/dialogs/GroundDialogs.tsx` | 3.3 |
| `src/api/mock/` | 같은 모양으로 `imageUrl: null, imageCaption: null`. `bodyText` 도 2절 구조로 시드를 바꿔 두면 목에서도 같은 화면이 나온다 |

---

## 4. 그림

원본 PDF 세 종류에서 표만 잘라 낸 PNG. 폭 약 830px, 높이 370~470px, 100~140 KB.

| 종류 | 심의번호 예 | 잘린 내용 |
|---|---|---|
| 과실비율 심의사례 | `2017-045140` | "사례 개요" 표 — 심의번호·결정비율·사고내용·참고 인정기준 그림 |
| 과실비율 인정기준 도표 | `차1-1` `보1` `거9-5` | 도표 제목 띠·사고 그림·기본 과실비율 표·조정 예시 |
| 2차로형 회전교차로 비정형기준 | `회전-4` | 위와 같은 구성 |

- 인덱스에 있는 405건 전부 만들어져 있다. `imageUrl` 이 `null` 인 경우는 인덱스에 없는 번호뿐이다.
- 그림 속 결정비율(`A(청구) : B(피청구) = 70 : 30`)은 심의사례 기준이다. 사용자 기준 비율은 판정 카드에 있다.

---

## 5. 확인하는 방법

로컬 백엔드(`AGENT_IMPL=ai.agent.real:RealAgent`)로 사건을 판정까지 진행한 뒤:

```bash
TOKEN=<accessToken>; CASE=<caseId>
curl -s "http://localhost:8000/api/v1/precedents/2017-045140?caseId=$CASE" \
  -H "Authorization: Bearer $TOKEN" | jq '{imageUrl, body: .bodyText[0:80]}'
# → "imageUrl": "/api/v1/precedents/2017-045140/image?t=…"

curl -s -o /tmp/p.png "http://localhost:8000<imageUrl 값>" && file /tmp/p.png     # PNG image data, 766 x 420
```

| 증상 | 원인 |
|---|---|
| `imageUrl` 이 전부 `null` | 백엔드가 그림 폴더(`ai/data/rag_index/images/`)를 못 찾음 → AI 담당에게 `cd ai && python build_case_images.py` 요청. 배포 서버는 Docker 이미지에 들어가므로 이미지 재빌드 필요 |
| `<img>` 가 깨짐(403) | `imageUrl` 을 직접 조립했거나 10분이 지난 URL 을 재사용함 → 팝업을 열 때 조회 API 를 다시 부른다 |
| 옛 사건의 글이 예전 형식 | `bodyText` 는 판정 시점에 만들어 저장된다 → 새로 판정한 사건부터 새 구조. 채팅에서 "다시 판정해줘" 로 갱신 |
| 소제목이 볼드로 안 보임 | 문단 글자가 2절 목록과 정확히 같아야 한다. `trim()` 뒤에 비교한다 |

---

## 6. 왜 이렇게 했나

- **그림을 base64 로 `bodyText` 에 넣지 않았다.** 응답이 150 KB 로 커지고 판정 DB 에 저장되기 때문. 파일은 파일로 내려준다.
- **서명 URL 을 쓴다.** `<img>` 는 `Authorization` 헤더를 못 보낸다. 영상 스트림(`/videos/{id}/stream?t=`)과 같은 방식이라 프론트에 새 패턴이 생기지 않는다.
- **사건 소유자 검사 없이 서명만 본다.** 그림은 사용자 데이터가 아니라 공개 자료(손해보험협회 간행물)를 잘라 둔 것이고, 서명은 로그인이 필요한 조회 API 에서만 발급된다.
- **사례 내용과 내 사건 비교를 분리했다.** "이 사례가 무엇인가"를 먼저 보여 주고, "내 사건과 무엇이 같고 다른가"는 뒤에 항목별로 붙인다. 둘이 한 문단에 섞이면 예전처럼 빼곡해진다.
- **소제목을 글 안에 넣어 보냈다.** 프론트가 문단 글자만 보고 소제목을 알아볼 수 있어, 응답 형식을 바꾸지 않고도 화면 구조를 바꿀 수 있다.
