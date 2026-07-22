from __future__ import annotations

import threading

from agentbus.sandbox import BoundedProcessOutput
from agentbus.tools.protocol import (
    ToolOutputChunk,
    ToolOutputStream,
    ToolResourceBudget,
)


def _budget(
    *,
    stdout: int = 32,
    stderr: int = 32,
    combined: int = 64,
) -> ToolResourceBudget:
    return ToolResourceBudget(
        stdout_bytes=stdout,
        stderr_bytes=stderr,
        combined_output_bytes=combined,
    )


def test_output_capture_enforces_stream_and_combined_byte_limits() -> None:
    capture = BoundedProcessOutput(_budget(stdout=5, stderr=5, combined=7))

    capture.consume(ToolOutputStream.STDOUT, b"abcdef")
    capture.consume(ToolOutputStream.STDERR, b"uvwxyz")
    result = capture.finalize()

    assert result.stdout == "abcde"
    assert result.stderr == "uv"
    assert result.stdout_bytes == 6
    assert result.stderr_bytes == 6
    assert result.retained_stdout_bytes == 5
    assert result.retained_stderr_bytes == 2
    assert result.stdout_truncated is True
    assert result.stderr_truncated is True


def test_streamed_output_is_redacted_before_callback() -> None:
    events: list[ToolOutputChunk] = []
    capture = BoundedProcessOutput(_budget(), callback=events.append)

    capture.consume(ToolOutputStream.STDOUT, b"API_KEY=super-secret")
    assert events == []
    capture.consume(ToolOutputStream.STDOUT, b"\nready\n")
    result = capture.finalize()

    assert "super-secret" not in result.stdout
    assert "super-secret" not in "".join(event.text for event in events)
    assert "API_KEY=[REDACTED]" in events[0].text
    assert [event.sequence for event in events] == [1]


def test_output_event_count_and_callback_failures_are_bounded() -> None:
    def failing_callback(_event: ToolOutputChunk) -> None:
        raise RuntimeError("consumer unavailable")

    capture = BoundedProcessOutput(
        _budget(),
        callback=failing_callback,
        maximum_events=2,
    )
    capture.consume(ToolOutputStream.STDOUT, b"one\n")
    capture.consume(ToolOutputStream.STDOUT, b"two\n")
    capture.consume(ToolOutputStream.STDOUT, b"three\n")

    result = capture.finalize()

    assert result.output_events == 2
    assert result.output_events_truncated is True
    assert result.callback_failures == 2


def test_output_above_inline_protocol_limit_sets_truncation_flag() -> None:
    capture = BoundedProcessOutput(
        _budget(stdout=100_000, stderr=1, combined=100_001)
    )
    capture.consume(ToolOutputStream.STDOUT, b"x" * 70_000)

    result = capture.finalize()

    assert len(result.stdout.encode("utf-8")) == 65_536
    assert result.stdout_truncated is True
    assert result.stdout_bytes == 70_000
    assert result.retained_stdout_bytes == 70_000


def test_output_capture_is_safe_for_parallel_pipe_readers() -> None:
    capture = BoundedProcessOutput(
        _budget(stdout=1_024, stderr=1_024, combined=2_048)
    )

    stdout = threading.Thread(
        target=lambda: [
            capture.consume(ToolOutputStream.STDOUT, b"out\n")
            for _ in range(100)
        ]
    )
    stderr = threading.Thread(
        target=lambda: [
            capture.consume(ToolOutputStream.STDERR, b"err\n")
            for _ in range(100)
        ]
    )
    stdout.start()
    stderr.start()
    stdout.join(timeout=2)
    stderr.join(timeout=2)
    result = capture.finalize()

    assert stdout.is_alive() is False
    assert stderr.is_alive() is False
    assert result.stdout_bytes == 400
    assert result.stderr_bytes == 400
    assert result.stdout.count("out") == 100
    assert result.stderr.count("err") == 100
