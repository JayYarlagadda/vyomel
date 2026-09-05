"""Fixture camera perception for gym / multimodal scenes (FR-1101)."""

from __future__ import annotations

import json
from pathlib import Path

from vyomel.core.errors import ErrorCode, ToolError
from vyomel.core.ids import new_id
from vyomel.perception.types import BoundingBox, Detection, FrameObservation

_FRAME_MAGIC = b"VYOMEL_CAMERA_FRAME\n"

# Built-in gym floor scene used when no fixture path is supplied.
DEFAULT_GYM_SCENE: dict[str, object] = {
    "source": "fixture://gym/floor_a",
    "width": 1280,
    "height": 720,
    "detections": [
        {
            "label": "barbell",
            "category": "equipment",
            "confidence": 0.97,
            "bbox": {"x": 0.2, "y": 0.5, "w": 0.4, "h": 0.1},
            "attributes": {"plates_kg": 60},
        },
        {
            "label": "squat_rack",
            "category": "equipment",
            "confidence": 0.95,
            "bbox": {"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.7},
        },
        {
            "label": "dumbbells",
            "category": "equipment",
            "confidence": 0.93,
            "bbox": {"x": 0.55, "y": 0.4, "w": 0.3, "h": 0.25},
            "attributes": {"pair_kg": 22.5},
        },
        {
            "label": "cable_machine",
            "category": "equipment",
            "confidence": 0.9,
            "bbox": {"x": 0.7, "y": 0.05, "w": 0.25, "h": 0.8},
        },
        {
            "label": "bench",
            "category": "equipment",
            "confidence": 0.92,
            "bbox": {"x": 0.35, "y": 0.55, "w": 0.25, "h": 0.2},
        },
        {
            "label": "person",
            "category": "person",
            "confidence": 0.88,
            "bbox": {"x": 0.45, "y": 0.2, "w": 0.15, "h": 0.5},
        },
    ],
}


def encode_frame(scene: dict[str, object]) -> bytes:
    return _FRAME_MAGIC + json.dumps(scene, ensure_ascii=True).encode("utf-8")


def _as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return float(value)
    return default


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return default


def _parse_detection(raw: dict[str, object]) -> Detection:
    bbox_raw = raw.get("bbox")
    bbox = None
    if isinstance(bbox_raw, dict):
        bbox = BoundingBox(
            x=_as_float(bbox_raw.get("x")),
            y=_as_float(bbox_raw.get("y")),
            w=_as_float(bbox_raw.get("w")),
            h=_as_float(bbox_raw.get("h")),
        )
    attrs = raw.get("attributes")
    return Detection(
        label=str(raw["label"]),
        category=str(raw.get("category") or "object"),
        confidence=_as_float(raw.get("confidence"), 1.0),
        bbox=bbox,
        attributes=dict(attrs) if isinstance(attrs, dict) else {},
    )


def observe_frame(data: bytes | None = None, *, scene_path: Path | None = None) -> FrameObservation:
    """Return structured detections from fixture bytes or a JSON scene file."""
    if scene_path is not None:
        if not scene_path.exists():
            raise ToolError(
                "camera fixture scene missing",
                code=ErrorCode.PRECONDITION_FAILED,
                observation=str(scene_path),
            )
        payload = json.loads(scene_path.read_text(encoding="utf-8"))
    elif data is None:
        payload = dict(DEFAULT_GYM_SCENE)
    elif data.startswith(_FRAME_MAGIC):
        payload = json.loads(data[len(_FRAME_MAGIC) :].decode("utf-8"))
    else:
        raise ToolError(
            "live camera backend is not installed; use a fixture frame",
            code=ErrorCode.UNSUPPORTED,
        )

    detections_raw = payload.get("detections") or []
    if not isinstance(detections_raw, list):
        raise ToolError("detections must be a list", code=ErrorCode.INVALID_PARAMETERS)
    detections = [_parse_detection(item) for item in detections_raw if isinstance(item, dict)]
    return FrameObservation(
        frame_id=new_id(),
        source=str(payload.get("source") or "fixture"),
        width=_as_int(payload.get("width"), 1280),
        height=_as_int(payload.get("height"), 720),
        detections=detections,
        backend="fixture",
    )


def equipment_labels(frame: FrameObservation) -> list[str]:
    return sorted(
        {d.label for d in frame.detections if d.category == "equipment" and d.confidence >= 0.5}
    )
