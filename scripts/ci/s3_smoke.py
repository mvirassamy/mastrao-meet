"""Exercise the S3 operations required by the backend CI job."""

from __future__ import annotations

import os
from typing import Any

BUCKET = "meet-media-storage"
KEY = "ci/s3-smoke.txt"
PAYLOAD = b"mastrao-s3-smoke"


def run_smoke(client: Any) -> None:
    """Run the required object round trip against an S3-compatible client."""
    client.create_bucket(Bucket=BUCKET)
    try:
        client.put_object(Bucket=BUCKET, Key=KEY, Body=PAYLOAD)
        response = client.get_object(Bucket=BUCKET, Key=KEY)
        body = response["Body"].read()
        if body != PAYLOAD:
            raise RuntimeError("S3 smoke readback did not match the uploaded bytes")
    finally:
        client.delete_object(Bucket=BUCKET, Key=KEY)

    remaining = client.list_objects_v2(Bucket=BUCKET, Prefix=KEY)
    if remaining.get("KeyCount") != 0:
        raise RuntimeError("S3 smoke object still exists after deletion")


def main() -> None:
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "s3",
        endpoint_url=os.environ["AWS_S3_ENDPOINT_URL"],
        aws_access_key_id=os.environ["AWS_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_S3_SECRET_ACCESS_KEY"],
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )

    run_smoke(client)


if __name__ == "__main__":
    main()
