"""1차 전체 분석 + focus 재분석 결과 Merge / Reconcile (가이드 56절)."""

from __future__ import annotations

from typing import Optional

from video.schemas import (
    Observation,
    RoadEnvironment,
    VehicleEntry,
    VideoResult,
)

_STATUS_RANK = {"CONFIRMED": 2, "INFERRED": 1, "UNKNOWN": 0}


def _better(candidate: Observation, current: Observation) -> bool:
    if candidate.value is None:
        return False
    if current.value is None:
        return True
    return (_STATUS_RANK[candidate.status], candidate.confidence) > (
        _STATUS_RANK[current.status],
        current.confidence,
    )


def _merge_observation_fields(base, newer) -> None:
    for name in type(base).model_fields:
        current = getattr(base, name)
        candidate = getattr(newer, name)
        if isinstance(current, Observation) and isinstance(candidate, Observation):
            if _better(candidate, current):
                setattr(base, name, candidate.model_copy(deep=True))
        elif isinstance(current, str) and isinstance(candidate, str):
            if candidate and not current:
                setattr(base, name, candidate)
        elif isinstance(current, list) and isinstance(candidate, list) and name == "signal_observations":
            seen = {(item.time, item.applies_to, item.color) for item in current}
            for item in candidate:
                key = (item.time, item.applies_to, item.color)
                if key not in seen:
                    current.append(item.model_copy(deep=True))
                    seen.add(key)
        elif current is None and candidate is not None:
            setattr(base, name, candidate)


def _merge_vehicle(base: VehicleEntry, newer: VehicleEntry) -> None:
    for name in ("description", "vehicle_type", "color", "first_seen", "last_seen", "first_seen_position", "movement", "entry_direction", "lane"):
        current = getattr(base, name)
        candidate = getattr(newer, name)
        if candidate and (not current or str(current).lower() in {"unknown", "none"}):
            setattr(base, name, candidate)
    base.is_ego = base.is_ego or newer.is_ego
    _merge_observation_fields(base, newer)


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in items if item and item.strip()))


def merge_video_results(*results: VideoResult) -> VideoResult:
    """첫 결과를 기준으로 이후 결과(focus / tracking)를 보수적으로 병합한다."""
    if not results:
        raise ValueError("병합할 결과가 없습니다.")
    merged = results[0].model_copy(deep=True)
    backends = {results[0].video_backend}
    identity_notes: list[str] = []

    for newer in results[1:]:
        backends.add(newer.video_backend)
        base_ids = set(merged.vehicle_ids())
        for vehicle in newer.vehicles:
            existing = merged.get_vehicle(vehicle.id)
            if existing is None:
                merged.vehicles.append(vehicle.model_copy(deep=True))
                if base_ids:
                    identity_notes.append(
                        f"재분석에서 새 vehicle ID {vehicle.id}가 추가됨 — identity 확인 필요"
                    )
            else:
                _merge_vehicle(existing, vehicle)
        if newer.ego_vehicle_id and not merged.ego_vehicle_id:
            merged.ego_vehicle_id = newer.ego_vehicle_id

        merged.vehicle_identity_consistent = merged.vehicle_identity_consistent and newer.vehicle_identity_consistent

        if newer.collision_window.confidence >= merged.collision_window.confidence and newer.collision_window.detected():
            merged.collision_window = newer.collision_window.model_copy(deep=True)

        newer_pair = newer.collision_pair
        if len(newer_pair.participants) == 2 and newer_pair.confidence >= merged.collision_pair.confidence:
            previous = merged.collision_pair.participants
            merged.collision_pair = newer_pair.model_copy(deep=True)
            if previous and set(previous) != set(newer_pair.participants):
                merged.changes_from_previous.append(
                    f"collision pair 변경: {previous} → {newer_pair.participants}"
                )
        elif len(newer_pair.participants) == 2 and set(newer_pair.participants) != set(merged.collision_pair.participants):
            if newer_pair.participants not in merged.collision_pair.alternative_pairs:
                merged.collision_pair.alternative_pairs.append(list(newer_pair.participants))
        if newer.pair_scores:
            merged.pair_scores = [item.model_copy(deep=True) for item in newer.pair_scores]

        _merge_observation_fields(merged.collision, newer.collision)
        if newer.collision.participant_parts and not merged.collision.participant_parts:
            merged.collision.participant_parts = [item.model_copy(deep=True) for item in newer.collision.participant_parts]
        elif newer.collision.participant_parts:
            known = {item.vehicle_id for item in merged.collision.participant_parts}
            for item in newer.collision.participant_parts:
                if item.vehicle_id not in known:
                    merged.collision.participant_parts.append(item.model_copy(deep=True))

        _merge_observation_fields(merged.road_environment, newer.road_environment)

        seen_events = {(item.start_time, item.event) for item in merged.timeline}
        for item in newer.timeline:
            key = (item.start_time, item.event)
            if key not in seen_events:
                merged.timeline.append(item.model_copy(deep=True))
                seen_events.add(key)
        merged.timeline.sort(key=lambda item: _sort_key(item.start_time))

        seen_snapshots = {item.time for item in merged.tracking_timeline}
        for item in newer.tracking_timeline:
            if item.time not in seen_snapshots:
                merged.tracking_timeline.append(item.model_copy(deep=True))
                seen_snapshots.add(item.time)
        merged.tracking_timeline.sort(key=lambda item: _sort_key(item.time))

        merged.confirmed_facts = _dedupe(merged.confirmed_facts + newer.confirmed_facts)
        merged.inferred_facts = _dedupe(
            [fact for fact in merged.inferred_facts if fact not in merged.confirmed_facts]
            + [fact for fact in newer.inferred_facts if fact not in merged.confirmed_facts]
        )
        merged.unknown_or_unobservable = _dedupe(merged.unknown_or_unobservable + newer.unknown_or_unobservable)
        merged.uncertain_facts = _dedupe(merged.uncertain_facts + newer.uncertain_facts)

        factors = {item.factor: item for item in merged.fault_relevant_factors}
        for item in newer.fault_relevant_factors:
            current = factors.get(item.factor)
            if current is None or _STATUS_RANK[item.observability] > _STATUS_RANK[current.observability]:
                factors[item.factor] = item.model_copy(deep=True)
        merged.fault_relevant_factors = list(factors.values())

        merged.changes_from_previous = _dedupe(merged.changes_from_previous + newer.changes_from_previous)
        merged.recommended_reanalysis_targets = _dedupe(newer.recommended_reanalysis_targets)
        merged.recommended_dense_sampling = newer.recommended_dense_sampling or merged.recommended_dense_sampling

        # 재분석 서술은 changes_from_previous를 비워 보내도 버리지 않는다 (2차 분석의 상세 서술이 사건경위서 재료가 된다)
        newer_description = newer.detailed_description.strip()
        if newer_description and newer_description not in merged.detailed_description:
            merged.detailed_description = (merged.detailed_description + "\n\n[재분석 보완] " + newer_description).strip()
            if not newer.changes_from_previous:
                merged.changes_from_previous.append("재분석 상세 서술 보완")
        if not merged.short_summary and newer.short_summary:
            merged.short_summary = newer.short_summary

        merged.analysis_passes = merged.analysis_passes + [item.model_copy(deep=True) for item in newer.analysis_passes]

    if identity_notes:
        merged.uncertain_facts = _dedupe(merged.uncertain_facts + identity_notes)
    if len(backends) > 1:
        merged.video_backend = "merged"
    merged.cached = False
    return merged


def _sort_key(value: Optional[str]) -> float:
    from common.timeutil import parse_timestamp

    parsed = parse_timestamp(value)
    return parsed if parsed is not None else 1e9
