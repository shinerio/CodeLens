import hashlib
import json
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Literal

type SelectionMode = Literal["fixed", "adaptive"]


class ReviewPass(IntEnum):
    """Stable pass numbers used in persisted multi-Agent node identities."""

    PLANNER = 0
    REVIEWER = 1
    VERIFIER = 2


class ReviewPlanNodeType(StrEnum):
    """Classify one immutable node in the host-controlled Review DAG."""

    PLANNER = "planner"
    REVIEWER = "reviewer"
    VERIFIER = "verifier"


@dataclass(frozen=True)
class ReviewerPlanGuidance:
    """Persist bounded Planner attention hints without narrowing Snapshot access."""

    reviewer_reference: str
    reason_codes: tuple[str, ...]
    focus_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.reviewer_reference:
            raise ValueError("Reviewer guidance requires a reviewer reference")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("Reviewer guidance contains duplicate reason codes")
        if len(self.focus_paths) != len(set(self.focus_paths)):
            raise ValueError("Reviewer guidance contains duplicate focus paths")


@dataclass(frozen=True)
class PlanCapabilityDegradation:
    """Record optional frozen capability omissions without changing task success."""

    agent_reference: str
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.agent_reference or not self.reason_codes:
            raise ValueError("Capability degradation requires an Agent and reason codes")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("Capability degradation contains duplicate reason codes")


class CoverageStatus(StrEnum):
    """Describe whether one planned Review perspective produced a result."""

    PLANNED = "planned"
    COMPLETED = "completed"
    FAILED = "failed"
    OMITTED = "omitted"


_PASS_BY_NODE_TYPE = {
    ReviewPlanNodeType.PLANNER: ReviewPass.PLANNER,
    ReviewPlanNodeType.REVIEWER: ReviewPass.REVIEWER,
    ReviewPlanNodeType.VERIFIER: ReviewPass.VERIFIER,
}


@dataclass(frozen=True)
class ReviewPlanNode:
    """Identify one logical DAG node independently from its physical attempts."""

    node_id: str
    task_id: str
    node_type: ReviewPlanNodeType
    agent_reference: str
    pass_index: ReviewPass
    shard_id: str
    logical_attempt_group: str
    depends_on: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        node_type: ReviewPlanNodeType,
        agent_reference: str,
        pass_index: int,
        shard_id: str,
        logical_attempt_group: str,
        depends_on: tuple[str, ...],
    ) -> "ReviewPlanNode":
        """Create a stable logical identity and reject malformed DAG metadata."""

        review_pass = ReviewPass(pass_index)
        if review_pass is not _PASS_BY_NODE_TYPE[node_type]:
            raise ValueError("Review plan node type does not match its pass")
        if not all((task_id, agent_reference, shard_id, logical_attempt_group)):
            raise ValueError("Review plan node identity fields cannot be empty")
        if len(depends_on) != len(set(depends_on)):
            raise ValueError("Review plan node contains duplicate dependencies")
        identity = "\0".join(
            (
                task_id,
                agent_reference,
                str(review_pass.value),
                shard_id,
                logical_attempt_group,
            )
        )
        node_id = f"node_{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"
        if node_id in depends_on:
            raise ValueError("Review plan node cannot depend on itself")
        return cls(
            node_id=node_id,
            task_id=task_id,
            node_type=node_type,
            agent_reference=agent_reference,
            pass_index=review_pass,
            shard_id=shard_id,
            logical_attempt_group=logical_attempt_group,
            depends_on=tuple(sorted(depends_on)),
        )


@dataclass(frozen=True)
class ReviewPlan:
    """Freeze the canonical Reviewer set and host-controlled execution DAG."""

    task_id: str
    selection_mode: SelectionMode
    reviewer_references: tuple[str, ...]
    nodes: tuple[ReviewPlanNode, ...]
    planner_reason: str | None
    plan_hash: str
    reviewer_guidance: tuple[ReviewerPlanGuidance, ...] = ()
    capability_degradations: tuple[PlanCapabilityDegradation, ...] = ()

    def canonical_json(self) -> str:
        """Serialize the complete frozen plan for durable hash verification."""

        return json.dumps(
            self._payload(
                self.task_id,
                self.selection_mode,
                self.reviewer_references,
                self.nodes,
                self.planner_reason,
                self.reviewer_guidance,
                self.capability_degradations,
            ),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, payload: str, expected_hash: str) -> "ReviewPlan":
        """Rebuild a persisted plan and reject any canonical hash mismatch."""

        value = json.loads(payload)
        nodes = tuple(
            ReviewPlanNode.create(
                task_id=str(item["task_id"]),
                node_type=ReviewPlanNodeType(str(item["node_type"])),
                agent_reference=str(item["agent_reference"]),
                pass_index=int(item["pass_index"]),
                shard_id=str(item["shard_id"]),
                logical_attempt_group=str(item["logical_attempt_group"]),
                depends_on=tuple(str(dependency) for dependency in item["depends_on"]),
            )
            for item in value["nodes"]
        )
        plan = cls.create(
            task_id=str(value["task_id"]),
            selection_mode=value["selection_mode"],
            reviewer_references=tuple(str(item) for item in value["reviewer_references"]),
            nodes=nodes,
            planner_reason=(
                str(value["planner_reason"]) if value["planner_reason"] is not None else None
            ),
            reviewer_guidance=tuple(
                ReviewerPlanGuidance(
                    reviewer_reference=str(item["reviewer_reference"]),
                    reason_codes=tuple(str(code) for code in item["reason_codes"]),
                    focus_paths=tuple(str(path) for path in item["focus_paths"]),
                )
                for item in value.get("reviewer_guidance", ())
            ),
            capability_degradations=tuple(
                PlanCapabilityDegradation(
                    agent_reference=str(item["agent_reference"]),
                    reason_codes=tuple(str(code) for code in item["reason_codes"]),
                )
                for item in value.get("capability_degradations", ())
            ),
        )
        if plan.plan_hash != expected_hash or plan.canonical_json() != payload:
            raise ValueError("persisted Review Plan hash mismatch")
        return plan

    @staticmethod
    def _payload(
        task_id: str,
        selection_mode: SelectionMode,
        reviewer_references: tuple[str, ...],
        nodes: tuple[ReviewPlanNode, ...],
        planner_reason: str | None,
        reviewer_guidance: tuple[ReviewerPlanGuidance, ...],
        capability_degradations: tuple[PlanCapabilityDegradation, ...],
    ) -> dict[str, object]:
        return {
            "capability_degradations": [
                {
                    "agent_reference": degradation.agent_reference,
                    "reason_codes": degradation.reason_codes,
                }
                for degradation in capability_degradations
            ],
            "nodes": [
                {
                    "agent_reference": node.agent_reference,
                    "depends_on": node.depends_on,
                    "logical_attempt_group": node.logical_attempt_group,
                    "node_id": node.node_id,
                    "node_type": node.node_type.value,
                    "pass_index": node.pass_index.value,
                    "shard_id": node.shard_id,
                    "task_id": node.task_id,
                }
                for node in nodes
            ],
            "planner_reason": planner_reason,
            "reviewer_references": reviewer_references,
            "reviewer_guidance": [
                {
                    "focus_paths": guidance.focus_paths,
                    "reason_codes": guidance.reason_codes,
                    "reviewer_reference": guidance.reviewer_reference,
                }
                for guidance in reviewer_guidance
            ],
            "selection_mode": selection_mode,
            "task_id": task_id,
        }

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        selection_mode: SelectionMode,
        reviewer_references: tuple[str, ...],
        nodes: tuple[ReviewPlanNode, ...],
        planner_reason: str | None,
        reviewer_guidance: tuple[ReviewerPlanGuidance, ...] = (),
        capability_degradations: tuple[PlanCapabilityDegradation, ...] = (),
    ) -> "ReviewPlan":
        """Canonicalize plan inputs and enforce topology invariants before hashing."""

        if not reviewer_references:
            raise ValueError("Review plan requires at least one reviewer")
        if len(reviewer_references) != len(set(reviewer_references)):
            raise ValueError("Review plan contains duplicate reviewers")
        if "general:v2" in reviewer_references and reviewer_references != ("general:v2",):
            raise ValueError("General reviewer must run alone")
        if selection_mode == "adaptive" and not planner_reason:
            raise ValueError("Adaptive plan requires a planner reason")
        if selection_mode == "fixed" and planner_reason is not None:
            raise ValueError("Fixed plan cannot contain a planner reason")
        guidance_references = tuple(item.reviewer_reference for item in reviewer_guidance)
        if len(guidance_references) != len(set(guidance_references)):
            raise ValueError("Review plan contains duplicate reviewer guidance")
        if not set(guidance_references).issubset(reviewer_references):
            raise ValueError("Review plan guidance references an unselected reviewer")
        degradation_references = tuple(item.agent_reference for item in capability_degradations)
        if len(degradation_references) != len(set(degradation_references)):
            raise ValueError("Review plan contains duplicate capability degradations")
        if not set(degradation_references).issubset(reviewer_references):
            raise ValueError("Capability degradation references an unselected reviewer")
        if any(node.task_id != task_id for node in nodes):
            raise ValueError("Review plan node belongs to another task")
        node_ids = tuple(node.node_id for node in nodes)
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("Review plan contains duplicate nodes")
        known_node_ids = set(node_ids)
        if any(
            dependency not in known_node_ids for node in nodes for dependency in node.depends_on
        ):
            raise ValueError("Review plan contains an unknown dependency")
        reviewer_node_references = {
            node.agent_reference for node in nodes if node.node_type is ReviewPlanNodeType.REVIEWER
        }
        if reviewer_node_references != set(reviewer_references):
            raise ValueError("Review plan reviewer nodes do not match selected reviewers")
        planner_nodes = tuple(
            node for node in nodes if node.node_type is ReviewPlanNodeType.PLANNER
        )
        reviewer_nodes = tuple(
            node for node in nodes if node.node_type is ReviewPlanNodeType.REVIEWER
        )
        if selection_mode == "adaptive":
            if len(planner_nodes) != 1 or planner_nodes[0].agent_reference != "review-planner:v2":
                raise ValueError("Adaptive plan requires one review-planner:v2 node")
            if any(node.depends_on != (planner_nodes[0].node_id,) for node in reviewer_nodes):
                raise ValueError("Adaptive Reviewer nodes must depend on the Planner")
        elif planner_nodes:
            raise ValueError("Fixed plan cannot contain a Planner node")
        elif any(node.depends_on for node in reviewer_nodes):
            raise ValueError("Fixed Reviewer nodes cannot have dependencies")
        verifier_nodes = tuple(
            node for node in nodes if node.node_type is ReviewPlanNodeType.VERIFIER
        )
        if len(verifier_nodes) != 1 or verifier_nodes[0].shard_id != "batch":
            raise ValueError("Review plan requires one batched verifier")
        if verifier_nodes[0].depends_on != tuple(
            sorted(node.node_id for node in reviewer_nodes)
        ):
            raise ValueError("batched verifier must depend on every Reviewer node")

        canonical_reviewers = tuple(sorted(reviewer_references))
        canonical_nodes = tuple(sorted(nodes, key=lambda node: node.node_id))
        canonical_guidance = tuple(
            sorted(reviewer_guidance, key=lambda item: item.reviewer_reference)
        )
        canonical_degradations = tuple(
            sorted(capability_degradations, key=lambda item: item.agent_reference)
        )
        payload = cls._payload(
            task_id,
            selection_mode,
            canonical_reviewers,
            canonical_nodes,
            planner_reason,
            canonical_guidance,
            canonical_degradations,
        )
        canonical_bytes = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return cls(
            task_id=task_id,
            selection_mode=selection_mode,
            reviewer_references=canonical_reviewers,
            nodes=canonical_nodes,
            planner_reason=planner_reason,
            plan_hash=hashlib.sha256(canonical_bytes).hexdigest(),
            reviewer_guidance=canonical_guidance,
            capability_degradations=canonical_degradations,
        )
