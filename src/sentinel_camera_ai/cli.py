from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path

from .aws_sink import AwsSink
from .config import AppConfig
from .doctor import run_doctor
from .matching import compare_events
from .pipeline import CameraAIPipeline
from .schemas import CameraEvent
from .synthetic import generate_demo_media


def _config(path: str) -> AppConfig:
    return AppConfig.load(path)


def _datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include an offset, e.g. +02:00")
    return parsed


def process_command(args: argparse.Namespace) -> int:
    config = _config(args.config)
    if args.output_dir:
        config.output_dir = args.output_dir
    pipeline = CameraAIPipeline(config)
    results = pipeline.process_media(
        input_path=args.input,
        camera_id=args.camera_id,
        latitude=args.latitude,
        longitude=args.longitude,
        mode=args.mode,
        start_timestamp=_datetime(args.timestamp),
    )
    response = []
    for event, event_path in results:
        item = {
            "event_id": event.event_id,
            "event_json": str(event_path),
            "plate": event.plate.text,
            "vehicle": event.vehicle.model_dump(),
            "trust": event.camera_trust_score,
        }
        if args.publish_aws:
            config.aws.enabled = True
            published = AwsSink(config.aws).publish(
                event, config.resolve(config.output_dir)
            )
            item["aws"] = {
                "event_object": published.event_object,
                "api_status": published.api_status,
            }
        response.append(item)
    print(json.dumps({"events": response, "count": len(response)}, indent=2, default=str))
    return 0 if results else 2


def compare_command(args: argparse.Namespace) -> int:
    path_a = Path(args.event_a).resolve()
    path_b = Path(args.event_b).resolve()
    event_a = CameraEvent.model_validate_json(path_a.read_text(encoding="utf-8"))
    event_b = CameraEvent.model_validate_json(path_b.read_text(encoding="utf-8"))
    result = compare_events(
        event_a,
        event_b,
        base_dir_a=path_a.parent.parent,
        base_dir_b=path_b.parent.parent,
    )
    output = result.model_dump_json(indent=2)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    print(output)
    return 0


def validate_command(args: argparse.Namespace) -> int:
    path = Path(args.event).resolve()
    event = CameraEvent.model_validate_json(path.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "valid": True,
                "event_id": event.event_id,
                "schema_version": event.schema_version,
            },
            indent=2,
        )
    )
    return 0


def doctor_command(args: argparse.Namespace) -> int:
    result = run_doctor(_config(args.config))
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["required_ok"] else 1


def synthesize_command(args: argparse.Namespace) -> int:
    paths = generate_demo_media(args.output, plate_text=args.plate)
    print(json.dumps({"created": [str(path) for path in paths]}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sentinel-camera-ai",
        description="Discovery Sentinel Mesh local-first camera intelligence pipeline",
    )
    parser.add_argument(
        "--config",
        default="config/default.yaml",
        help="YAML configuration file",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    process = subparsers.add_parser("process", help="Process an image or recorded video")
    process.add_argument("--input", required=True)
    process.add_argument("--camera-id")
    process.add_argument("--latitude", type=float)
    process.add_argument("--longitude", type=float)
    process.add_argument("--mode", default="HEIGHTENED", choices=["NORMAL", "HEIGHTENED"])
    process.add_argument("--timestamp", help="ISO 8601 start time including timezone")
    process.add_argument("--output-dir")
    process.add_argument("--publish-aws", action="store_true")
    process.set_defaults(func=process_command)

    compare = subparsers.add_parser("compare", help="Compare two event JSON files")
    compare.add_argument("--event-a", required=True)
    compare.add_argument("--event-b", required=True)
    compare.add_argument("--output")
    compare.set_defaults(func=compare_command)

    validate = subparsers.add_parser("validate", help="Validate one event JSON file")
    validate.add_argument("--event", required=True)
    validate.set_defaults(func=validate_command)

    doctor = subparsers.add_parser("doctor", help="Check dependencies and model files")
    doctor.set_defaults(func=doctor_command)

    synthesize = subparsers.add_parser(
        "synthesize-demo", help="Create two safe synthetic camera clips"
    )
    synthesize.add_argument("--output", default="media")
    synthesize.add_argument("--plate", default="AB12CDGP")
    synthesize.set_defaults(func=synthesize_command)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s: %(message)s",
    )
    raise SystemExit(args.func(args))

