"""배포된 실서버를 심사위원과 똑같은 경로로 훑고 마지막에 실제 메일까지 보내는 연기 테스트.

사용법:
    .venv/Scripts/python scripts/e2e_live.py <받는주소> [영상경로]

가입부터 발송까지 16단계를 순서대로 밟는다. 중간에 하나라도 실패하면 그 자리에서 멈춘다.
메일 공급자를 바꾼 뒤(deploy/switch-mail.sh)에는 반드시 이걸 한 번 돌려 확인한다.
"""
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.environ.get("E2E_BASE", "https://api.fairway.click") + "/api/v1"
RECIPIENT = sys.argv[1] if len(sys.argv) > 1 else "chanu1855@gmail.com"
TOKEN = None


def call(method, path, body=None, headers=None, files=None, raw=False):
    url = BASE + path
    hdrs = {"Origin": "https://fairway.click"}
    if TOKEN:
        hdrs["Authorization"] = f"Bearer {TOKEN}"
    if headers:
        hdrs.update(headers)
    data = None
    if files:
        boundary = uuid.uuid4().hex
        buf = io.BytesIO()
        for name, (fname, content, ctype) in files.items():
            buf.write(f"--{boundary}\r\n".encode())
            buf.write(f'Content-Disposition: form-data; name="{name}"; filename="{fname}"\r\n'.encode())
            buf.write(f"Content-Type: {ctype}\r\n\r\n".encode())
            buf.write(content)
            buf.write(b"\r\n")
        buf.write(f"--{boundary}--\r\n".encode())
        data = buf.getvalue()
        hdrs["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            payload = r.read()
            return r.status, (payload if raw else (json.loads(payload) if payload else {}))
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload)
        except Exception:
            return e.code, {"raw": payload[:200].decode(errors="replace")}


def step(n, label, status, ok=(200, 201, 202)):
    mark = "OK  " if status in ok else "FAIL"
    print(f"  {mark} {n:2}. {label}  [{status}]")
    if status not in ok:
        raise SystemExit(f"\n중단: {label} 에서 {status}")


def poll(label, path, check, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st, body = call("GET", path)
        if st in (200, 201) and check(body):
            return body
        time.sleep(2)
    raise SystemExit(f"\n시간 초과: {label}")


print(f"대상 서버 : {BASE}")
print(f"수신 주소 : {RECIPIENT}  (SES에 등록되지 않은 주소)\n")

email = f"judge-{uuid.uuid4().hex[:8]}@example.com"
pw = "Test1234!aB"

st, r = call("POST", "/auth/signup", {
    "email": email, "password": pw, "passwordConfirm": pw,
    "agreements": {"termsOfService": True, "privacy": True, "videoConsent": True},
})
step(1, f"회원가입 ({email})", st)
TOKEN = r.get("accessToken") or r.get("access_token")
if not TOKEN:
    st, r = call("POST", "/auth/login", {"email": email, "password": pw})
    step(2, "로그인", st)
    TOKEN = r.get("accessToken")

st, r = call("POST", "/cases")
step(3, "사건 생성", st)
cid = r.get("id") or r.get("caseId")
print(f"       사건 ID: {cid}")

st, _ = call("POST", f"/cases/{cid}/messages",
             {"text": "교차로에서 직진 중이었는데 우측에서 오토바이가 신호를 무시하고 들어와 부딪혔어요."})
step(4, "사고 상황 설명 입력", st)

video = open(sys.argv[2] if len(sys.argv) > 2 else "data/videos/01M1K616MG5X7CGRB7DDGHVVF4/01M1K616RFP3244BFPK03TR59F.mp4", "rb").read()
st, r = call("POST", f"/cases/{cid}/videos", files={"file": ("blackbox_0822.mp4", video, "video/mp4")})
step(5, f"블랙박스 영상 업로드 ({len(video):,} bytes)", st)

def has_question(b):
    return any("물어볼게요" in ((m.get("payload") or {}).get("text") or "") for m in b.get("items", []))

poll("영상 분석", f"/cases/{cid}/messages", has_question)
step(6, "영상 분석 완료 (질문 도착)", 200)

st, _ = call("POST", f"/cases/{cid}/messages", {"text": "앞쪽 오른쪽 펜더에 부딪혔어요"})
step(7, "질문 1 답변 (충돌 부위)", st)
st, _ = call("POST", f"/cases/{cid}/messages", {"text": "초록불이었어요"})
step(8, "질문 2 답변 (신호) → 과실비율 계산 시작", st)

c = poll("과실비율 판정", f"/cases/{cid}", lambda b: b.get("verdict"))
step(9, "과실비율 판정", 200)
print(f"       결과: {json.dumps(c['verdict'], ensure_ascii=False)[:160]}")

st, _ = call("POST", f"/cases/{cid}/messages", {"text": "사건경위서 만들어줘"})
step(10, "사건경위서 요청", st)
c = poll("경위서 생성", f"/cases/{cid}", lambda b: (b.get("documents", {}).get("report") or {}).get("exists"))
step(11, "사건경위서 생성", 200)
vnum = c["documents"]["report"].get("version") or 1

st, _ = call("POST", f"/cases/{cid}/report/versions/{vnum}/pdf")
step(12, f"PDF 생성 (v{vnum})", st)

st, _ = call("POST", f"/cases/{cid}/messages", {"text": "반박의견서도 준비해줘"})
step(13, "반박의견서 요청", st)
poll("반박의견서 생성", f"/cases/{cid}/rebuttal", lambda b: b.get("body") or b.get("subject"))
step(14, "반박의견서 생성", 200)

st, _ = call("PATCH", f"/cases/{cid}/rebuttal", {"recipient": RECIPIENT, "claimNumber": "2026-09-0001"})
step(15, f"수신자·접수번호 입력 ({RECIPIENT})", st)

st, r = call("POST", f"/cases/{cid}/rebuttal/send", headers={"Idempotency-Key": uuid.uuid4().hex})
step(16, "메일 발송", st)
print("\n발송 완료")
print(f"  보낸 사람 : {r.get('fromEmail')}")
print(f"  받는 사람 : {r.get('recipient')}")
print(f"  첨부 개수 : {r.get('attachmentCount')}")
print(f"  발송 시각 : {r.get('sentAt')}")
