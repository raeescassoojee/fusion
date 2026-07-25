from __future__ import annotations

import os
from typing import Any


def aws_status() -> dict[str, Any]:
    region = os.getenv("AWS_REGION", "af-south-1")
    bucket = os.getenv("SENTINEL_EVIDENCE_BUCKET", "")
    tables = [
        os.getenv("SENTINEL_TABLE_SIGNATURES", "sentinel-signatures"),
        os.getenv("SENTINEL_TABLE_PATTERNS", "sentinel-patterns"),
        os.getenv("SENTINEL_TABLE_REVIEWS", "sentinel-reviews"),
    ]
    result: dict[str, Any] = {
        "region": region,
        "credentials": False,
        "bucket": {"name": bucket or None, "reachable": False},
        "tables": {},
        "ready": False,
    }
    try:
        import boto3
        session = boto3.Session(region_name=region)
        identity = session.client("sts").get_caller_identity()
        result["credentials"] = True
        result["account"] = identity.get("Account")
        ddb = session.client("dynamodb")
        for table in tables:
            try:
                desc = ddb.describe_table(TableName=table)["Table"]
                result["tables"][table] = desc.get("TableStatus")
            except Exception as exc:
                result["tables"][table] = f"UNAVAILABLE: {exc.__class__.__name__}"
        if bucket:
            try:
                session.client("s3").head_bucket(Bucket=bucket)
                result["bucket"]["reachable"] = True
            except Exception as exc:
                result["bucket"]["error"] = exc.__class__.__name__
        result["ready"] = (
            all(value == "ACTIVE" for value in result["tables"].values())
            and (not bucket or result["bucket"]["reachable"])
        )
    except Exception as exc:
        result["error"] = str(exc)
    return result
