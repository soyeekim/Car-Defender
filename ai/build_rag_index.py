import argparse
from pathlib import Path

from rag.pdf_index import DEFAULT_INDEX_DIR, build_index


def main():
    parser = argparse.ArgumentParser(
        description="로컬 과실비율 PDF를 검색 가능한 임베딩 인덱스로 만듭니다."
    )
    parser.add_argument("--force", action="store_true", help="기존 캐시를 무시하고 재생성")
    parser.add_argument(
        "--index-dir",
        type=Path,
        default=DEFAULT_INDEX_DIR,
        help="인덱스 저장 경로",
    )
    args = parser.parse_args()
    directory = build_index(
        index_dir=args.index_dir,
        force=args.force,
        progress=print,
    )
    # 사례 단위 문서(case_documents.jsonl)는 parents 에서 파생된다. 인덱스를 새로 만들면 같이 다시 만들어야
    # 예전 추출 결과가 남지 않는다 (load_case_documents 는 파일이 없을 때만 만든다).
    from rag.case_documents import build_case_documents

    documents = build_case_documents(directory)
    print(f"사례 문서 재생성: {len(documents)}건 → {Path(directory) / 'case_documents.jsonl'}")
    print(f"RAG 인덱스 위치: {directory}")


if __name__ == "__main__":
    main()
