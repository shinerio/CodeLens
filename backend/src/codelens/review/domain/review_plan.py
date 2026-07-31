import hashlib
import json
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Literal

type SelectionMode = Literal["fixed", "adaptive"]
type BudgetProfileValue = Literal["lean", "standard", "deep"]


class ReviewPass(IntEnum):
    """Stable pass numbers used in persisted multi-Agent node identities."""

    PLANNER = 0
    REVIEWER = 1
    RESOLVER = 2
    VERIFIER = 3


class ReviewPlanNodeType(StrEnum):
    """Classify one immutable node in the host-controlled Review DAG."""

    PLANNER = "planner"
    REVIEWER = "reviewer"
    RESOLVER = "resolver"
    VERIFIER = "verifier"


class CoverageStatus(StrEnum):
    """Describe whether one planned Review perspective produced a result."""

    PLANNED = "planned"
    COMPLETED = "completed"
    FAILED = "failed"
    OMITTED = "omitted"


_PASS_BY_NODE_TYPE = {
    ReviewPlanNodeType.PLANNER: ReviewPass.PLANNER,
    ReviewPlanNodeType.REVIEWER: ReviewPass.REVIEWER,
    ReviewPlanNodeType.RESOLVER: ReviewPass.RESOLVER,
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
    budget_profile: BudgetProfileValue
    reviewer_references: tuple[str, ...]
    nodes: tuple[ReviewPlanNode, ...]
    planner_reason: str | None
    plan_hash: str

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        selection_mode: SelectionMode,
        budget_profile: BudgetProfileValue,
        reviewer_references: tuple[str, ...],
        nodes: tuple[ReviewPlanNode, ...],
        planner_reason: str | None,
    ) -> "ReviewPlan":
        """Canonicalize plan inputs and enforce topology invariants before hashing."""

        if not reviewer_references:
            raise ValueError("Review plan requires at least one reviewer")
        if len(reviewer_references) != len(set(reviewer_references)):
            raise ValueError("Review plan contains duplicate reviewers")
        if "general:v1" in reviewer_references and reviewer_references != ("general:v1",):
            raise ValueError("General reviewer must run alone")
        if "correctness:v1" in reviewer_references and reviewer_references != (
            "correctness:v1",
        ):
            raise ValueError("correctness:v1 is legacy single-reviewer only")
        if selection_mode == "adaptive" and not planner_reason:
            raise ValueError("Adaptive plan requires a planner reason")
        if selection_mode == "fixed" and planner_reason is not None:
            raise ValueError("Fixed plan cannot contain a planner reason")
        if any(node.task_id != task_id for node in nodes):
            raise ValueError("Review plan node belongs to another task")
        node_ids = tuple(node.node_id for node in nodes)
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("Review plan contains duplicate nodes")
        known_node_ids = set(node_ids)
        if any(
            dependency not in known_node_ids
            for node in nodes
            for dependency in node.depends_on
        ):
            raise ValueError("Review plan contains an unknown dependency")
        reviewer_node_references = {
            node.agent_reference
            for node in nodes
            if node.node_type is ReviewPlanNodeType.REVIEWER
        }
        if reviewer_node_references != set(reviewer_references):
            raise ValueError("Review plan reviewer nodes do not match selected reviewers")
        resolver_count = sum(
            node.node_type is ReviewPlanNodeType.RESOLVER for node in nodes
        )
        is_multi_specialist = len(reviewer_references) > 1
        if is_multi_specialist and resolver_count == 0:
            raise ValueError("multi-specialist plan requires a resolver")
        if resolver_count > 1:
            raise ValueError("Review plan permits at most one resolver")

        canonical_reviewers = tuple(sorted(reviewer_references))
        canonical_nodes = tuple(sorted(nodes, key=lambda node: node.node_id))
        payload = {
            "budget_profile": budget_profile,
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
                for node in canonical_nodes
            ],
            "planner_reason": planner_reason,
            "reviewer_references": canonical_reviewers,
            "selection_mode": selection_mode,
            "task_id": task_id,
        }
        canonical_bytes = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return cls(
            task_id=task_id,
            selection_mode=selection_mode,
            budget_profile=budget_profile,
            reviewer_references=canonical_reviewers,
            nodes=canonical_nodes,
            planner_reason=planner_reason,
            plan_hash=hashlib.sha256(canonical_bytes).hexdigest(),
        )

