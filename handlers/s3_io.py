# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""JSON read/write helpers over S3 for the pipeline handlers.

All handlers move plain, JSON-serializable objects through a single S3 bucket
(see ``docs/infra-contracts.md``). This module isolates the boto3 S3 access so
each handler stays a thin adapter and so unit tests can inject a fake client.

Injection seam
--------------
Both helpers accept an optional ``client=`` argument. In deployment it defaults
to a lazily-created ``boto3.client("s3")`` -- the client is built on first use,
never at import time, so importing this module has no AWS side effects and unit
tests can pass a fake S3 client that stays entirely offline.
"""
from __future__ import annotations

import json
from typing import Any, Optional


def _default_client() -> Any:
    """Lazily construct the default boto3 S3 client.

    Imported and constructed here (not at module import) so importing this
    module has no AWS side effects and offline unit tests never need boto3
    configured.

    Returns:
        A ``boto3.client("s3")`` instance.
    """
    import boto3

    return boto3.client("s3")


def read_json(bucket: str, key: str, client: Optional[Any] = None) -> Any:
    """Read and deserialize a JSON object from S3.

    Args:
        bucket: the S3 bucket name.
        key: the object key (e.g. ``"raw/reviews.json"``).
        client: optional S3 client to use. When ``None`` (the default) a boto3
            S3 client is constructed lazily; tests pass a fake client here.

    Returns:
        The deserialized Python object (typically a ``list`` or ``dict``).

    Raises:
        json.JSONDecodeError: if the object body is not valid JSON.
        botocore.exceptions.ClientError: if the object cannot be fetched.
    """
    s3 = client if client is not None else _default_client()
    response = s3.get_object(Bucket=bucket, Key=key)
    body = response["Body"].read()
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    return json.loads(body)


def write_json(
    bucket: str, key: str, obj: Any, client: Optional[Any] = None
) -> None:
    """Serialize ``obj`` to JSON and write it to S3.

    The object is stored UTF-8 encoded with ``Content-Type: application/json``.

    Args:
        bucket: the S3 bucket name.
        key: the destination object key (e.g. ``"staged/ingested.json"``).
        obj: any JSON-serializable Python object.
        client: optional S3 client to use. When ``None`` (the default) a boto3
            S3 client is constructed lazily; tests pass a fake client here.

    Raises:
        botocore.exceptions.ClientError: if the object cannot be written.
    """
    s3 = client if client is not None else _default_client()
    payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=payload,
        ContentType="application/json",
    )
