from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def _draw_scene(
    frame_index: int,
    total_frames: int,
    camera_number: int,
    plate_text: str,
) -> np.ndarray:
    width, height = 960, 540
    frame = np.full((height, width, 3), (203, 205, 207), dtype=np.uint8)
    cv2.rectangle(frame, (0, 245), (width, height), (75, 78, 82), -1)
    for x in range(-80, width, 180):
        offset = int((frame_index * 5) % 180)
        cv2.rectangle(frame, (x + offset, 380), (x + 90 + offset, 392), (230, 230, 230), -1)

    progress = frame_index / max(total_frames - 1, 1)
    if camera_number == 1:
        car_x = int(-180 + progress * (width + 260))
    else:
        car_x = int(-120 + progress * (width + 220))
    car_y = 292 if camera_number == 1 else 305
    car_w, car_h = 360, 145
    cv2.rectangle(frame, (car_x, car_y), (car_x + car_w, car_y + car_h), (190, 85, 25), -1)
    cv2.rectangle(frame, (car_x + 65, car_y - 58), (car_x + 260, car_y + 5), (190, 85, 25), -1)
    cv2.rectangle(frame, (car_x + 95, car_y - 46), (car_x + 170, car_y - 6), (105, 130, 145), -1)
    cv2.rectangle(frame, (car_x + 180, car_y - 46), (car_x + 245, car_y - 6), (105, 130, 145), -1)
    cv2.circle(frame, (car_x + 80, car_y + car_h), 34, (25, 25, 28), -1)
    cv2.circle(frame, (car_x + 285, car_y + car_h), 34, (25, 25, 28), -1)

    plate_w, plate_h = 210, 58
    plate_x = car_x + car_w - plate_w - 18
    plate_y = car_y + car_h - plate_h - 18
    cv2.rectangle(
        frame,
        (plate_x, plate_y),
        (plate_x + plate_w, plate_y + plate_h),
        (248, 248, 248),
        -1,
    )
    cv2.rectangle(
        frame,
        (plate_x, plate_y),
        (plate_x + plate_w, plate_y + plate_h),
        (25, 25, 25),
        2,
    )
    font_scale = 1.0
    thickness = 2
    while font_scale > 0.45:
        (text_width, text_height), _ = cv2.getTextSize(
            plate_text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness
        )
        if text_width <= plate_w - 14 and text_height <= plate_h - 10:
            break
        font_scale -= 0.05
    text_x = plate_x + max(7, (plate_w - text_width) // 2)
    text_y = plate_y + (plate_h + text_height) // 2 - 2
    cv2.putText(
        frame,
        plate_text,
        (text_x, text_y),
        cv2.FONT_HERSHEY_SIMPLEX,
        font_scale,
        (15, 15, 15),
        thickness,
        cv2.LINE_AA,
    )

    person_x = 845 if camera_number == 1 else 55
    cv2.circle(frame, (person_x + 35, 175), 28, (110, 170, 220), -1)
    cv2.rectangle(frame, (person_x, 202), (person_x + 70, 300), (20, 20, 25), -1)
    cv2.rectangle(frame, (person_x, 300), (person_x + 70, 415), (175, 60, 30), -1)

    cv2.putText(
        frame,
        f"SYNTHETIC DEMO - CAM0{camera_number}",
        (20, 35),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (30, 40, 55),
        2,
        cv2.LINE_AA,
    )
    if camera_number == 2:
        overlay = np.full_like(frame, (22, 17, 10))
        frame = cv2.addWeighted(frame, 0.86, overlay, 0.14, 0)
    return frame


def generate_demo_media(output_dir: str | Path, plate_text: str = "AB12CDGP") -> list[Path]:
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    fps = 15.0
    total_frames = 90
    paths: list[Path] = []
    for camera_number in (1, 2):
        path = destination / f"camera_{camera_number}_clip.mp4"
        writer = cv2.VideoWriter(
            str(path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            fps,
            (960, 540),
        )
        if not writer.isOpened():
            raise RuntimeError("OpenCV could not create the synthetic MP4")
        for frame_index in range(total_frames):
            writer.write(
                _draw_scene(frame_index, total_frames, camera_number, plate_text)
            )
        writer.release()
        paths.append(path)

    reference = _draw_scene(45, total_frames, 1, plate_text)
    cv2.imwrite(str(destination / "synthetic_reference.jpg"), reference)
    return paths
