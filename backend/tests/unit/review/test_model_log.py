import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from codelens.bootstrap.logging import configure_process_logging
from codelens.review.infrastructure.model_log import ModelTranscriptLogWriter
from codelens.review.infrastructure.transcripts import TranscriptEntry


def _entry(sequence: int) -> TranscriptEntry:
    return TranscriptEntry(
        sequence=sequence,
        kind="prompt",
        content=f"prompt {sequence}",
        created_at=datetime.now(UTC),
        redacted=True,
        truncated=False,
    )


def test_model_output_logging_defaults_on_and_honors_runtime_disable(tmp_path: Path) -> None:
    model_log = tmp_path / "model.log"
    configure_process_logging("unified", log_directory=tmp_path, data_directory=tmp_path)
    writer = ModelTranscriptLogWriter(tmp_path)
    entries = (_entry(1), _entry(2))

    asyncio.run(writer.write("task-1", entries))

    from codelens.bootstrap.logging import set_model_output_logging_enabled

    set_model_output_logging_enabled(tmp_path, False)
    before = model_log.read_text(encoding="utf-8")
    asyncio.run(writer.write("task-2", entries))

    assert "prompt 1" in before
    assert "prompt 2" in before
    after = model_log.read_text(encoding="utf-8")
    assert "task-2" not in after

    for logger_name in ("", "codelens", "codelens.model"):
        logger = logging.getLogger(logger_name)
        for handler in tuple(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
