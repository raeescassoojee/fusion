"""Provision the Sentinel Mesh evidence-pattern tables in AWS.

    python provision_aws.py --region af-south-1
    python provision_aws.py --region af-south-1 --teardown     # after judging

Creates three DynamoDB tables, on-demand billed so an idle demo costs nothing:

  sentinel-signatures   one observation from one camera at one moment
  sentinel-patterns     a cluster of signatures that plausibly recur
  sentinel-reviews      the human decision on each pattern, with its reason

Design notes that matter for the write-up:

* Signatures carry a TTL. DynamoDB deletes them automatically once the retention
  window passes, so "we forgot to delete it" cannot happen. Retention is a decision
  someone makes, not a default.
* Point-in-time recovery is on for patterns and reviews (they are the audit record)
  and off for signatures (they are meant to expire).
* Server-side encryption uses a KMS key when one is supplied, otherwise the
  AWS-owned key. Pass --kms-key-id for anything beyond a demo.
* No table has an index on anything that could identify a person. There is no name
  attribute, no ID number, no face gallery. `saps_reference` points outward to a
  case that SAPS owns.
"""
from __future__ import annotations

import argparse
import sys
import time

TABLES = {
    "sentinel-signatures": {
        "KeySchema": [{"AttributeName": "signature_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [
            {"AttributeName": "signature_id", "AttributeType": "S"},
            {"AttributeName": "bucket", "AttributeType": "S"},
            {"AttributeName": "observed_at", "AttributeType": "S"},
            {"AttributeName": "plate_token", "AttributeType": "S"},
            {"AttributeName": "pattern_id", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [
            {
                # Candidate narrowing: kind#geofence#day. Cheap alternative to a
                # vector store — we filter hard before we ever score.
                "IndexName": "bucket-index",
                "KeySchema": [
                    {"AttributeName": "bucket", "KeyType": "HASH"},
                    {"AttributeName": "observed_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                # Exact retrieval on a salted plate token. The salt lives in
                # SENTINEL_PLATE_SALT; the raw plate is never a key.
                "IndexName": "plate-index",
                "KeySchema": [
                    {"AttributeName": "plate_token", "KeyType": "HASH"},
                    {"AttributeName": "observed_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "pattern-index",
                "KeySchema": [
                    {"AttributeName": "pattern_id", "KeyType": "HASH"},
                    {"AttributeName": "observed_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        "ttl": "ttl",
        "pitr": False,
    },
    "sentinel-patterns": {
        "KeySchema": [{"AttributeName": "pattern_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [
            {"AttributeName": "pattern_id", "AttributeType": "S"},
            {"AttributeName": "status", "AttributeType": "S"},
            {"AttributeName": "last_seen", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "status-index",
                "KeySchema": [
                    {"AttributeName": "status", "KeyType": "HASH"},
                    {"AttributeName": "last_seen", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        "ttl": None,
        "pitr": True,
    },
    "sentinel-reviews": {
        "KeySchema": [{"AttributeName": "review_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [
            {"AttributeName": "review_id", "AttributeType": "S"},
            {"AttributeName": "pattern_id", "AttributeType": "S"},
            {"AttributeName": "created_at", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "pattern-index",
                "KeySchema": [
                    {"AttributeName": "pattern_id", "KeyType": "HASH"},
                    {"AttributeName": "created_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        "ttl": None,
        "pitr": True,
    },
}

TAGS = [
    {"Key": "project", "Value": "sentinel-mesh"},
    {"Key": "env", "Value": "demo"},
    {"Key": "data-classification", "Value": "special-personal-information"},
]


def create(client, name: str, spec: dict, kms_key_id: str | None) -> None:
    params = {
        "TableName": name,
        "KeySchema": spec["KeySchema"],
        "AttributeDefinitions": spec["AttributeDefinitions"],
        "BillingMode": "PAY_PER_REQUEST",
        "Tags": TAGS,
        "SSESpecification": {"Enabled": True},
    }
    if kms_key_id:
        params["SSESpecification"] = {
            "Enabled": True, "SSEType": "KMS", "KMSMasterKeyId": kms_key_id
        }
    if spec.get("GlobalSecondaryIndexes"):
        params["GlobalSecondaryIndexes"] = spec["GlobalSecondaryIndexes"]

    try:
        client.create_table(**params)
        print(f"  creating {name} …", end="", flush=True)
        client.get_waiter("table_exists").wait(TableName=name)
        print(" active")
    except client.exceptions.ResourceInUseException:
        print(f"  {name} already exists — leaving it alone")

    if spec.get("ttl"):
        try:
            client.update_time_to_live(
                TableName=name,
                TimeToLiveSpecification={"Enabled": True, "AttributeName": spec["ttl"]},
            )
            print(f"    TTL enabled on '{spec['ttl']}' — signatures self-delete")
        except Exception as exc:
            print(f"    TTL not set: {exc}")

    if spec.get("pitr"):
        for attempt in range(5):
            try:
                client.update_continuous_backups(
                    TableName=name,
                    PointInTimeRecoverySpecification={"PointInTimeRecoveryEnabled": True},
                )
                print("    point-in-time recovery enabled (audit record)")
                break
            except Exception:
                time.sleep(2)



def create_evidence_bucket(s3, bucket: str, region: str, retention_days: int) -> None:
    """Create or harden the private evidence bucket used by the optional AWS sink."""
    try:
        kwargs = {"Bucket": bucket}
        if region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
        s3.create_bucket(**kwargs)
        print(f"  created private evidence bucket {bucket}")
    except s3.exceptions.BucketAlreadyOwnedByYou:
        print(f"  evidence bucket {bucket} already exists in this account")
    except Exception as exc:
        # A globally unique bucket name may already belong to another account.
        try:
            s3.head_bucket(Bucket=bucket)
            print(f"  evidence bucket {bucket} is reachable")
        except Exception:
            raise RuntimeError(f"could not create or access bucket {bucket}: {exc}") from exc

    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3.put_bucket_encryption(
        Bucket=bucket,
        ServerSideEncryptionConfiguration={
            "Rules": [{
                "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"},
            }]
        },
    )
    s3.put_bucket_versioning(
        Bucket=bucket, VersioningConfiguration={"Status": "Enabled"}
    )
    s3.put_bucket_lifecycle_configuration(
        Bucket=bucket,
        LifecycleConfiguration={
            "Rules": [{
                "ID": "expire-demo-evidence",
                "Status": "Enabled",
                "Filter": {"Prefix": "evidence/"},
                "Expiration": {"Days": retention_days},
                "NoncurrentVersionExpiration": {"NoncurrentDays": retention_days},
            }]
        },
    )
    print(
        f"    public access blocked, encryption/versioning on, "
        f"evidence expires after {retention_days} days"
    )

def teardown(client) -> None:
    for name in TABLES:
        try:
            client.delete_table(TableName=name)
            print(f"  deleted {name}")
        except client.exceptions.ResourceNotFoundException:
            print(f"  {name} not present")


def main() -> int:
    ap = argparse.ArgumentParser(description="Provision Sentinel Mesh pattern tables")
    ap.add_argument("--region", default="af-south-1",
                    help="prefer af-south-1 (Cape Town) for data residency")
    ap.add_argument("--kms-key-id", default=None,
                    help="customer-managed KMS key; omit only for a throwaway demo")
    ap.add_argument("--bucket", default=None, help="globally unique private S3 evidence bucket")
    ap.add_argument(
        "--evidence-retention-days", type=int, default=30,
        help="expire demo evidence objects after this many days",
    )
    ap.add_argument("--teardown", action="store_true", help="delete the DynamoDB tables")
    args = ap.parse_args()

    try:
        import boto3
    except ImportError:
        print("boto3 is not installed:  pip install boto3", file=sys.stderr)
        return 1

    client = boto3.client("dynamodb", region_name=args.region)
    s3 = boto3.client("s3", region_name=args.region)
    try:
        boto3.client("sts", region_name=args.region).get_caller_identity()
    except Exception as exc:
        print(f"AWS credentials not usable: {exc}", file=sys.stderr)
        print("Run 'aws configure sso' or set AWS_PROFILE first.", file=sys.stderr)
        return 1

    if args.teardown:
        print(f"Deleting Sentinel tables in {args.region}")
        teardown(client)
        return 0

    print(f"Provisioning Sentinel Mesh tables in {args.region}")
    if not args.kms_key_id:
        print("  note: using the AWS-owned key. Supply --kms-key-id for real data.")
    for name, spec in TABLES.items():
        create(client, name, spec, args.kms_key_id)
    if args.bucket:
        create_evidence_bucket(
            s3, args.bucket, args.region, max(1, args.evidence_retention_days)
        )

    print("\nDone. Point the app at these with:")
    print(f"  set AWS_REGION={args.region}")
    if args.bucket:
        print(f"  set SENTINEL_EVIDENCE_BUCKET={args.bucket}")
    print("  set SENTINEL_PLATE_SALT=<a real secret, not the default>")
    print("\nThe app falls back to local SQLite automatically if AWS is unreachable,")
    print("so the demo still runs if the venue network blocks you.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
