from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()

review_tasks = Table(
    "review_tasks",
    metadata,
    Column("task_id", String(128), primary_key=True),
    Column("repository_id", String(128), nullable=False, index=True),
    Column("repository_path", Text),
    Column("repository_realpath_hash", String(64), nullable=False),
    Column("git_common_dir_hash", String(64), nullable=False, index=True),
    Column("scope_json", Text, nullable=False),
    Column("base_oid", String(64), nullable=False),
    Column("head_oid", String(64), nullable=False),
    Column("overlay_hash", String(64)),
    Column("overlay_artifact_ref", String(128)),
    Column("target_paths_json", Text),
    Column("status", String(32), nullable=False),
    Column("selected_agent_versions_json", Text, nullable=False),
    Column("prompt_locale", String(8), nullable=False, default="en"),
    Column("external_context_json", Text),
    Column("worktree_id", String(128)),
    Column("snapshot_id", String(128)),
    Column("cancellation_requested", Boolean, nullable=False, default=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("deleted_at", DateTime(timezone=True)),
)

recent_repositories = Table(
    "recent_repositories",
    metadata,
    Column("repository_path", Text, primary_key=True),
    Column("last_reviewed_at", DateTime(timezone=True), nullable=False, index=True),
)

recent_repository_settings = Table(
    "recent_repository_settings",
    metadata,
    Column("settings_id", Integer, primary_key=True),
    Column("recent_repository_limit", Integer, nullable=False),
    CheckConstraint(
        "recent_repository_limit BETWEEN 1 AND 20",
        name="ck_recent_repository_settings_limit",
    ),
)

task_worktrees = Table(
    "task_worktrees",
    metadata,
    Column("worktree_id", String(128), primary_key=True),
    Column(
        "task_id",
        String(128),
        ForeignKey("review_tasks.task_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    ),
    Column("owned_path_hash", String(64), nullable=False),
    Column("common_dir_hash", String(64), nullable=False, index=True),
    Column("head_oid", String(64), nullable=False),
    Column("ownership_token_hash", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

jobs = Table(
    "jobs",
    metadata,
    Column(
        "task_id",
        String(128),
        ForeignKey("review_tasks.task_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("status", String(32), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

dag_checkpoints = Table(
    "dag_checkpoints",
    metadata,
    Column(
        "task_id",
        String(128),
        ForeignKey("review_tasks.task_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("node_key", String(256), primary_key=True),
    Column("logical_attempt_group", String(128), nullable=False),
    Column("status", String(32), nullable=False),
    Column("execution_attempts", Integer, nullable=False, default=0),
    Column("validation_attempts", Integer, nullable=False, default=0),
    Column("artifact_ref", String(128)),
    Column("artifact_hash", String(64)),
    Column("review_completion_status", String(32), nullable=False, server_default="complete"),
    Column("error_code", String(128)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

events = Table(
    "events",
    metadata,
    Column("event_id", Integer, primary_key=True, autoincrement=True),
    Column(
        "task_id",
        String(128),
        ForeignKey("review_tasks.task_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("event_type", String(128), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

artifacts = Table(
    "artifacts",
    metadata,
    Column("reference", String(128), primary_key=True),
    Column("run_id", String(128), nullable=False, index=True),
    Column("storage_key", String(128), nullable=False, unique=True),
    Column("content_hash", String(64), nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

findings = Table(
    "findings",
    metadata,
    Column("finding_id", String(128), primary_key=True),
    Column(
        "task_id",
        String(128),
        ForeignKey("review_tasks.task_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("node_key", String(256), nullable=False),
    Column("fingerprint", String(256), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("severity", String(16), nullable=False),
    Column("confidence", Float, nullable=False),
    Column("path", String(1024), nullable=False),
    Column("start_line", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("task_id", "fingerprint", name="uq_findings_task_fingerprint"),
)
