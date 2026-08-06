from sentinel_ops.camera_bridge import camera_ai_to_operations


def test_camera_ai_event_adapter_preserves_key_signals():
    payload = {
        "schema_version": "1.0",
        "event_id": "EVT-1",
        "camera_id": "CAM-1",
        "timestamp": "2026-07-24T21:07:00+02:00",
        "mode": "HEIGHTENED",
        "location": {"latitude": -25.797, "longitude": 28.301},
        "media_url": "evidence/EVT-1/best_frame.jpg",
        "plate": {
            "text": "AB12CDGP",
            "detection_confidence": 0.82,
            "ocr_confidence": 0.91
        },
        "face": {
            "embedding_ref": "evidence/EVT-1/faces/face_0_embedding.npy",
            "detection_confidence": 0.78
        },
        "vehicle": {"colour": "Blue", "type": "Car", "make_model": None},
        "appearance": {
            "upper_colour": "Black",
            "lower_colour": "Blue",
            "cap": False,
            "backpack": False
        },
        "camera_trust_score": 78
    }

    event = camera_ai_to_operations(payload)
    assert event.event_id == "EVT-1"
    assert event.plate.text == "AB12CDGP"
    assert event.plate.confidence == 0.91
    assert event.vehicle.colour == "Blue"
    assert event.camera_trust_score == 78
    assert event.source == "sentinel-camera-ai"
