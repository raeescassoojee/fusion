# Third-party model policy

The complete demonstration bundle includes three official OpenCV Zoo model
files, their upstream licence notices under `models/licenses`, and recorded
SHA-256 checksums under `models/checksums.sha256`.

- OpenCV YuNet face detection: MIT License.
- OpenCV SFace embeddings: Apache License 2.0.
- OpenCV LPD-YuNet plate detection: Apache License 2.0. It is
  trained on Chinese licence plates and is not South African validation.
- Ultralytics YOLO: review the current Ultralytics software/model licence before
  commercial or production deployment.
- Plate detection weights: verify the dataset provenance, permitted use,
  geographic suitability and licence before placing weights at
  `models/license_plate_detector.pt`.
- PaddleOCR and Tesseract: retain their upstream notices and verify the exact
  versions included in a deployed image.

The controlled synthetic demo and contour/Tesseract fallback are intended for
integration testing. They are not evidence of production CCTV accuracy.
