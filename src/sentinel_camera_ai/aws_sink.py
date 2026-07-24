from __future__ import annotations

import json
import mimetypes
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import AwsConfig
from .schemas import CameraEvent


@dataclass(slots=True)
class AwsPublishResult:
    evidence_objects: dict[str, str]
    event_object: str | None
    api_status: int | None


class AwsSink:
    """Optional adapter. It never loads AWS credentials from source code."""

    def __init__(self, config: AwsConfig):
        if not config.enabled:
            raise ValueError("AWS publishing is disabled in the configuration")
        if not config.bucket:
            raise ValueError("aws.bucket must be configured")
        self.config = config
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("install the project with the [aws] extra") from exc
        session_kwargs = {"region_name": config.region}
        if config.profile:
            session_kwargs["profile_name"] = config.profile
        self.session = boto3.Session(**session_kwargs)
        self.s3 = self.session.client("s3")

    def publish(self, event: CameraEvent, output_root: str | Path) -> AwsPublishResult:
        root = Path(output_root).resolve()
        evidence_objects: dict[str, str] = {}
        local_paths = [
            event.media_url,
            event.metadata.get("annotated_media_url"),
            event.plate.crop_url,
            *event.face.crop_paths,
        ]
        for relative in filter(None, local_paths):
            path = root / str(relative)
            if not path.exists():
                continue
            suffix = Path(relative).as_posix()
            key = f"{self.config.evidence_prefix.rstrip('/')}/{event.event_id}/{Path(suffix).name}"
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.s3.upload_file(
                str(path),
                self.config.bucket,
                key,
                ExtraArgs={"ContentType": content_type},
            )
            evidence_objects[str(relative)] = f"s3://{self.config.bucket}/{key}"

        payload = event.model_copy(deep=True)
        payload.media_url = evidence_objects.get(event.media_url, event.media_url)
        if payload.plate.crop_url:
            payload.plate.crop_url = evidence_objects.get(
                payload.plate.crop_url, payload.plate.crop_url
            )
        payload.face.crop_paths = [
            evidence_objects.get(path, path) for path in payload.face.crop_paths
        ]

        event_key = (
            f"{self.config.events_prefix.rstrip('/')}/{event.timestamp:%Y/%m/%d}/"
            f"{event.event_id}.json"
        )
        body = payload.model_dump_json(indent=2).encode("utf-8")
        self.s3.put_object(
            Bucket=self.config.bucket,
            Key=event_key,
            Body=body,
            ContentType="application/json",
        )
        event_object = f"s3://{self.config.bucket}/{event_key}"

        api_status = None
        if self.config.ingestion_url:
            headers = {"Content-Type": "application/json"}
            token = os.getenv(self.config.api_token_env)
            if token:
                headers["Authorization"] = f"Bearer {token}"
            request = urllib.request.Request(
                self.config.ingestion_url,
                data=body,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                api_status = int(response.status)
        return AwsPublishResult(
            evidence_objects=evidence_objects,
            event_object=event_object,
            api_status=api_status,
        )

