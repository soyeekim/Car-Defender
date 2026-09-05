"""Gemini Native vs GPT Frames 비교 실험 (가이드 49.6~49.7절).

    cd ai
    python -m eval.compare_backends --dataset data/eval/video_annotations.json \
        --backends gemini_native gpt_frames --output logs/backend_comparison.json

dataset 형식:
{
  "items": [
    {"video_id": "accident_001", "video_path": "data/mp4/xxx.mp4", "description": "...",
     "ground_truth": {"vehicle_count": 3, "collision_pair": ["vehicle_1", "vehicle_3"], "collision_timestamp_sec": 5.8}}
  ]
}
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.video_agent import VideoAnalysisAgent  # noqa: E402
from eval.dataset_schema import VideoEvalDataset  # noqa: E402
from eval.metrics import aggregate_video_metrics, evaluate_video_item  # noqa: E402
from settings import get_settings  # noqa: E402
from video.base import get_video_analyzer  # noqa: E402


def run_comparison(dataset_path: Path, backends: list[str], *, output: Path, use_cache: bool, frame_intervals: list[float]) -> dict:
    dataset = VideoEvalDataset.model_validate_json(dataset_path.read_text(encoding="utf-8"))
    settings = get_settings()
    report: dict = {"created_at": datetime.now().isoformat(), "dataset": str(dataset_path), "backends": {}, "items": []}
    per_item: dict[str, dict] = {item.video_id: {"video_id": item.video_id, "ground_truth": item.ground_truth.model_dump()} for item in dataset.items}

    configs: list[tuple[str, dict]] = []
    for backend in backends:
        if backend == "gpt_frames":
            for interval in frame_intervals:
                configs.append((f"gpt_frames_{str(interval).replace('.', '')}", {"backend": "gpt_frames", "interval": interval}))
        else:
            configs.append((backend, {"backend": backend, "interval": None}))

    for name, config in configs:
        if config["backend"] == "gpt_frames":
            from video.gpt_frames_analyzer import GPTFrameVideoAnalyzer

            analyzer = GPTFrameVideoAnalyzer(settings=settings.video, interval_sec=config["interval"])
        else:
            analyzer = get_video_analyzer(config["backend"], settings=settings.video)
        agent = VideoAnalysisAgent(analyzer=analyzer, settings=settings)
        metrics = []
        for item in dataset.items:
            started = time.monotonic()
            try:
                decision = agent.run_policy(item.video_path, case_id=f"eval_{item.video_id}", extra_context=item.description, progress=lambda m: print(f"  · {m}", file=sys.stderr))
                elapsed = time.monotonic() - started
                if decision.result is None:
                    raise RuntimeError(decision.error or "no result")
                item_metrics = evaluate_video_item(item.video_id, decision.result, item.ground_truth)
                item_metrics.latency_sec = elapsed
                metrics.append(item_metrics)
                per_item[item.video_id][name] = {
                    **asdict(item_metrics),
                    "decision": decision.status,
                    "predicted_pair": decision.result.collision_pair.participants,
                    "vehicle_count": len(decision.result.vehicles),
                    "token_usage": [p.token_usage for p in decision.result.analysis_passes],
                }
                print(f"[{name}] {item.video_id}: pair_correct={item_metrics.collision_pair_correct} latency={elapsed:.1f}s", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                per_item[item.video_id][name] = {"error": str(exc)[:300]}
                print(f"[{name}] {item.video_id}: 실패 {exc}", file=sys.stderr)
        aggregate = aggregate_video_metrics(metrics)
        summary = asdict(aggregate)
        summary.pop("items", None)
        report["backends"][name] = summary
    report["items"] = list(per_item.values())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Video backend 비교 실험")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--backends", nargs="+", default=["gemini_native", "gpt_frames"])
    parser.add_argument("--frame-intervals", nargs="+", type=float, default=[0.5, 0.25])
    parser.add_argument("--output", type=Path, default=Path("logs") / f"backend_comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()
    report = run_comparison(args.dataset, args.backends, output=args.output, use_cache=not args.no_cache, frame_intervals=args.frame_intervals)
    print(json.dumps(report["backends"], ensure_ascii=False, indent=2))
    print(f"결과 저장: {args.output}")


if __name__ == "__main__":
    main()
