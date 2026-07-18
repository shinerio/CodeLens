from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path

from codelens.bootstrap.settings import Settings
from codelens.interface.http.dependencies import build_components
from codelens.review.application.commands import CreateReviewCommand
from codelens.testing.correctness_fixture import prepare_simple_branch_repository
from codelens.worker.main import build_worker
from codelens.workspace.domain.models import UncommittedScope


async def _run() -> int:
    if not os.environ.get("OPENAI_API_KEY"):
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
            openai_model=model,
        )
        components = build_components(settings)
        worker = build_worker(settings)
        stop_event = asyncio.Event()
        runner = asyncio.create_task(worker.run(stop_event))
        started = time.perf_counter()
        try:
            await components.start()
            repository = await components.repository_inspector.inspect(fixture.repository)
            review = await components.create_review.handle(
                CreateReviewCommand(
                    repository=repository,
                    scope=UncommittedScope(),
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
            await runner
            await components.close()


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
