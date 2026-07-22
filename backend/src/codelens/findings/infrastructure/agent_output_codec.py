import json
from dataclasses import dataclass

from pydantic import ValidationError

from codelens.findings.infrastructure.model_output import FindingBatchSchema


class AgentOutputCodecError(ValueError):
    """Reject an unsupported envelope or structurally invalid untrusted output."""


@dataclass(frozen=True)
class AgentOutputCodec:
    """Implement the review output Port for the FindingBatch boundary contract."""

    schema_version: str

    def __post_init__(self) -> None:
        if self.schema_version != "1":
            raise AgentOutputCodecError("unsupported Agent output schema version")

    @property
    def output_type(self) -> type[object]:
        """Return the Pydantic boundary type without leaking it through the Port."""

        return FindingBatchSchema

    def encode(self, final_output: object) -> bytes:
        """Revalidate SDK output and serialize only the declared envelope."""

        try:
            batch = FindingBatchSchema.model_validate(final_output)
        except ValidationError as error:
            raise AgentOutputCodecError("Agent output does not match its schema") from error
        if batch.schema_version != self.schema_version:
            raise AgentOutputCodecError("Agent output schema version mismatch")
        return json.dumps(
            batch.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def json_schema(self) -> str:
        """Expose the stable envelope schema without coupling runtimes to Pydantic types."""

        return json.dumps(
            FindingBatchSchema.model_json_schema(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def decode(self, payload: bytes) -> FindingBatchSchema:
        """Revalidate persisted checkpoint bytes before downstream domain validation."""

        try:
            decoded = json.loads(payload)
            batch = FindingBatchSchema.model_validate(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            raise AgentOutputCodecError("persisted Agent output is invalid") from error
        if batch.schema_version != self.schema_version:
            raise AgentOutputCodecError("persisted Agent output schema version mismatch")
        return batch
