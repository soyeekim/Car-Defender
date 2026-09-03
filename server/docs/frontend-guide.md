# 프론트엔드 연동 가이드

API 계약 자체는 `20_API명세서_v2.md` 가 정본이다. 이 문서는 **어느 주소에 어떻게 붙느냐**만 다룬다.

환경은 셋이다. 붙는 방법은 같고, **로그인 유지가 되느냐**가 다르다.

| 환경 | API 주소 | 로그인 유지 |
|---|---|---|
| 로컬 | `http://localhost` | 된다 |
| Vercel 임시 도메인 | `https://api.fairway.click` | **안 된다 (30분 뒤 풀림)** |
| `fairway.click` 연결 후 | `https://api.fairway.click` | 된다 |

시연은 반드시 세 번째로 한다. 이유는 아래 "쿠키" 절에 있다.

---

## 1. 로컬에서 백엔드 띄우기

Docker만 있으면 된다. 파이썬도 DB도 따로 안 깐다.

```bash
git clone https://github.com/soyeekim/Car-Defender.git
cd Car-Defender/server
cp .env.example .env          # 기본값 그대로 쓰면 된다
docker compose up -d
```

1~2분 뒤 확인한다.

```bash
curl http://localhost/api/v1/health
# {"status":"ok","version":"2.0.0","checks":{...}}
```

### 바로 쓸 수 있는 것

| | |
|---|---|
| API | `http://localhost/api/v1` |
| **Swagger (API 눌러보기)** | http://localhost/docs |
| 데모 계정 | `demo@fairway.click` / `demo1234` (종결된 사건 1건 들어 있음) |

데모 계정이 안 보이면 한 번 넣어 준다.

```bash
docker compose exec app python -m app.seed
```

### 알아 둘 것

- **AI는 가짜다.** `MockAgent` 가 정해진 시나리오로 답한다. 영상을 올리면 항상 같은 질문 2개가 오고, 답하면 과실비율 `0 : 100` 이 나온다. 화면 만들기에는 충분하다.
- **메일도 가짜다.** 발송 버튼을 눌러도 실제로 안 나가고 성공 응답만 온다.
- 업로드한 영상과 PDF는 `server/data/` 에 쌓인다.

### 자주 쓰는 명령

```bash
docker compose logs -f app     # 로그 보기
docker compose restart app     # 재시작
docker compose down            # 끄기
docker compose down -v         # 끄면서 DB까지 비우기 (처음부터 다시)
```

---

## 2. Vercel 임시 도메인에서 붙기

`https://car-defender-tawny.vercel.app` → `https://api.fairway.click`

CORS는 열어 뒀다. 그대로 호출하면 된다.

### 다만 로그인이 30분마다 풀린다

이 조합에서는 **refresh 쿠키가 서버로 가지 않는다.** `vercel.app` 과 `fairway.click` 은
브라우저가 남남으로 보는 사이라, 쿠키에 걸린 `SameSite=Lax` 가 전송을 막는다.

그래서 이렇게 된다.

1. 로그인 → 액세스 토큰 받음 → 30분간 정상
2. 30분 뒤 → `POST /auth/refresh` 가 401
3. 다시 로그인해야 함

**개발과 화면 확인에는 문제없다. 시연에는 쓰면 안 된다.**

---

## 3. `fairway.click` 연결 후

Vercel Settings → Domains 에서 `fairway.click` 을 붙이고 나면 끝난다.

`https://fairway.click` 과 `https://api.fairway.click` 은 뿌리 도메인이 같아 브라우저가
한 식구로 본다. 쿠키가 정상적으로 오가고 자동 갱신이 된다. **코드는 안 고쳐도 된다.**

---

## 붙일 때 지켜야 하는 것

### 모든 요청에 `credentials: 'include'`

이게 빠지면 로그인 자체는 되는데 refresh 쿠키가 저장되지도, 전송되지도 않는다.
세 환경 모두 프론트와 API의 출처가 다르기 때문이다.

```js
const res = await fetch(`${API_BASE}/cases`, {
  credentials: 'include',                       // 필수
  headers: { Authorization: `Bearer ${accessToken}` },
});
```

액세스 토큰은 메모리(또는 상태 저장소)에 둔다. `localStorage` 는 XSS에 그대로 털린다.

### 401이 오면 한 번 갱신하고 재시도

```js
if (res.status === 401) {
  const r = await fetch(`${API_BASE}/auth/refresh`, { method: 'POST', credentials: 'include' });
  if (r.ok) { accessToken = (await r.json()).accessToken; /* 원래 요청 재시도 */ }
  else { /* 로그인 화면으로 */ }
}
```

`/auth/refresh` 는 본문도 헤더도 필요 없다. 쿠키만으로 동작한다.

### 실시간 알림(SSE)은 쿼리 파라미터로 인증한다

`EventSource` 는 헤더를 못 붙인다. 그래서 이 엔드포인트만 토큰을 쿼리로도 받는다.

```js
const es = new EventSource(
  `${API_BASE}/cases/${caseId}/events?access_token=${accessToken}`,
  { withCredentials: true },
);
```

분석·판정·문서 생성이 끝나면 여기로 이벤트가 온다. **폴링하지 말 것.**
연결이 끊겼다 붙으면 브라우저가 `Last-Event-ID` 를 자동으로 보내고, 서버가 최근 5분치를
다시 흘려준다. 놓친 이벤트를 따로 챙길 필요는 없다.

### 영상 재생은 받은 URL을 그대로 쓴다

영상 조회 응답의 `streamUrl` 은 이미 서명이 붙어 있다.

```
/api/v1/videos/{videoId}/stream?t=eyJhbGci...
```

`<video src={API_BASE_ORIGIN + streamUrl} />` 로 그냥 넣으면 된다. `Authorization` 헤더를
붙이지 않는다 (video 태그는 헤더를 못 보낸다). **유효 기간이 10분**이라 오래 열어 두는
화면이면 만료 시 영상 정보를 다시 받아 URL을 갱신한다.

### 에러는 화면에 그대로 쓸 수 있게 온다

```json
{ "error": {
    "code": "REBUTTAL_LOCKED",
    "title": "아직 만들 수 없어요",
    "message": "경위서를 먼저 만들면 열려요.",
    "retryable": false,
    "actions": [{ "label": "경위서 만들기", "action": "create_report" }]
} }
```

`title` 과 `message` 는 사용자에게 보여 줄 문장으로 이미 다듬어져 있다. 직접 문구를
만들지 말고 이걸 쓴다. `actions` 가 있으면 버튼으로 띄운다.

### 업로드 제한

영상은 **최대 200MB, 3분**, mp4 권장. 초과하면 413 또는 422로 사유가 온다.

---

## 환경 전환

주소만 환경변수로 빼 두면 셋 다 커버된다.

```bash
# .env.local
VITE_API_BASE=http://localhost/api/v1

# Vercel 환경변수
VITE_API_BASE=https://api.fairway.click/api/v1
```

---

## 막혔을 때

| 증상 | 원인 |
|---|---|
| CORS 오류 | 프론트 주소가 서버 허용 목록에 없다. 백엔드 담당에게 주소를 알려 준다 |
| 로그인은 되는데 새로고침하면 풀림 | `credentials: 'include'` 가 빠졌다 |
| 30분마다 로그아웃 (Vercel 임시 도메인) | 정상이다. 위 2번 참고 |
| SSE가 401 | 토큰을 쿼리(`?access_token=`)로 안 넘겼다 |
| 영상이 갑자기 안 나옴 | `streamUrl` 이 10분 지나 만료됐다. 다시 받는다 |
| 로컬에서 502 | 컨테이너가 아직 뜨는 중이다. `docker compose logs -f app` 로 본다 |
