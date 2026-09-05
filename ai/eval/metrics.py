"""평가 지표 (가이드 21, 43~44, 49.6, 102절).

Video : Vehicle Count Accuracy, Collision Pair Accuracy, Collision Timestamp Error,
        Participant Precision/Recall, Identity Consistency, Road Type / Signal Accuracy
RAG   : Recall@k, MRR
Fault : exact match, ±10% accuracy, MAE
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from common.timeutil import parse_timestamp
from eval.dataset_schema import VideoGroundTruth
from video.schemas import VideoResult


@dataclass
class VideoItemMetrics:
    video_id: str
    vehicle_count_correct: Optional[bool] = None
    collision_pair_correct: Optional[bool] = None
    participant_precision: Optional[float] = None
    participant_recall: Optional[float] = None
    collision_timestamp_error_sec: Optional[float] = None
    identity_consistent: bool = True
    road_type_correct: Optional[bool] = None
    signal_present_correct: Optional[bool] = None
    fault_fact_recall: Optional[float] = None
    completion_status: str = ""
    completion_score: int = 0
    latency_sec: float = 0.0
    passes: int = 0


def _norm(value: Optional[str]) -> str:
    return (value or "").strip().lower().replace(" ", "_")


def evaluate_video_item(video_id: str, result: VideoResult, truth: VideoGroundTruth) -> VideoItemMetrics:
    metrics = VideoItemMetrics(video_id=video_id)
    predicted = set(result.collision_pair.participants)
    expected = set(truth.collision_pair)
    if truth.vehicle_count is not None:
        metrics.vehicle_count_correct = len(result.vehicles) == truth.vehicle_count
    if expected:
        metrics.collision_pair_correct = predicted == expected
        true_positive = len(predicted & expected)
        metrics.participant_precision = true_positive / len(predicted) if predicted else 0.0
        metrics.participant_recall = true_positive / len(expected)
    if truth.collision_timestamp_sec is not None:
        predicted_ts = parse_timestamp(result.collision_pair.timestamp or result.collision_window.most_likely_timestamp or result.collision.timestamp)
        if predicted_ts is not None:
            metrics.collision_timestamp_error_sec = abs(predicted_ts - truth.collision_timestamp_sec)
    metrics.identity_consistent = result.vehicle_identity_consistent
    if truth.road_type:
        metrics.road_type_correct = _norm(truth.road_type) in _norm(result.road_environment.road_type.value) or _norm(truth.road_type) in _norm(result.road_environment.intersection_type.value)
    if truth.signal_present is not None:
        value = _norm(result.road_environment.signal_present.value)
        metrics.signal_present_correct = (value in {"true", "yes"}) == truth.signal_present if value else None
    if truth.fault_relevant_facts:
        blob = " ".join([*result.confirmed_facts, *result.inferred_facts, *(f.factor + " " + f.note for f in result.fault_relevant_factors)]).lower()
        hits = sum(1 for fact in truth.fault_relevant_facts if fact.lower() in blob)
        metrics.fault_fact_recall = hits / len(truth.fault_relevant_facts)
    metrics.completion_status = result.analysis_completion.status
    metrics.completion_score = result.analysis_completion.score
    metrics.latency_sec = sum(item.latency_sec for item in result.analysis_passes)
    metrics.passes = len(result.analysis_passes)
    return metrics


@dataclass
class VideoAggregate:
    count: int = 0
    vehicle_count_accuracy: Optional[float] = None
    collision_pair_accuracy: Optional[float] = None
    participant_precision: Optional[float] = None
    participant_recall: Optional[float] = None
    mean_timestamp_error_sec: Optional[float] = None
    identity_consistency_rate: float = 0.0
    road_type_accuracy: Optional[float] = None
    signal_accuracy: Optional[float] = None
    fault_fact_recall: Optional[float] = None
    completion_rate: float = 0.0
    average_latency_sec: float = 0.0
    average_passes: float = 0.0
    items: list[VideoItemMetrics] = field(default_factory=list)


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    collected = [float(value) for value in values if value is not None]
    return sum(collected) / len(collected) if collected else None


def aggregate_video_metrics(items: list[VideoItemMetrics]) -> VideoAggregate:
    aggregate = VideoAggregate(count=len(items), items=items)
    if not items:
        return aggregate
    aggregate.vehicle_count_accuracy = _mean(None if item.vehicle_count_correct is None else float(item.vehicle_count_correct) for item in items)
    aggregate.collision_pair_accuracy = _mean(None if item.collision_pair_correct is None else float(item.collision_pair_correct) for item in items)
    aggregate.participant_precision = _mean(item.participant_precision for item in items)
    aggregate.participant_recall = _mean(item.participant_recall for item in items)
    aggregate.mean_timestamp_error_sec = _mean(item.collision_timestamp_error_sec for item in items)
    aggregate.identity_consistency_rate = sum(item.identity_consistent for item in items) / len(items)
    aggregate.road_type_accuracy = _mean(None if item.road_type_correct is None else float(item.road_type_correct) for item in items)
    aggregate.signal_accuracy = _mean(None if item.signal_present_correct is None else float(item.signal_present_correct) for item in items)
    aggregate.fault_fact_recall = _mean(item.fault_fact_recall for item in items)
    aggregate.completion_rate = sum(item.completion_status == "VIDEO_ANALYSIS_COMPLETE" for item in items) / len(items)
    aggregate.average_latency_sec = sum(item.latency_sec for item in items) / len(items)
    aggregate.average_passes = sum(item.passes for item in items) / len(items)
    return aggregate


# --------------------------------------------------------------------------- RAG


def recall_at_k(ranked_ids: list[str], relevant_ids: Iterable[str], k: int) -> float:
    relevant = set(relevant_ids)
    if not relevant:
        return 0.0
    return len(set(ranked_ids[:k]) & relevant) / len(relevant)


def reciprocal_rank(ranked_ids: list[str], relevant_ids: Iterable[str]) -> float:
    relevant = set(relevant_ids)
    for index, case_id in enumerate(ranked_ids, start=1):
        if case_id in relevant:
            return 1.0 / index
    return 0.0


def rag_metrics(results: list[tuple[list[str], list[str]]], ks: tuple[int, ...] = (1, 3, 5)) -> dict[str, float]:
    """results: [(ranked_case_ids, relevant_case_ids), ...]"""
    if not results:
        return {}
    summary = {f"recall@{k}": sum(recall_at_k(ranked, relevant, k) for ranked, relevant in results) / len(results) for k in ks}
    summary["mrr"] = sum(reciprocal_rank(ranked, relevant) for ranked, relevant in results) / len(results)
    return summary


# --------------------------------------------------------------------------- Fault


def parse_ratio_text(text: str) -> Optional[tuple[int, int]]:
    from assessment.fault_ratio import parse_ratio

    return parse_ratio(text)


def fault_metrics(pairs: list[tuple[str, str]], tolerance: int = 10) -> dict[str, float]:
    """pairs: [(predicted 'user:opponent', expected 'user:opponent'), ...]"""
    exact = within = 0
    errors = []
    for predicted, expected in pairs:
        p = parse_ratio_text(predicted)
        e = parse_ratio_text(expected)
        if p is None or e is None:
            continue
        diff = abs(p[0] - e[0])
        errors.append(diff)
        exact += diff == 0
        within += diff <= tolerance
    if not errors:
        return {}
    return {
        "exact_match": exact / len(errors),
        f"within_{tolerance}_accuracy": within / len(errors),
        "mae": sum(errors) / len(errors),
        "count": len(errors),
    }
