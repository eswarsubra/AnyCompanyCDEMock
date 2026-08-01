"""CDK stacks for the AnyCompany Apparel review pipeline.

Three stacks, split by concern (see docs/infra-contracts.md):

* :class:`DataStack` — the single S3 data bucket shared by every stage.
* :class:`PipelineStack` — the four batch Lambdas + Step Functions state machine.
* :class:`ApiStack` — the read API Lambda + API Gateway REST API.

The stacks are wired together in ``infra/app.py`` by passing the bucket from
DataStack into the other two.
"""

from .api_stack import ApiStack
from .data_stack import DataStack
from .pipeline_stack import PipelineStack

__all__ = ["DataStack", "PipelineStack", "ApiStack"]
