SERVICE_DOMAIN = "cardefender.kr"


def rebuttal_body(body: str, user_email: str, video_dropped: bool) -> str:
    parts = [body.rstrip(), ""]
    if video_dropped:
        parts.append("블랙박스 영상은 용량 제한으로 첨부하지 못했습니다.")
        parts.append("")
    parts.append(f"이 메일은 카-디펜더({SERVICE_DOMAIN})를 통해 {user_email} 님이 보냈습니다.")
    parts.append("회신은 위 주소로 전달됩니다.")
    return "\n".join(parts)
