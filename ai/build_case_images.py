"""심의사례·인정기준 도표 그림을 PDF 에서 잘라 `data/rag_index/images/` 에 만든다.

    cd ai && python build_case_images.py            # 없는 것만 생성
    python build_case_images.py --force              # 전부 다시
    python build_case_images.py --only 2019-036580,회전-4,차1-1

RAG 인덱스(build_rag_index.py)를 만든 뒤, Docker 이미지를 빌드하기 전에 한 번 돌린다.
poppler-utils(pdftotext, pdftoppm)가 필요하다. 결과 manifest.json 을 백엔드가 읽어 심의사례 팝업 그림으로 내려준다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rag.case_images import DEFAULT_DPI, DEFAULT_PDF_DIR, build_case_images, images_dir, poppler_available
from rag.pdf_index import DEFAULT_INDEX_DIR


def main() -> int:
    parser = argparse.ArgumentParser(description="심의사례·인정기준 도표 PNG 생성")
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX_DIR, help="RAG 인덱스 폴더 (parents.jsonl 이 있는 곳)")
    parser.add_argument("--pdf-dir", type=Path, default=DEFAULT_PDF_DIR, help="원본 PDF 3개가 있는 폴더")
    parser.add_argument("--out-dir", type=Path, default=None, help="출력 폴더 (기본: <index-dir>/images)")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    parser.add_argument("--force", action="store_true", help="이미 있는 그림도 다시 만든다")
    parser.add_argument("--only", default="", help="쉼표로 구분한 심의번호/도표 번호만")
    args = parser.parse_args()

    if not poppler_available():
        print("poppler(pdftotext, pdftoppm)가 없다. Ubuntu: sudo apt install poppler-utils", file=sys.stderr)
        return 2
    only = [item.strip() for item in args.only.split(",") if item.strip()] or None
    summary = build_case_images(index_dir=args.index_dir, pdf_dir=args.pdf_dir, out_dir=args.out_dir, dpi=args.dpi, force=args.force, only=only)
    out = args.out_dir or images_dir(args.index_dir)
    print(f"\n완료: 대상 {summary.total} · 생성 {summary.created} · 건너뜀 {summary.skipped} · 실패 {len(summary.failed)} → {out}")
    for item in summary.failed:
        print("  실패:", item)
    return 1 if summary.failed else 0


if __name__ == "__main__":
    sys.exit(main())
