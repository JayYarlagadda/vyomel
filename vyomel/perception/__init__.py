"""Environment observation: camera fixtures and multimodal verticals (M17)."""

from vyomel.perception.camera import (
    DEFAULT_GYM_SCENE,
    encode_frame,
    equipment_labels,
    observe_frame,
)
from vyomel.perception.gym import build_gym_session, default_history
from vyomel.perception.types import (
    Detection,
    FrameObservation,
    GymSessionPlan,
    TrainingPreference,
)

__all__ = [
    "DEFAULT_GYM_SCENE",
    "Detection",
    "FrameObservation",
    "GymSessionPlan",
    "TrainingPreference",
    "build_gym_session",
    "default_history",
    "encode_frame",
    "equipment_labels",
    "observe_frame",
]
