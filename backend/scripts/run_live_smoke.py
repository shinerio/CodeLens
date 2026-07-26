from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path

from codelens.bootstrap.settings import Settings
from codelens.bootstrap.unified import build_unified_backend
from codelens.review.application.commands import CreateReviewCommand
from codelens.reviewer_catalog.domain.provider_config import ModelGateway, ModelGatewayCatalog
from codelens.reviewer_catalog.infrastructure.file_provider_config import (
    FilesystemModelProviderConfigAdapter,
)
from codelens.testing.correctness_fixture import prepare_simple_branch_repository
from codelens.workspace.domain.models import BranchScope


async def _run() -> int:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY must be set for the live smoke test")
        return 1
    model = os.environ.get("CODELENS_OPENAI_MODEL")
    if not model:
        print("CODELENS_OPENAI_MODEL must be set for the live smoke test")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        fixture = await prepare_simple_branch_repository(workspace)
        settings = Settings(
            data_dir=workspace / "data",
            repository_roots=(fixture.repository,),
        )
        await FilesystemModelProviderConfigAdapter(settings.data_dir).save_catalog(
            ModelGatewayCatalog(
                active_gateway_id="gateway_live_smoke",
                gateways=(
                    ModelGateway(
                        gateway_id="gateway_live_smoke",
                        name="Live smoke gateway",
                        api_key=api_key,
                        model=model,
                        base_url=os.environ.get(
                            "OPENAI_BASE_URL", "https://api.openai.com/v1"
                        ),
                    ),
                ),
            )
        )
        backend = build_unified_backend(settings)
        components = backend.components
        stop_event = asyncio.Event()
        runner: asyncio.Task[None] | None = None
        started = time.perf_counter()
        try:
            await backend.start()
            runner = asyncio.create_task(backend.scheduler.run(stop_event))
            repository = await components.repository_inspector.inspect(fixture.repository)
            review = await components.create_review.handle(
                CreateReviewCommand(
                    repository=repository,
                    scope=BranchScope(
                        base_ref="main",
                        target_ref="fixture-change",
                        include_workspace_changes=False,
                    ),
                    selected_agent_versions=("correctness:v1",),
                )
            )
            task_id = review.task_id
            while True:
                current = await components.get_review.handle(task_id)
                if current.status in {"completed", "partial", "failed", "canceled"}:
                    break
                await asyncio.sleep(0.1)
            findings = await components.review_store.list_findings(task_id)
            elapsed = time.perf_counter() - started
            print(f"task_id={task_id}")
            print(f"model={model}")
            print(f"elapsed_seconds={elapsed:.2f}")
            print("token_usage=unavailable")
            print(f"validated_findings={len(findings)}")
            if current.status != "completed" or not findings:
                return 1
            return 0
        finally:
            stop_event.set()
            if runner is not None:
                await runner
            await backend.close()


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
