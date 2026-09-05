"""프론트 없이 터미널에서 챗봇을 써 보는 클라이언트.

서버(uvicorn)가 떠 있는 상태에서 다른 터미널에서 실행한다.

    cd server
    .venv/bin/python scripts/chat_cli.py --video ../ai/data/mp4/bb_1_220804_vehicle_116_067.mp4

동작: 가입(또는 로그인) → 사건 생성 → 사고 설명 입력 → 영상 업로드 → 분석 결과·질문 출력 →
이후에는 채팅처럼 답을 입력하면 된다. 판정 카드, 경위서, 반박의견서가 오면 내용까지 같이 찍어 준다.

명령어(슬래시로 시작):
    /video <경로>   영상 업로드(또는 교체)
    /case           사건 상태(진행 단계, 판정, 문서 유무)
    /report [v]     사건경위서 본문 (기본: 최신 버전)
    /pdf            최신 경위서 PDF 생성
    /rebuttal       반박의견서 메일 본문
    /messages       지금까지의 대화 전체 다시 출력
    /quit           종료
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

DEFAULT_BASE = os.environ.get("CHAT_CLI_BASE", "http://localhost:8000")
DEFAULT_PASSWORD = "Test1234!aB"
POLL_SEC = 2.0
WAIT_LIMIT_SEC = 420  # 영상 분석·판정은 서버 Job 제한(300 s)까지 걸릴 수 있다


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/") + "/api/v1"
        self.token: str | None = None

    def call(self, method: str, path: str, body: dict | None = None, files: dict | None = None, headers: dict | None = None):
        hdrs = {"Origin": "http://localhost:5173"}
        if self.token:
            hdrs["Authorization"] = f"Bearer {self.token}"
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
        req = urllib.request.Request(self.base + path, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                payload = r.read()
                return r.status, (json.loads(payload) if payload else {})
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                return e.code, json.loads(payload)
            except Exception:
                return e.code, {"raw": payload[:300].decode(errors="replace")}
        except urllib.error.URLError as e:
            raise SystemExit(f"서버에 연결할 수 없어요: {self.base} ({e.reason}). uvicorn이 떠 있는지 확인해 주세요.")


def error_text(body: dict) -> str:
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return f"{err.get('code', '')} {err.get('message', '')}".strip()
    return json.dumps(body, ensure_ascii=False)[:300]


def indent(text: str, pad: str = "    ") -> str:
    return "\n".join(pad + line for line in (text or "").splitlines()) or pad


class Chat:
    def __init__(self, api: Api, case_id: str) -> None:
        self.api = api
        self.case_id = case_id
        self.last_seen = ""  # 마지막으로 출력한 메시지 id (ULID라 문자열 비교로 정렬됨)
        self.report_version = 0
        self.rebuttal_shown = False

    # ----- 조회 -----
    def case(self) -> dict:
        st, body = self.api.call("GET", f"/cases/{self.case_id}")
        if st != 200:
            raise SystemExit(f"사건 조회 실패: {error_text(body)}")
        return body

    def messages(self) -> list[dict]:
        st, body = self.api.call("GET", f"/cases/{self.case_id}/messages?limit=50")
        if st != 200:
            raise SystemExit(f"메시지 조회 실패: {error_text(body)}")
        return sorted(body.get("items", []), key=lambda m: m["id"])

    # ----- 출력 -----
    def print_message(self, m: dict) -> None:
        p = m.get("payload") or {}
        role, kind = m["role"], m["type"]
        if role == "user":
            if kind == "video_attachment":
                dur = f", {p.get('durationSec')}초" if p.get("durationSec") is not None else ""
                print(f"\n👤 [영상 첨부] {p.get('filename')} ({p.get('sizeLabel')}{dur})")
            else:
                print(f"\n👤 {p.get('text', '')}")
            return
        if kind in ("text", "guide"):
            print("\n🤖 " + (p.get("text") or "").replace("\n", "\n   "))
        elif kind == "verdict":
            ratio = p.get("ratio") or {}
            print(f"\n⚖️  [판정 카드 v{p.get('version')}] 나 {ratio.get('mine')} : 상대 {ratio.get('other')}")
            print(indent(p.get("summary", "")))
            if p.get("changeReason"):
                print("    · 변경 사유: " + p["changeReason"])
            claim = p.get("opponentClaim") or {}
            if claim:
                print(f"    · 상대 주장: 나 {claim.get('mine')} : 상대 {claim.get('other')}")
            basis = p.get("basis") or {}
            chart = basis.get("chart") or {}
            if chart.get("name"):
                print(f"    · 근거 도표: {chart.get('name')} — {chart.get('note', '')}")
            for pr in basis.get("precedents") or []:
                print(f"    · 심의사례 {pr.get('id')}: {pr.get('title')}")
        elif kind == "report_draft":
            print(f"\n📄 [사건경위서 카드] {p.get('versionLabel') or p.get('version')} · {p.get('pageCount')}쪽")
            for line in p.get("preview") or []:
                print("    " + str(line))
            if p.get("caveat"):
                print("    ※ " + str(p["caveat"]))
        elif kind == "rebuttal_draft":
            print("\n✉️  [반박의견서 카드] " + (p.get("subject") or p.get("text") or ""))
        elif kind == "rebuttal_locked":
            print("\n🔒 " + (p.get("text") or json.dumps(p, ensure_ascii=False)))
        elif kind == "sent":
            print("\n📬 [발송 완료] " + json.dumps(p, ensure_ascii=False)[:300])
        else:
            print(f"\n🤖 [{kind}] " + json.dumps(p, ensure_ascii=False)[:500])

    def print_new(self) -> int:
        new = [m for m in self.messages() if m["id"] > self.last_seen]
        for m in new:
            self.print_message(m)
            self.last_seen = m["id"]
        return len(new)

    def print_documents_if_changed(self, c: dict) -> None:
        docs = c.get("documents") or {}
        report = docs.get("report") or {}
        version = report.get("version") or 0
        if report.get("exists") and version > self.report_version:
            self.report_version = version
            self.show_report(version)
        rebuttal = docs.get("rebuttal") or {}
        if rebuttal.get("exists") and not self.rebuttal_shown:
            self.rebuttal_shown = True
            self.show_rebuttal()

    def show_case(self) -> None:
        c = self.case()
        print(f"\n📁 {c.get('title')}  [{c.get('statusLabel')}]  {c.get('subtitle')}")
        stages = c.get("stages") or {}
        if isinstance(stages, dict) and stages:
            names = {"analysis": "영상 분석", "fault_ratio": "과실비율", "report": "경위서", "rebuttal": "반박의견서"}
            print("    단계: " + "  ".join(f"{names.get(k, k)}={v.get('state')}" for k, v in stages.items() if isinstance(v, dict)))
        if c.get("verdict"):
            print("    판정: " + json.dumps(c["verdict"], ensure_ascii=False))
        print("    문서: " + json.dumps(c.get("documents"), ensure_ascii=False))
        if c.get("activeJob"):
            print("    실행 중 작업: " + json.dumps(c["activeJob"], ensure_ascii=False))

    def show_report(self, version: int | str | None = None) -> None:
        if not version:
            c = self.case()
            version = ((c.get("documents") or {}).get("report") or {}).get("version")
            if not version:
                print("    아직 사건경위서가 없어요. 채팅에 '사건경위서 만들어줘'라고 입력해 보세요.")
                return
        st, body = self.api.call("GET", f"/cases/{self.case_id}/report/versions/{version}")
        if st != 200:
            print("    경위서 조회 실패: " + error_text(body))
            return
        print(f"\n📄 사건경위서 {body.get('versionLabel') or version} ({body.get('dateLabel')}, {body.get('pageCount')}쪽)")
        for s in body.get("sections") or []:
            print(f"\n  {s.get('index')}. {s.get('title')}")
            print(indent(s.get("body", "")))

    def show_rebuttal(self) -> None:
        st, body = self.api.call("GET", f"/cases/{self.case_id}/rebuttal")
        if st != 200:
            print("    아직 반박의견서가 없어요. 판정과 사건경위서가 있어야 만들 수 있어요.")
            return
        print(f"\n✉️  반박의견서 (상태: {body.get('status')})")
        print("  제목: " + (body.get("subject") or ""))
        print("  첨부: " + ", ".join(a.get("filename", "") for a in body.get("attachments") or [] if isinstance(a, dict)))
        print(indent(body.get("body", "")))

    def make_pdf(self) -> None:
        c = self.case()
        version = ((c.get("documents") or {}).get("report") or {}).get("version")
        if not version:
            print("    아직 사건경위서가 없어요.")
            return
        st, body = self.api.call("POST", f"/cases/{self.case_id}/report/versions/{version}/pdf")
        if st in (200, 201):
            size_kb = (body.get("sizeBytes") or 0) / 1024
            print(f"    PDF {'생성' if st == 201 else '이미 있음'}: {body.get('filename')} ({size_kb:.0f} KB)  GET {body.get('downloadUrl')}")
        else:
            print("    PDF 생성 실패: " + error_text(body))

    # ----- 동작 -----
    def wait_for_reply(self, label: str = "답변을 기다리는 중") -> None:
        """새 assistant 메시지가 오고, 서버 Job(분석·판정·문서)이 끝날 때까지 폴링한다."""
        deadline = time.time() + WAIT_LIMIT_SEC
        got_reply = False
        spinner = 0
        while time.time() < deadline:
            c = self.case()
            job = c.get("activeJob")
            if self.print_new():
                got_reply = True
            self.print_documents_if_changed(c)
            if job:
                spinner += 1
                names = {"analysis": "영상 분석", "verdict": "과실비율 판정", "report": "사건경위서 작성", "rebuttal": "반박의견서 작성"}
                sys.stdout.write(f"\r    ⏳ {names.get(job.get('kind'), job.get('kind'))} 진행 중{'.' * (spinner % 4):<3}")
                sys.stdout.flush()
            elif got_reply:
                time.sleep(1.5)  # 답변 직후 Job이 붙는 경우(판정·문서)를 한 번 더 확인
                c = self.case()
                self.print_new()
                self.print_documents_if_changed(c)
                if not c.get("activeJob"):
                    print()
                    return
            time.sleep(POLL_SEC)
        print(f"\n    ⚠️  {WAIT_LIMIT_SEC}초 동안 응답이 없었어요. /messages 로 다시 확인해 보세요.")

    def send(self, text: str) -> None:
        st, body = self.api.call("POST", f"/cases/{self.case_id}/messages", {"text": text})
        if st != 202:
            print("    전송 실패: " + error_text(body))
            return
        self.last_seen = max(self.last_seen, body["message"]["id"])
        self.wait_for_reply()

    def upload(self, path: str) -> None:
        p = Path(path).expanduser()
        if not p.is_file():
            print(f"    파일이 없어요: {p}")
            return
        mime = {"mp4": "video/mp4", "mov": "video/quicktime", "avi": "video/x-msvideo"}.get(p.suffix.lower().lstrip("."), "video/mp4")
        print(f"    ⬆️  업로드 중: {p.name} ({p.stat().st_size / 1_048_576:.1f} MB)")
        st, body = self.api.call("POST", f"/cases/{self.case_id}/videos", files={"file": (p.name, p.read_bytes(), mime)})
        if st != 201:
            print("    업로드 실패: " + error_text(body))
            return
        if body.get("needsDescription"):
            self.print_new()
            print("    (사고 설명을 먼저 입력하면 분석이 시작돼요)")
            return
        print("    영상 분석을 시작했어요. 보통 30초~2분 걸려요.")
        self.wait_for_reply()


def sign_in(api: Api, email: str | None, password: str) -> str:
    email = email or f"cli-{uuid.uuid4().hex[:8]}@example.com"
    st, body = api.call("POST", "/auth/signup", {
        "email": email, "password": password, "passwordConfirm": password,
        "agreements": {"termsOfService": True, "privacy": True, "videoConsent": True},
    })
    if st == 201:
        api.token = body["accessToken"]
        print(f"✅ 가입: {email}")
        return email
    st, body = api.call("POST", "/auth/login", {"email": email, "password": password})
    if st != 200:
        raise SystemExit(f"로그인 실패: {error_text(body)}")
    api.token = body["accessToken"]
    print(f"✅ 로그인: {email}")
    return email


def main() -> None:
    ap = argparse.ArgumentParser(description="Car-Defender 챗봇 터미널 클라이언트")
    ap.add_argument("--base", default=DEFAULT_BASE, help=f"서버 주소 (기본 {DEFAULT_BASE})")
    ap.add_argument("--video", help="처음에 올릴 블랙박스 영상 경로")
    ap.add_argument("--description", help="사고 설명 (없으면 물어봄)")
    ap.add_argument("--email", help="계정 이메일 (없으면 임시 계정 생성)")
    ap.add_argument("--password", default=DEFAULT_PASSWORD)
    ap.add_argument("--case", dest="case_id", help="기존 사건 id로 이어서 대화")
    args = ap.parse_args()

    api = Api(args.base)
    st, _ = api.call("GET", "/health")
    print(f"서버: {api.base}  (health {st})")
    sign_in(api, args.email, args.password)

    if args.case_id:
        chat = Chat(api, args.case_id)
        print(f"📁 사건 {args.case_id} 이어서 진행")
        chat.print_new()
        chat.print_documents_if_changed(chat.case())
    else:
        st, body = api.call("POST", "/cases")
        if st != 201:
            raise SystemExit(f"사건 생성 실패: {error_text(body)}")
        chat = Chat(api, body["id"])
        print(f"📁 새 사건 {chat.case_id}")
        chat.print_new()
        desc = args.description or input("\n사고 상황을 한두 문장으로 알려 주세요 > ").strip()
        if desc:
            st, body = api.call("POST", f"/cases/{chat.case_id}/messages", {"text": desc})
            if st != 202:
                raise SystemExit("설명 전송 실패: " + error_text(body))
            time.sleep(1.5)
            chat.print_new()  # 방금 보낸 설명과 안내 답변을 같이 출력
        if args.video:
            chat.upload(args.video)
        else:
            print("\n영상을 올리려면  /video <경로>  를 입력하세요. (예: /video ../ai/data/mp4/bb_1_220804_vehicle_116_067.mp4)")

    print("\n대화를 시작해요. 도움말은 /help, 종료는 /quit")
    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n종료")
            return
        if not line:
            continue
        if line.startswith("/"):
            cmd, _, arg = line.partition(" ")
            cmd = cmd.lower()
            if cmd in ("/quit", "/exit", "/q"):
                print(f"종료. 이어서 하려면: --case {chat.case_id}")
                return
            elif cmd == "/video":
                chat.upload(arg.strip() or input("영상 경로 > ").strip())
            elif cmd == "/case":
                chat.show_case()
            elif cmd == "/report":
                chat.show_report(arg.strip() or None)
            elif cmd == "/pdf":
                chat.make_pdf()
            elif cmd == "/rebuttal":
                chat.show_rebuttal()
            elif cmd == "/messages":
                for m in chat.messages():
                    chat.print_message(m)
            elif cmd == "/help":
                print(__doc__)
            else:
                print("    모르는 명령이에요. /help 를 입력해 보세요.")
            continue
        chat.send(line)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
