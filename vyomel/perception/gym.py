"""Gym session planner from camera equipment + personal history (FR-1102 / S8)."""

from __future__ import annotations

from vyomel.perception.camera import equipment_labels, observe_frame
from vyomel.perception.types import (
    ExerciseBlock,
    FrameObservation,
    GymSessionPlan,
    TrainingPreference,
)

# Equipment → candidate movements (deterministic; no LLM required for the fixture path).
_MOVEMENTS: dict[str, list[tuple[str, str, str]]] = {
    "squat_rack": [
        ("Back squat", "5x5", "Brace; full depth if mobility allows"),
        ("Rack pull", "3x5", "Above-knee if back is fatigued"),
    ],
    "barbell": [
        ("Romanian deadlift", "3x8", "Soft knees, hinge"),
        ("Barbell row", "3x8", "Chest supported if low back is sore"),
    ],
    "dumbbells": [
        ("DB press", "3x10", "Neutral grip OK"),
        ("DB lunges", "3x8/leg", "Short steps if knees sensitive"),
    ],
    "cable_machine": [
        ("Cable face pull", "3x15", "External rotation finish"),
        ("Cable row", "3x12", "Pause at contraction"),
    ],
    "bench": [
        ("DB or barbell bench", "3x8", "Shoulder blades set"),
        ("Hip thrust", "3x10", "Pause at lockout"),
    ],
    "pull_up_bar": [
        ("Pull-ups", "3xAMRAP", "Band assist OK"),
    ],
    "treadmill": [
        ("Easy incline walk", "10 min", "Zone 2 finish"),
    ],
}


def default_history() -> TrainingPreference:
    """Fixture personal history: recent push day, avoid heavy axial loading."""
    return TrainingPreference(
        goal="strength",
        days_per_week=4,
        avoid=["heavy axial load"],
        recent=["bench press", "overhead press", "cable fly"],
        minutes=45,
    )


def _focus_from_history(pref: TrainingPreference) -> str:
    recent = " ".join(pref.recent).lower()
    if any(tok in recent for tok in ("bench", "press", "fly", "push")):
        return "pull_and_legs"
    if any(tok in recent for tok in ("squat", "deadlift", "row", "pull")):
        return "push"
    return "full_body"


def build_gym_session(
    frame: FrameObservation | None = None,
    *,
    preference: TrainingPreference | None = None,
    max_blocks: int = 5,
) -> GymSessionPlan:
    """Build today's session from visible equipment + history (scenario S8)."""
    observed = frame or observe_frame()
    pref = preference or default_history()
    equipment = equipment_labels(observed)
    if not equipment:
        raise ValueError("no equipment detected in frame")

    focus = _focus_from_history(pref)
    avoid_axial = any("axial" in a.lower() for a in pref.avoid)

    blocks: list[ExerciseBlock] = []
    used: list[str] = []
    history_notes = [
        f"goal={pref.goal}",
        f"recent={', '.join(pref.recent) or 'none'}",
        f"focus={focus}",
    ]

    # Prefer non-axial options when history says to avoid heavy axial load.
    priority = list(equipment)
    if avoid_axial and "squat_rack" in priority:
        priority = [e for e in priority if e != "squat_rack"] + ["squat_rack"]

    for label in priority:
        for name, reps, notes in _MOVEMENTS.get(label, []):
            if avoid_axial and label == "squat_rack" and "squat" in name.lower():
                # Swap to a lighter hinge variation already covered by barbell.
                continue
            if focus == "pull_and_legs" and any(tok in name.lower() for tok in ("bench", "press")):
                continue
            if focus == "push" and any(
                tok in name.lower() for tok in ("row", "pull", "squat", "lunge", "deadlift")
            ):
                continue
            blocks.append(ExerciseBlock(name=name, equipment=label, sets=3, reps=reps, notes=notes))
            if label not in used:
                used.append(label)
            if len(blocks) >= max_blocks:
                break
        if len(blocks) >= max_blocks:
            break

    if not blocks:
        # Fallback: first movement per piece of equipment.
        for label in equipment:
            moves = _MOVEMENTS.get(label)
            if not moves:
                continue
            name, reps, notes = moves[0]
            blocks.append(ExerciseBlock(name=name, equipment=label, sets=3, reps=reps, notes=notes))
            used.append(label)
            if len(blocks) >= max_blocks:
                break

    title = f"Gym session — {focus.replace('_', ' ')}"
    return GymSessionPlan(
        title=title,
        focus=focus,
        duration_min=pref.minutes,
        blocks=blocks,
        equipment_used=used,
        history_notes=history_notes,
    )
