# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Lambda handler for the read API (API Gateway REST proxy integration).

Wraps :func:`review_pipeline.api.get_product_reviews` and
:func:`review_pipeline.api.get_product_summary` behind an API Gateway proxy
integration. It parses the ``productId`` path parameter and which resource was
requested (reviews vs. summary) from the event, builds an S3-backed
:class:`review_pipeline.api.ReviewStore`, and returns an API-Gateway-shaped
response dict.

Read model
----------
The store reads the two serving artifacts (see ``docs/infra-contracts.md``):

* ``serving/scored.json`` -- the kept, scored reviews.
* ``staged/summaries.json`` -- the per-product summaries.

It indexes them by ``product_id`` in exactly the way
:class:`review_pipeline.api.InMemoryStore` does, so the framework-light API core
is reused unchanged.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from review_pipeline import api
from review_pipeline.config import load_config
from review_pipeline.logging_config import configure_logging, get_logger

from handlers import keys, s3_io

logger = get_logger(__name__)

# API Gateway path-parameter name (route: /products/{productId}/...).
PATH_PARAM_PRODUCT_ID = "productId"

# Resource discriminators used to route to reviews vs. summary.
RESOURCE_REVIEWS = "reviews"
RESOURCE_SUMMARY = "summary"

_JSON_HEADERS = {"Content-Type": "application/json"}


class S3ReviewStore:
    """S3-backed :class:`review_pipeline.api.ReviewStore`.

    Loads the two serving artifacts from S3 once and indexes them by
    ``product_id`` for the lifetime of the store, satisfying the same protocol
    as :class:`review_pipeline.api.InMemoryStore` so the API core is reused.
    """

    def __init__(self, bucket: str, client: Optional[Any] = None) -> None:
        """Load and index the scored reviews and product summaries from S3.

        Args:
            bucket: the data bucket name.
            client: optional S3 client (injected by tests); defaults to the
                lazily constructed boto3 client in :mod:`handlers.s3_io`.
        """
        scored_reviews: List[Dict[str, Any]] = s3_io.read_json(
            bucket, keys.SERVING_SCORED_KEY, client=client
        )
        product_summaries: List[Dict[str, Any]] = s3_io.read_json(
            bucket, keys.STAGED_SUMMARIES_KEY, client=client
        )
        # Reuse the in-memory indexing so this store behaves identically to the
        # one the API tests exercise.
        self._delegate = api.InMemoryStore(
            scored_reviews=scored_reviews,
            product_summaries=product_summaries,
        )

    def get_reviews(self, product_id: str) -> List[Dict[str, Any]]:
        """Return all scored reviews for ``product_id`` (empty list if none)."""
        return self._delegate.get_reviews(product_id)

    def get_summary(self, product_id: str) -> Optional[Dict[str, Any]]:
        """Return the product summary for ``product_id``, or ``None`` if absent."""
        return self._delegate.get_summary(product_id)


def _response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    """Build an API-Gateway proxy response dict with a JSON body."""
    return {
        "statusCode": status_code,
        "headers": dict(_JSON_HEADERS),
        "body": json.dumps(body, ensure_ascii=False),
    }


def _product_id_from_event(event: Dict[str, Any]) -> Optional[str]:
    """Extract the ``productId`` path parameter from the event, if present."""
    path_params = event.get("pathParameters") or {}
    return path_params.get(PATH_PARAM_PRODUCT_ID)


def _resource_from_event(event: Dict[str, Any]) -> Optional[str]:
    """Determine whether the request targets reviews or the summary.

    Prefers the API Gateway ``resource`` template (e.g.
    ``/products/{productId}/summary``) and falls back to the concrete
    ``path``. Returns :data:`RESOURCE_REVIEWS`, :data:`RESOURCE_SUMMARY`, or
    ``None`` when neither can be identified.
    """
    route = event.get("resource") or event.get("path") or ""
    route = route.rstrip("/")
    if route.endswith(RESOURCE_SUMMARY):
        return RESOURCE_SUMMARY
    if route.endswith(RESOURCE_REVIEWS):
        return RESOURCE_REVIEWS
    return None


def handler(
    event: Optional[Dict[str, Any]] = None,
    context: Optional[Any] = None,
    *,
    store: Optional[api.ReviewStore] = None,
    s3_client: Optional[Any] = None,
) -> Dict[str, Any]:
    """Serve a product's reviews or summary from the S3 serving store.

    Routes on the requested resource: ``.../reviews`` calls
    :func:`review_pipeline.api.get_product_reviews`, ``.../summary`` calls
    :func:`review_pipeline.api.get_product_summary`.

    Args:
        event: the API Gateway proxy event. Must carry the ``productId`` path
            parameter and identify the resource via ``resource`` or ``path``.
        context: the Lambda context (unused).
        store: optional :class:`review_pipeline.api.ReviewStore`, injected by
            tests. Defaults to an :class:`S3ReviewStore` over the data bucket.
        s3_client: optional S3 client passed to the default store (injected by
            tests); ignored when ``store`` is supplied.

    Returns:
        An API-Gateway proxy response dict::

            {"statusCode": int, "headers": {...}, "body": "<json>"}

        ``200`` with the payload on success; ``404`` with the not-found shape
        when the product is unknown; ``400`` when the request is malformed.
    """
    event = event or {}
    cfg = load_config()
    configure_logging(cfg.log_level)

    product_id = _product_id_from_event(event)
    resource = _resource_from_event(event)

    if not product_id:
        logger.warning("api request missing productId path parameter")
        return _response(400, {"error": "bad_request", "message": "productId is required"})
    if resource is None:
        logger.warning(
            "api request for unknown resource", extra={"product_id": product_id}
        )
        return _response(
            400, {"error": "bad_request", "message": "unknown resource"}
        )

    if store is None:
        bucket = keys.resolve_bucket()
        store = S3ReviewStore(bucket, client=s3_client)

    if resource == RESOURCE_SUMMARY:
        result = api.get_product_summary(product_id, store)
    else:
        result = api.get_product_reviews(product_id, store)

    status_code = 404 if result.get(api.ERROR_KEY) == api.ERROR_NOT_FOUND else 200
    logger.info(
        "api handler served request",
        extra={
            "product_id": product_id,
            "resource": resource,
            "status_code": status_code,
        },
    )
    return _response(status_code, result)
