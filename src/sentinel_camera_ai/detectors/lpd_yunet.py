"""OpenCV DNN adapter for the OpenCV Zoo LPD-YuNet model.

Post-processing follows the Apache-2.0-licensed OpenCV Zoo reference:
https://github.com/opencv/opencv_zoo/tree/main/models/license_plate_detection_yunet

The upstream model is trained on Chinese plates, so this adapter is used as one
detector candidate and is not assumed to be geographically sufficient by itself.
"""

from __future__ import annotations

from itertools import product

import cv2
import numpy as np


class LPDYuNet:
    def __init__(
        self,
        model_path: str,
        input_size: tuple[int, int] = (320, 240),
        confidence_threshold: float = 0.70,
        nms_threshold: float = 0.30,
        top_k: int = 5000,
        keep_top_k: int = 100,
    ):
        self.input_size = np.array(input_size)
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.top_k = top_k
        self.keep_top_k = keep_top_k
        self.output_names = ["loc", "conf", "iou"]
        self.min_sizes = [[10, 16, 24], [32, 48], [64, 96], [128, 192, 256]]
        self.steps = [8, 16, 32, 64]
        self.variance = [0.1, 0.2]
        self.model = cv2.dnn.readNet(model_path)
        self.model.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.model.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self._generate_priors()

    def set_input_size(self, input_size: tuple[int, int]) -> None:
        self.input_size = np.array(input_size)
        self._generate_priors()

    def infer(self, image: np.ndarray) -> np.ndarray:
        height, width = image.shape[:2]
        if (width, height) != tuple(self.input_size):
            self.set_input_size((width, height))
        self.model.setInput(cv2.dnn.blobFromImage(image))
        outputs = self.model.forward(self.output_names)
        detections = self._decode(outputs)
        indices = cv2.dnn.NMSBoxes(
            bboxes=detections[:, 0:4].tolist(),
            scores=detections[:, -1].tolist(),
            score_threshold=self.confidence_threshold,
            nms_threshold=self.nms_threshold,
            top_k=self.top_k,
        )
        if len(indices) == 0:
            return np.empty((0, 9), dtype=np.float32)
        return detections[np.asarray(indices).reshape(-1)][: self.keep_top_k]

    def _generate_priors(self) -> None:
        width, height = self.input_size
        second = [int(int((height + 1) / 2) / 2), int(int((width + 1) / 2) / 2)]
        third = [int(second[0] / 2), int(second[1] / 2)]
        fourth = [int(third[0] / 2), int(third[1] / 2)]
        fifth = [int(fourth[0] / 2), int(fourth[1] / 2)]
        sixth = [int(fifth[0] / 2), int(fifth[1] / 2)]
        feature_maps = [third, fourth, fifth, sixth]
        priors = []
        for level, feature_map in enumerate(feature_maps):
            for row, column in product(
                range(feature_map[0]), range(feature_map[1])
            ):
                for minimum_size in self.min_sizes[level]:
                    priors.append(
                        [
                            (column + 0.5) * self.steps[level] / width,
                            (row + 0.5) * self.steps[level] / height,
                            minimum_size / width,
                            minimum_size / height,
                        ]
                    )
        self.priors = np.asarray(priors, dtype=np.float32)

    def _decode(self, outputs: list[np.ndarray]) -> np.ndarray:
        locations, confidences, ious = outputs
        class_scores = confidences[:, 1]
        iou_scores = np.clip(ious[:, 0], 0.0, 1.0)
        scores = np.sqrt(class_scores * iou_scores)[:, np.newaxis]
        scale = self.input_size
        corners = np.hstack(
            (
                (
                    self.priors[:, 0:2]
                    + locations[:, 4:6] * self.variance[0] * self.priors[:, 2:4]
                )
                * scale,
                (
                    self.priors[:, 0:2]
                    + locations[:, 6:8] * self.variance[0] * self.priors[:, 2:4]
                )
                * scale,
                (
                    self.priors[:, 0:2]
                    + locations[:, 10:12] * self.variance[0] * self.priors[:, 2:4]
                )
                * scale,
                (
                    self.priors[:, 0:2]
                    + locations[:, 12:14] * self.variance[0] * self.priors[:, 2:4]
                )
                * scale,
            )
        )
        return np.hstack((corners, scores))

