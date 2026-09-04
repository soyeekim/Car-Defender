import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Iterable, Literal, Optional

import numpy as np
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()


class RetrievedSource(BaseModel):
    source_id: str
    parent_id: Optional[str] = None
    source_type: Literal["deliberation_case", "fault_standard", "roundabout_special_standard"]
    source_file: str
    page_number: int = Field(ge=1)
    page_start: Optional[int] = Field(default=None, ge=1)
    page_end: Optional[int] = Field(default=None, ge=1)
    chunk_index: int = Field(ge=0)
    similarity_score: float
    semantic_score: Optional[float] = None
    lexical_score: Optional[float] = None
    case_number: Optional[str] = None
    chart_number: Optional[str] = None
    decision_ratio: Optional[str] = None
    matched_child_ids: list[str] = Field(default_factory=list)
    matched_sections: list[str] = Field(default_factory=list)
    excerpt: str

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PDF_PATHS = [
    _ROOT / "data" / "text" / "(최종)과실비율심의사례_(54MB).pdf",
    _ROOT / "data" / "text" / "230630_자동차사고 과실비율 인정기준_최종.pdf",
    _ROOT / "data" / "text" / "250624_2차로형 회전교차로사고 과실비율 비정형기준.pdf",
]
DEFAULT_INDEX_DIR = _ROOT / "data" / "rag_index"
INDEX_VERSION = 4

_SOURCE_TYPES = {
    "(최종)과실비율심의사례_(54MB).pdf": "deliberation_case",
    "230630_자동차사고 과실비율 인정기준_최종.pdf": "fault_standard",
    "250624_2차로형 회전교차로사고 과실비율 비정형기준.pdf": (
        "roundabout_special_standard"
    ),
}
_MIN_CONTENT_PAGES = {
    "(최종)과실비율심의사례_(54MB).pdf": 18,
    "230630_자동차사고 과실비율 인정기준_최종.pdf": 10,
    "250624_2차로형 회전교차로사고 과실비율 비정형기준.pdf": 10,
}
_SECTION_PATTERN = re.compile(
    r"^(사례\s*개요|사고\s*내용|주장\s*내용|입증\s*자료|주요\s*쟁점|"
    r"결정\s*근거|결정\s*이유|사고\s*상황|기본\s*과실비율(?:\s*해설)?|"
    r"수정요소[^\n]*|관련\s*법규|참고\s*판례)\b"
)


def _embedding_model() -> str:
    return os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")


def _clean_page_text(text: str) -> str:
    text = text.replace("\x00", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    compact = []
    blank = False
    for line in lines:
        if line:
            compact.append(line)
            blank = False
        elif not blank:
            compact.append("")
            blank = True
    return "\n".join(compact).strip()


def extract_pdf_pages(pdf_path: str | Path) -> list[str]:
    path = Path(pdf_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"RAG 원본 PDF를 찾을 수 없습니다: {path}")
    executable = shutil.which("pdftotext")
    if not executable:
        raise RuntimeError(
            "PDF 텍스트 추출에 필요한 pdftotext(poppler-utils)가 설치되어 있지 않습니다."
        )
    completed = subprocess.run(
        [executable, "-layout", str(path), "-"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    decoded = completed.stdout.decode("utf-8", errors="replace")
    return [_clean_page_text(page) for page in decoded.split("\f")]


def _split_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            boundary = max(
                text.rfind("\n\n", start, end),
                text.rfind(". ", start, end),
            )
            if boundary > start + max_chars // 2:
                end = boundary + 1
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(start + 1, end - overlap_chars)
    return [chunk for chunk in chunks if chunk]


def _unit_marker(text: str) -> tuple[Optional[str], Optional[str]]:
    case = re.search(r"심의번호\s+([0-9]{4}-[0-9]{4,})", text)
    if case:
        return "case", case.group(1)
    roundabout = re.search(r"(?m)^\s*(회전-\d+)\b", text)
    if roundabout:
        return "roundabout_chart", roundabout.group(1)
    standard = re.search(r"(?m)^\s*((?:보|거|차)\d+(?:-\d+)?)\s+", text)
    if standard:
        return "standard_chart", standard.group(1)
    return None, None


def _extract_chart_number(text: str, unit_kind: Optional[str], unit_id: Optional[str]):
    if unit_kind in {"standard_chart", "roundabout_chart"}:
        return unit_id
    # 심의사례는 본문보다 상단의 참고도표 번호가 우선이다. 괄호 세부유형을 보존한다.
    parenthetical = re.search(r"\b([1-9]\d{2}\([가-힣]\))", text[:2500])
    if parenthetical:
        return parenthetical.group(1)
    standalone = re.search(r"(?m)^\s*([1-9]\d{2})\s*$", text[:1800])
    if standalone:
        return standalone.group(1)
    explicit = re.search(
        r"(?:참고\s*인정기준|과실도표|도표)\D{0,40}([1-9]\d{0,2}(?:\([가-힣]\))?)",
        text,
    )
    return explicit.group(1) if explicit else None


def _extract_ratio(text: str) -> Optional[str]:
    patterns = [
        r"결정비율[^\n]{0,100}?(\d{1,3})\s*[:：]\s*(\d{1,3})",
        r"기본\s*비율[^\n]{0,100}?(\d{1,3})\s*[:：]\s*(\d{1,3})",
        r"기본\s*과실비율[^\n]{0,100}?A\s*(\d{1,3})\s*B\s*(\d{1,3})",
        r"기본\s*과실비율[^\n]{0,100}?레드\s*(\d{1,3})\s*블루\s*(\d{1,3})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return f"{match.group(1)}:{match.group(2)}"
    return None


def _extract_metadata(
    text: str,
    *,
    unit_kind: Optional[str] = None,
    unit_id: Optional[str] = None,
) -> dict:
    case_match = re.search(r"심의번호\s+([0-9]{4}-[0-9]{4,})", text)
    return {
        "case_number": case_match.group(1) if case_match else None,
        "chart_number": _extract_chart_number(text, unit_kind, unit_id),
        "decision_ratio": _extract_ratio(text),
    }


def _make_parent(
    *,
    source_type: str,
    path: Path,
    page_items: list[tuple[int, str]],
    unit_kind: Optional[str],
    unit_id: Optional[str],
) -> dict:
    page_start = page_items[0][0]
    page_end = page_items[-1][0]
    full_text = "\n\n".join(
        f"[PDF {page_number}쪽]\n{text}" for page_number, text in page_items
    )
    metadata = _extract_metadata(full_text, unit_kind=unit_kind, unit_id=unit_id)
    stable_id = unit_id or f"page-{page_start}"
    return {
        "parent_id": "",  # 전체 문서 순서가 정해진 뒤 안정적인 번호를 넣는다.
        "unit_key": f"{source_type}:{stable_id}",
        "unit_type": unit_kind or "reference_page",
        "source_type": source_type,
        "source_file": path.name,
        "source_path": str(path),
        "page_start": page_start,
        "page_end": page_end,
        "full_text": full_text,
        **metadata,
    }


def create_parent_documents(
    pdf_paths: Iterable[str | Path] = DEFAULT_PDF_PATHS,
    *,
    page_extractor: Callable[[str | Path], list[str]] = extract_pdf_pages,
) -> list[dict]:
    """PDF 페이지를 실제 심의사례/과실도표 경계로 묶은 Parent를 만든다."""
    parents = []
    for pdf_path in pdf_paths:
        path = Path(pdf_path).expanduser().resolve()
        source_type = _SOURCE_TYPES.get(path.name, "fault_standard")
        pages = page_extractor(path)
        first_content_page = _MIN_CONTENT_PAGES.get(path.name, 1)
        active_pages: list[tuple[int, str]] = []
        active_kind: Optional[str] = None
        active_id: Optional[str] = None

        def flush_active():
            nonlocal active_pages, active_kind, active_id
            if active_pages:
                parents.append(
                    _make_parent(
                        source_type=source_type,
                        path=path,
                        page_items=active_pages,
                        unit_kind=active_kind,
                        unit_id=active_id,
                    )
                )
            active_pages, active_kind, active_id = [], None, None

        for page_number, page_text in enumerate(pages, start=1):
            if page_number < first_content_page or len(page_text) < 10:
                continue
            marker_kind, marker_id = _unit_marker(page_text)
            if marker_id:
                flush_active()
                active_pages = [(page_number, page_text)]
                active_kind, active_id = marker_kind, marker_id
                continue

            if active_pages:
                max_pages = 6 if active_kind == "case" else 16
                if len(active_pages) < max_pages:
                    active_pages.append((page_number, page_text))
                    continue
                flush_active()

            # 도표가 아닌 총설·해설은 의미가 다른 페이지와 합치지 않는다.
            parents.append(
                _make_parent(
                    source_type=source_type,
                    path=path,
                    page_items=[(page_number, page_text)],
                    unit_kind=None,
                    unit_id=None,
                )
            )
        flush_active()

    for index, parent in enumerate(parents):
        parent["parent_id"] = f"parent_{index:05d}"
    return parents


def _semantic_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current_name = "개요"
    current_lines: list[str] = []
    for line in text.splitlines():
        match = _SECTION_PATTERN.match(re.sub(r"\s+", " ", line).strip())
        if match and current_lines:
            sections.append((current_name, "\n".join(current_lines).strip()))
            current_lines = []
        if match:
            current_name = re.sub(r"\s+", "", match.group(1))
        current_lines.append(line)
    if current_lines:
        sections.append((current_name, "\n".join(current_lines).strip()))
    return [(name, body) for name, body in sections if body]


def create_child_chunks(
    parents: list[dict],
    *,
    max_chars: int = 2200,
    overlap_chars: int = 200,
    min_chars: int = 80,
) -> list[dict]:
    """Parent를 검색에 적합한 의미 섹션과 길이로 나눈 Child를 만든다."""
    children = []
    for parent in parents:
        parent_children = []
        for section_name, section_text in _semantic_sections(parent["full_text"]):
            for part in _split_text(section_text, max_chars, overlap_chars):
                if len(part) < min_chars and parent_children:
                    parent_children[-1]["text"] += "\n" + part
                    parent_children[-1]["embedding_text"] += "\n" + part
                    continue
                prefix = " | ".join(
                    value
                    for value in [
                        f"자료유형 {parent['source_type']}",
                        f"심의번호 {parent['case_number']}" if parent.get("case_number") else None,
                        f"도표 {parent['chart_number']}" if parent.get("chart_number") else None,
                        f"결정비율 {parent['decision_ratio']}" if parent.get("decision_ratio") else None,
                        f"섹션 {section_name}",
                    ]
                    if value
                )
                parent_children.append(
                    {
                        "child_id": "",
                        "parent_id": parent["parent_id"],
                        "source_type": parent["source_type"],
                        "source_file": parent["source_file"],
                        "source_path": parent["source_path"],
                        "page_start": parent["page_start"],
                        "page_end": parent["page_end"],
                        # 이전 호출부와 저장 파일을 위한 호환 필드
                        "page_number": parent["page_start"],
                        "chunk_index": len(parent_children),
                        "section": section_name,
                        "text": part,
                        "embedding_text": f"{prefix}\n{part}",
                        "case_number": parent.get("case_number"),
                        "chart_number": parent.get("chart_number"),
                        "decision_ratio": parent.get("decision_ratio"),
                    }
                )
        if not parent_children:
            continue
        children.extend(parent_children)

    for index, child in enumerate(children):
        child["child_id"] = f"child_{index:05d}"
        child["source_id"] = child["child_id"]  # 구버전 디버깅 도구 호환
    return children


def create_chunks(
    pdf_paths: Iterable[str | Path] = DEFAULT_PDF_PATHS,
    *,
    max_chars: int = 2200,
    overlap_chars: int = 200,
    min_chars: int = 80,
    page_extractor: Callable[[str | Path], list[str]] = extract_pdf_pages,
) -> list[dict]:
    parents = create_parent_documents(pdf_paths, page_extractor=page_extractor)
    return create_child_chunks(
        parents,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
        min_chars=min_chars,
    )


def _openai_embed(texts: list[str], model: str) -> np.ndarray:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY가 설정되어 있지 않습니다.")
    client = OpenAI(api_key=api_key)
    response = client.embeddings.create(model=model, input=texts)
    ordered = sorted(response.data, key=lambda item: item.index)
    return np.asarray([item.embedding for item in ordered], dtype=np.float32)


def _normalize_rows(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.clip(norms, 1e-12, None)


def _fingerprint(
    paths: Iterable[str | Path], model: str, max_chars: int, min_chars: int
) -> str:
    digest = hashlib.sha256()
    digest.update(str(INDEX_VERSION).encode("ascii"))
    digest.update(model.encode("utf-8"))
    digest.update(str(max_chars).encode("ascii"))
    digest.update(str(min_chars).encode("ascii"))
    for item in paths:
        path = Path(item).expanduser().resolve()
        stat = path.stat()
        digest.update(str(path).encode("utf-8"))
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
    return digest.hexdigest()


def build_index(
    *,
    pdf_paths: Iterable[str | Path] = DEFAULT_PDF_PATHS,
    index_dir: str | Path = DEFAULT_INDEX_DIR,
    force: bool = False,
    batch_size: int = 64,
    max_chars: int = 2200,
    min_chars: int = 80,
    embedder: Optional[Callable[[list[str], str], np.ndarray]] = None,
    page_extractor: Callable[[str | Path], list[str]] = extract_pdf_pages,
    progress: Optional[Callable[[str], None]] = None,
) -> Path:
    paths = [Path(item).expanduser().resolve() for item in pdf_paths]
    directory = Path(index_dir).expanduser().resolve()
    manifest_path = directory / "manifest.json"
    parents_path = directory / "parents.jsonl"
    children_path = directory / "children.jsonl"
    embeddings_path = directory / "embeddings.npy"
    model = _embedding_model()
    fingerprint = _fingerprint(paths, model, max_chars, min_chars)

    required_files = [manifest_path, parents_path, children_path, embeddings_path]
    if not force and all(path.is_file() for path in required_files):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("fingerprint") == fingerprint:
            if progress:
                progress(f"기존 Parent-Child RAG 인덱스 사용: {directory}")
            return directory

    if progress:
        progress("과실비율 PDF를 사례·도표 Parent 단위로 추출합니다.")
    parents = create_parent_documents(paths, page_extractor=page_extractor)
    children = create_child_chunks(parents, max_chars=max_chars, min_chars=min_chars)
    if not children:
        raise RuntimeError("RAG 인덱스에 넣을 PDF 텍스트를 추출하지 못했습니다.")

    embed = embedder or _openai_embed
    batches = []
    for start in range(0, len(children), batch_size):
        stop = min(start + batch_size, len(children))
        if progress:
            progress(f"Child 임베딩 생성 중: {stop}/{len(children)}")
        batches.append(
            embed([item["embedding_text"] for item in children[start:stop]], model)
        )
    embeddings = _normalize_rows(np.vstack(batches).astype(np.float32))

    directory.mkdir(parents=True, exist_ok=True)
    outputs = {
        parents_path: "\n".join(json.dumps(item, ensure_ascii=False) for item in parents) + "\n",
        children_path: "\n".join(json.dumps(item, ensure_ascii=False) for item in children) + "\n",
    }
    for destination, content in outputs.items():
        temporary = directory / f".{destination.name}.tmp"
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(destination)
    temporary_embeddings = directory / ".embeddings.npy.tmp"
    with temporary_embeddings.open("wb") as file:
        np.save(file, embeddings)
    temporary_embeddings.replace(embeddings_path)
    temporary_manifest = directory / ".manifest.json.tmp"
    temporary_manifest.write_text(
        json.dumps(
            {
                "fingerprint": fingerprint,
                "index_version": INDEX_VERSION,
                "index_strategy": "parent_child_hybrid",
                "embedding_model": model,
                "parent_count": len(parents),
                "child_count": len(children),
                "chunk_count": len(children),
                "embedding_dimensions": int(embeddings.shape[1]),
                "source_files": [str(path) for path in paths],
                "max_chars": max_chars,
                "min_chars": min_chars,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary_manifest.replace(manifest_path)
    # 구버전 페이지 청크를 새 인덱스로 오인하지 않도록 제거한다.
    legacy_chunks = directory / "chunks.jsonl"
    if legacy_chunks.is_file():
        legacy_chunks.unlink()
    if progress:
        progress(
            f"Parent-Child RAG 인덱스 생성 완료: Parent {len(parents)}개, Child {len(children)}개"
        )
    return directory


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_index(index_dir: str | Path = DEFAULT_INDEX_DIR) -> tuple[list[dict], np.ndarray, dict]:
    directory = Path(index_dir).expanduser().resolve()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    children = _read_jsonl(directory / "children.jsonl")
    embeddings = np.load(directory / "embeddings.npy")
    if len(children) != embeddings.shape[0]:
        raise ValueError("RAG Child 수와 임베딩 행 수가 일치하지 않습니다.")
    return children, embeddings, manifest


def load_parent_documents(index_dir: str | Path = DEFAULT_INDEX_DIR) -> list[dict]:
    directory = Path(index_dir).expanduser().resolve()
    return _read_jsonl(directory / "parents.jsonl")


def _tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[가-힣A-Za-z0-9]+", text)
        if len(token) >= 2
    }


def _lexical_scores(query: str, children: list[dict]) -> np.ndarray:
    query_tokens = _tokens(query)
    if not query_tokens:
        return np.zeros(len(children), dtype=np.float32)
    child_tokens = [_tokens(item["embedding_text"]) for item in children]
    frequencies = Counter(
        token for tokens in child_tokens for token in query_tokens if token in tokens
    )
    weights = {
        token: math.log((len(children) + 1) / (frequencies[token] + 1)) + 1.0
        for token in query_tokens
    }
    denominator = sum(weights.values()) or 1.0
    return np.asarray(
        [
            sum(weights[token] for token in query_tokens if token in tokens) / denominator
            for tokens in child_tokens
        ],
        dtype=np.float32,
    )


def _parent_excerpt(parent: dict, matched_children: list[dict], max_chars: int = 9000) -> str:
    full_text = parent["full_text"]
    if len(full_text) <= max_chars:
        return full_text
    selected = []
    used = set()
    for child in matched_children:
        signature = child["text"]
        if signature in used:
            continue
        used.add(signature)
        selected.append(f"[{child['section']}]\n{signature}")
        if sum(len(item) for item in selected) >= max_chars:
            break
    return "\n\n".join(selected)[:max_chars]


def search_index(
    query: str,
    *,
    index_dir: str | Path = DEFAULT_INDEX_DIR,
    top_k: int = 12,
    ensure_source_diversity: bool = True,
    embedder: Optional[Callable[[list[str], str], np.ndarray]] = None,
    source_types: Optional[set[str]] = None,
) -> list[RetrievedSource]:
    """source_types를 주면 해당 자료 유형(deliberation_case 등)만 대상으로 검색한다."""
    children, embeddings, manifest = load_index(index_dir)
    parents = load_parent_documents(index_dir)
    parents_by_id = {item["parent_id"]: item for item in parents}
    model = manifest["embedding_model"]
    embed = embedder or _openai_embed
    query_vector = _normalize_rows(embed([query], model).astype(np.float32))[0]
    semantic_scores = embeddings @ query_vector
    lexical_scores = _lexical_scores(query, children)
    child_scores = semantic_scores * 0.82 + lexical_scores * 0.18

    by_parent: dict[str, list[int]] = defaultdict(list)
    for child_index, child in enumerate(children):
        by_parent[child["parent_id"]].append(child_index)
    parent_scores = {}
    for parent_id, indices in by_parent.items():
        ordered = sorted(indices, key=lambda value: float(child_scores[value]), reverse=True)
        best = float(child_scores[ordered[0]])
        supporting = [float(child_scores[index]) for index in ordered[1:3]]
        parent_scores[parent_id] = best if not supporting else best * 0.88 + np.mean(supporting) * 0.12
    ranked_parent_ids = sorted(parent_scores, key=parent_scores.get, reverse=True)
    if source_types:
        ranked_parent_ids = [
            parent_id for parent_id in ranked_parent_ids if parents_by_id[parent_id]["source_type"] in source_types
        ]
        ensure_source_diversity = False
        if not ranked_parent_ids:
            return []

    limit = max(1, min(top_k, len(ranked_parent_ids)))
    selected_ids = ranked_parent_ids[:limit]
    if ensure_source_diversity and limit >= 6:
        selected_ids = ranked_parent_ids[: max(1, limit - 6)]
        selected_set = set(selected_ids)
        for source_type in dict.fromkeys(_SOURCE_TYPES.values()):
            added = 0
            for parent_id in ranked_parent_ids:
                if parents_by_id[parent_id]["source_type"] != source_type:
                    continue
                if parent_id not in selected_set:
                    selected_ids.append(parent_id)
                    selected_set.add(parent_id)
                added += 1
                if added >= 2:
                    break
        for parent_id in ranked_parent_ids:
            if len(selected_ids) >= limit:
                break
            if parent_id not in selected_set:
                selected_ids.append(parent_id)
                selected_set.add(parent_id)
        selected_ids = selected_ids[:limit]

    results = []
    for parent_id in selected_ids:
        parent = parents_by_id[parent_id]
        child_indices = sorted(
            by_parent[parent_id], key=lambda value: float(child_scores[value]), reverse=True
        )
        matched = [children[index] for index in child_indices[:3]]
        results.append(
            RetrievedSource(
                source_id=parent_id,
                parent_id=parent_id,
                source_type=parent["source_type"],
                source_file=parent["source_file"],
                page_number=parent["page_start"],
                page_start=parent["page_start"],
                page_end=parent["page_end"],
                chunk_index=matched[0]["chunk_index"],
                similarity_score=float(parent_scores[parent_id]),
                semantic_score=float(max(semantic_scores[index] for index in child_indices)),
                lexical_score=float(max(lexical_scores[index] for index in child_indices)),
                case_number=parent.get("case_number"),
                chart_number=parent.get("chart_number"),
                decision_ratio=parent.get("decision_ratio"),
                matched_child_ids=[item["child_id"] for item in matched],
                matched_sections=list(dict.fromkeys(item["section"] for item in matched)),
                excerpt=_parent_excerpt(parent, matched),
            )
        )
    return results
