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
    print(f"RAG 인덱스 위치: {directory}")


if __name__ == "__main__":
    main()
