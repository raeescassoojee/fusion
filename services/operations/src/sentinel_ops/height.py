"""Calibrated human-height estimation for fixed cameras.

Two calibration modes, both single-view:

1. INTRINSIC  — you know the physical mount: camera height above ground, downward
   tilt, and horizontal field of view. Height is computed by intersecting the foot
   ray with the ground plane to recover depth, then measuring the head ray against
   that depth.

2. REFERENCE  — you don't know the mount. An operator marks one object of known
   height standing on the ground (a door, a team member) plus the horizon row.
   Height then follows from the cross-ratio, which is invariant under perspective.

Both return a *band*, never a point estimate: detector boxes are noisy and a single
pixel of error at the top of frame is worth centimetres. The band widens honestly
as geometry gets worse.

Full-view estimates are preferred.  When a tracked subject is vertically clipped,
the caller may provide inferred head/foot rows from the face/body track.  Those
observations require five frames and always return a visibly wider approximate
band.  They remain corroborating metadata and are never an identity input.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import median
from typing import Iterable, Literal


# Detector box jitter, in pixels, at the head and feet. Feet are usually worse:
# shoes/shadow boundaries are ambiguous and occlusion is common.
HEAD_PIXEL_SIGMA = 3.0
FOOT_PIXEL_SIGMA = 5.0

# Below this many pixels of subject height the geometry is too coarse to trust.
MIN_SUBJECT_PIXELS = 60

# Calibration confidence below which height is disabled entirely (Camera Trust).
MIN_CALIBRATION_SCORE = 70

# Partial-view estimates must be honest about their extra uncertainty.  A
# 20-centimetre-wide band communicates roughly +/-10 cm without pretending that
# an inferred head or foot row is as precise as a visible one.
PARTIAL_MIN_HALF_BAND_M = 0.10
PARTIAL_HEAD_PIXEL_SIGMA = 8.0
PARTIAL_FOOT_PIXEL_SIGMA = 12.0


class HeightUnavailable(Exception):
    """Raised when a height estimate would not be defensible."""


@dataclass
class CameraCalibration:
    """Per-camera geometry. Persisted once per installed device."""

    camera_id: str
    image_width: int
    image_height: int
    mode: Literal["INTRINSIC", "REFERENCE"] = "INTRINSIC"

    # --- INTRINSIC mode ---
    mount_height_m: float | None = None      # lens height above the ground plane
    tilt_deg: float | None = None            # downward tilt of the optical axis
    horizontal_fov_deg: float | None = None  # full horizontal field of view

    # --- REFERENCE mode ---
    horizon_y: float | None = None           # image row of the horizon line
    ref_height_m: float | None = None        # true height of the reference object
    ref_foot_y: float | None = None          # image row of its base
    ref_head_y: float | None = None          # image row of its top

    # Health: drops as the camera drifts off its calibrated pose.
    calibration_score: float = 100.0
    principal_point_y: float | None = None   # defaults to image centre

    def focal_px(self) -> float:
        if not self.horizontal_fov_deg:
            raise HeightUnavailable("horizontal_fov_deg required for INTRINSIC mode")
        half = math.radians(self.horizontal_fov_deg) / 2.0
        return (self.image_width / 2.0) / math.tan(half)

    def cy(self) -> float:
        return self.image_height / 2.0 if self.principal_point_y is None else self.principal_point_y

    def validate(self) -> None:
        if self.calibration_score < MIN_CALIBRATION_SCORE:
            raise HeightUnavailable(
                f"calibration score {self.calibration_score:.0f} below {MIN_CALIBRATION_SCORE}"
                " — height estimation disabled until the camera is re-calibrated"
            )
        if self.mode == "INTRINSIC":
            missing = [
                n for n, v in (
                    ("mount_height_m", self.mount_height_m),
                    ("tilt_deg", self.tilt_deg),
                    ("horizontal_fov_deg", self.horizontal_fov_deg),
                ) if v is None
            ]
            if missing:
                raise HeightUnavailable(f"INTRINSIC calibration missing: {', '.join(missing)}")
        else:
            missing = [
                n for n, v in (
                    ("horizon_y", self.horizon_y),
                    ("ref_height_m", self.ref_height_m),
                    ("ref_foot_y", self.ref_foot_y),
                    ("ref_head_y", self.ref_head_y),
                ) if v is None
            ]
            if missing:
                raise HeightUnavailable(f"REFERENCE calibration missing: {', '.join(missing)}")


@dataclass
class HeightEstimate:
    low_m: float
    high_m: float
    point_m: float
    frames_used: int
    method: str
    quality: float                       # 0-1, how much the geometry deserves trust
    notes: list[str] = field(default_factory=list)

    @property
    def band(self) -> str:
        return f"{self.low_m:.2f}-{self.high_m:.2f} m"

    def to_dict(self) -> dict:
        return {
            "height_low_m": round(self.low_m, 2),
            "height_high_m": round(self.high_m, 2),
            "height_point_m": round(self.point_m, 2),
            "height_band": self.band,
            "frames_used": self.frames_used,
            "method": self.method,
            "quality": round(self.quality, 2),
            "notes": self.notes,
        }


# --------------------------------------------------------------------------- core
def _height_intrinsic(cal: CameraCalibration, foot_y: float, head_y: float) -> float:
    """Ground-plane intersection. Returns metres above the ground plane."""
    f = cal.focal_px()
    cy = cal.cy()
    tilt = math.radians(cal.tilt_deg or 0.0)

    # Depression angle below the horizontal for a given image row.
    def depression(y: float) -> float:
        return tilt + math.atan((y - cy) / f)

    dep_foot = depression(foot_y)
    if dep_foot <= 1e-6:
        raise HeightUnavailable("feet are at or above the horizon — no ground intersection")

    # Horizontal distance from the camera to where the feet meet the ground.
    depth = cal.mount_height_m / math.tan(dep_foot)

    # Height of the head ray at that same depth, measured up from the ground.
    dep_head = depression(head_y)
    height = cal.mount_height_m - depth * math.tan(dep_head)
    return height


def _height_reference(cal: CameraCalibration, foot_y: float, head_y: float) -> float:
    """Cross-ratio against a reference object of known height on the same ground plane."""
    hz = cal.horizon_y
    subj_den = foot_y - hz
    ref_den = cal.ref_foot_y - hz
    if abs(subj_den) < 1e-6 or abs(ref_den) < 1e-6:
        raise HeightUnavailable("subject or reference stands on the horizon — degenerate geometry")

    subj_ratio = (foot_y - head_y) / subj_den
    ref_ratio = (cal.ref_foot_y - cal.ref_head_y) / ref_den
    if abs(ref_ratio) < 1e-9:
        raise HeightUnavailable("reference object has no measurable image height")
    return cal.ref_height_m * (subj_ratio / ref_ratio)


def estimate_single_frame(
    cal: CameraCalibration,
    foot_y: float,
    head_y: float,
    frame_clipped: bool = False,
    allow_partial: bool = False,
) -> tuple[float, float, float]:
    """Return (point, low, high) in metres for one frame.

    The band is produced by re-running the geometry at the corners of the pixel
    uncertainty, which is more honest than a fixed percentage: the same pixel error
    means very different things near and far from the camera.
    """
    cal.validate()
    if frame_clipped and not allow_partial:
        raise HeightUnavailable("subject clipped by the frame edge — head or feet not fully visible")
    if foot_y <= head_y:
        raise HeightUnavailable("foot row is above head row — box is inverted or invalid")
    if (foot_y - head_y) < MIN_SUBJECT_PIXELS:
        raise HeightUnavailable(
            f"subject only {foot_y - head_y:.0f}px tall, below the {MIN_SUBJECT_PIXELS}px floor"
        )

    solver = _height_intrinsic if cal.mode == "INTRINSIC" else _height_reference
    point = solver(cal, foot_y, head_y)

    # Worst cases: head too high + feet too low (over-estimate), and the reverse.
    # Inferred rows receive much larger pixel uncertainty than visible boundaries.
    head_sigma = PARTIAL_HEAD_PIXEL_SIGMA if allow_partial else HEAD_PIXEL_SIGMA
    foot_sigma = PARTIAL_FOOT_PIXEL_SIGMA if allow_partial else FOOT_PIXEL_SIGMA
    tall = solver(cal, foot_y + foot_sigma, head_y - head_sigma)
    short = solver(cal, foot_y - foot_sigma, head_y + head_sigma)
    low, high = sorted((short, tall))

    if not (0.6 < point < 2.6):
        raise HeightUnavailable(f"implausible result {point:.2f} m — check calibration")
    return point, low, high


def estimate_height(
    cal: CameraCalibration,
    observations: Iterable[dict],
) -> HeightEstimate:
    """Combine per-frame observations into one banded estimate.

    Each observation: {"foot_y": float, "head_y": float, "clipped": bool (optional)}

    The median across frames is used rather than the mean, so one bad detection
    cannot drag the answer. The band is the median of the per-frame bands, widened
    if the frames disagree with each other.
    """
    cal.validate()
    accepted: list[tuple[float, float, float, bool]] = []
    rejected = 0
    reasons: set[str] = set()

    for obs in observations:
        try:
            partial = bool(obs.get("partial", False))
            p, lo, hi = estimate_single_frame(
                cal,
                foot_y=float(obs["foot_y"]),
                head_y=float(obs["head_y"]),
                frame_clipped=bool(obs.get("clipped", False)),
                allow_partial=partial,
            )
        except HeightUnavailable as exc:
            rejected += 1
            reasons.add(str(exc))
            continue
        accepted.append((p, lo, hi, partial))

    full = [item for item in accepted if not item[3]]
    if len(full) >= 3:
        chosen = full
        used_partial = False
    elif len(accepted) >= 5:
        chosen = accepted
        used_partial = any(item[3] for item in chosen)
    else:
        needed = 3 if full else 5
        raise HeightUnavailable(
            f"only {len(accepted)} usable frame(s), need {needed} — "
            + ("; ".join(sorted(reasons)) if reasons else "subject not tracked long enough")
        )

    points = [item[0] for item in chosen]
    lows = [item[1] for item in chosen]
    highs = [item[2] for item in chosen]

    point = median(points)
    low = median(lows)
    high = median(highs)

    # Frame-to-frame disagreement is real uncertainty: fold it in.
    spread = max(points) - min(points)
    low = min(low, point - spread / 2)
    high = max(high, point + spread / 2)

    if used_partial:
        low = min(low, point - PARTIAL_MIN_HALF_BAND_M)
        high = max(high, point + PARTIAL_MIN_HALF_BAND_M)

    width = high - low
    quality = max(0.0, min(1.0, 1.0 - width / 0.40))
    if cal.calibration_score < 90:
        quality *= cal.calibration_score / 100.0
    if used_partial:
        quality = min(quality, 0.72)

    notes: list[str] = []
    if rejected:
        notes.append(f"{rejected} frame(s) rejected: {'; '.join(sorted(reasons))}")
    if spread > 0.15:
        notes.append(f"frames disagree by {spread * 100:.0f} cm — band widened accordingly")
    if width > 0.30:
        notes.append("band exceeds 30 cm — treat as corroborating detail only, not identifying")
    if used_partial:
        notes.append(
            "approximate partial-view result: one or more head/foot rows were inferred; "
            "not used for identity or automatic escalation"
        )

    return HeightEstimate(
        low_m=low,
        high_m=high,
        point_m=point,
        frames_used=len(chosen),
        method=(
            f"{cal.mode.lower()}-partial-view-approximation"
            if used_partial
            else f"{cal.mode.lower()}-full-view"
        ),
        quality=quality,
        notes=notes,
    )


def observations_from_person_boxes(
    boxes: Iterable[dict],
    image_height: int,
    image_width: int,
    edge_margin: int = 4,
) -> list[dict]:
    """Convert detector person boxes into height observations.

    Explicit ``head_y`` / ``foot_y`` values may be supplied by a tracked face/body
    association.  If a vertical edge is clipped these are treated as partial-view
    observations and receive the wider uncertainty policy in ``estimate_height``.
    Horizontal clipping alone does not invalidate vertical camera geometry.
    """
    out = []
    for b in boxes:
        y, h = float(b["y"]), float(b["height"])
        x, w = float(b.get("x", 0)), float(b.get("width", 0))
        top_clipped = y <= edge_margin
        bottom_clipped = (y + h) >= image_height - edge_margin
        vertical_clipped = top_clipped or bottom_clipped
        explicit_partial = bool(b.get("partial", False))
        head_y = float(b.get("head_y", y))
        foot_y = float(b.get("foot_y", y + h))
        out.append(
            {
                "head_y": head_y,
                "foot_y": foot_y,
                "clipped": vertical_clipped,
                "partial": explicit_partial or vertical_clipped,
                "top_clipped": top_clipped,
                "bottom_clipped": bottom_clipped,
                "side_clipped": x <= edge_margin or (x + w) >= image_width - edge_margin,
            }
        )
    return out
