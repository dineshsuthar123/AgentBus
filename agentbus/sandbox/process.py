from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from agentbus.execution.cancellation import CancellationRequested, CancellationToken
from agentbus.sandbox.environment import (
    environment_diagnostics,
    sanitized_process_environment,
)
from agentbus.sandbox.errors import (
    ExecutableValidationError,
    ProcessSupervisionError,
    WorkingDirectoryValidationError,
)
from agentbus.sandbox.limits import (
    effective_wall_clock_limit,
    process_resource_usage,
)
from agentbus.sandbox.output import (
    BoundedProcessOutput,
    OutputCallback,
    ProcessOutputSnapshot,
)
from agentbus.sandbox.platform import (
    ExecutableCatalog,
    ExecutableIdentity,
    validate_working_directory,
    windows_system_command_processor,
)
from agentbus.sandbox.process_tree import ManagedProcessTree, ProcessTreeMetadata
from agentbus.tools.protocol import ToolOutputStream, ToolResourceBudget, ToolResourceUsage


_BATCH_UNSAFE_ARGUMENT = re.compile(r'[\x00-\x1f"%!&|<>^()]')


@dataclass(frozen=True)
class ProcessExecutionResult:
    executable: ExecutableIdentity
    working_directory: Path
    pid: int | None
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    duration_seconds: float
    timed_out: bool
    cancelled: bool
    termination_reason: str | None
    resource_usage: ToolResourceUsage
    process_tree: ProcessTreeMetadata | None
    safe_diagnostic_metadata: dict[str, object]

    @property
    def passed(self) -> bool:
        return (
            not self.timed_out
            and not self.cancelled
            and self.exit_code == 0
        )


class _ProcessCancelled(RuntimeError):
    def __init__(self, result: ProcessExecutionResult):
        self.result = result
        super().__init__("Managed process was cancelled.")


class ControlledProcessSupervisor:
    def __init__(
        self,
        worktree: str | Path,
        *,
        catalog: ExecutableCatalog | None = None,
        source_environment: Mapping[str, str] | None = None,
        poll_interval_seconds: float = 0.05,
        termination_grace_seconds: float = 1.0,
    ) -> None:
        self.worktree = validate_working_directory(worktree)
        self.catalog = catalog or ExecutableCatalog.standard()
        self._command_processor_catalog = (
            ExecutableCatalog(
                {"agentbus-command-processor": windows_system_command_processor()}
            )
            if os.name == "nt"
            else None
        )
        if poll_interval_seconds <= 0 or termination_grace_seconds <= 0:
            raise ValueError("Process polling and termination grace must be positive.")
        self.poll_interval_seconds = poll_interval_seconds
        self.termination_grace_seconds = termination_grace_seconds
        self._executable_directories = self.catalog.executable_directories
        self._base_environment = sanitized_process_environment(
            source=source_environment,
            executable_directories=self._executable_directories,
        )

    def run(
        self,
        executable: str | Path,
        arguments: Sequence[str] = (),
        *,
        working_directory: str | Path | None = None,
        environment_overrides: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
        resource_budget: ToolResourceBudget | None = None,
        cancellation: CancellationToken | None = None,
        output_callback: OutputCallback | None = None,
        task_id: str | None = None,
    ) -> ProcessExecutionResult:
        identity = self.catalog.resolve(executable)
        command_arguments = _validate_arguments(arguments)
        current_worktree = validate_working_directory(self.worktree)
        if current_worktree != self.worktree:
            raise WorkingDirectoryValidationError(
                "Assigned worktree identity changed after supervisor creation."
            )
        cwd = validate_working_directory(current_worktree, working_directory)
        budget = resource_budget or ToolResourceBudget()
        effective_timeout = effective_wall_clock_limit(timeout_seconds, budget)
        operation = (
            cancellation.operation(
                f"process:{identity.alias}",
                source="sandbox-process",
                interruptible=True,
                task_id=task_id,
            )
            if cancellation is not None
            else nullcontext()
        )
        try:
            with operation:
                result = self._run_process(
                    identity=identity,
                    arguments=command_arguments,
                    cwd=cwd,
                    environment_overrides=environment_overrides,
                    effective_timeout=effective_timeout,
                    budget=budget,
                    cancellation=cancellation,
                    output_callback=output_callback,
                )
                if result.cancelled:
                    raise _ProcessCancelled(result)
                return result
        except _ProcessCancelled as exc:
            return exc.result
        except CancellationRequested:
            return self._cancelled_before_launch(
                identity=identity,
                cwd=cwd,
                budget=budget,
                effective_timeout=effective_timeout,
            )

    def _run_process(
        self,
        *,
        identity: ExecutableIdentity,
        arguments: tuple[str, ...],
        cwd: Path,
        environment_overrides: Mapping[str, str] | None,
        effective_timeout: float,
        budget: ToolResourceBudget,
        cancellation: CancellationToken | None,
        output_callback: OutputCallback | None,
    ) -> ProcessExecutionResult:
        capture = BoundedProcessOutput(budget, callback=output_callback)
        started = time.monotonic()
        command, launch_backend, command_processor, executable_override = (
            self._launch_command(identity, arguments)
        )
        try:
            isolated_home_context = tempfile.TemporaryDirectory(
                prefix="agentbus-tool-"
            )
        except OSError as exc:
            raise ProcessSupervisionError(
                "Could not create the isolated process temporary directory; verify "
                "the temporary directory is writable and has available space."
            ) from exc
        with isolated_home_context as isolated_home:
            environment = sanitized_process_environment(
                source=self._base_environment,
                executable_directories=self._executable_directories,
                overrides=environment_overrides,
                isolated_home=isolated_home,
            )
            if command_processor is not None:
                environment["COMSPEC"] = str(command_processor.path)
            try:
                process = subprocess.Popen(
                    command,
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=False,
                    env=environment,
                    close_fds=True,
                    executable=executable_override,
                    **ManagedProcessTree.launch_options(),
                )
            except OSError as exc:
                raise ProcessSupervisionError(
                    f"Could not launch allowlisted executable: {identity.alias}."
                ) from exc
            tree = ManagedProcessTree.attach(process, budget)
            try:
                readers = _start_pipe_readers(process, capture)
            except Exception:
                tree.terminate(grace_seconds=self.termination_grace_seconds)
                tree.close()
                raise
            timed_out = False
            cancelled = False
            termination_reason: str | None = None
            try:
                while process.poll() is None:
                    if cancellation is not None and cancellation.is_requested:
                        cancellation.mark_propagated("sandbox-process")
                        cancelled = True
                        termination_reason = "cancellation_requested"
                        tree.terminate(grace_seconds=self.termination_grace_seconds)
                        cancellation.acknowledge(
                            "sandbox-process",
                            stage="process-tree-terminated",
                        )
                        break
                    elapsed = time.monotonic() - started
                    if elapsed >= effective_timeout:
                        timed_out = True
                        termination_reason = "wall_clock_timeout"
                        tree.terminate(grace_seconds=self.termination_grace_seconds)
                        break
                    try:
                        process.wait(timeout=self.poll_interval_seconds)
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                if process.poll() is None:
                    tree.terminate(grace_seconds=self.termination_grace_seconds)
                tree.close(grace_seconds=self.termination_grace_seconds)
                _finish_pipe_readers(process, readers)

            duration = max(0.0, time.monotonic() - started)
            output = capture.finalize()
            usage = process_resource_usage(
                budget=budget,
                duration_seconds=duration,
                output=output,
                platform_limits=tree.platform_limits(),
            )
            diagnostic = {
                "executable": identity.safe_metadata(),
                "working_directory": str(cwd),
                "pid": process.pid,
                "effective_timeout_seconds": effective_timeout,
                "environment": environment_diagnostics(environment),
                "process_tree": tree.metadata.safe_metadata(),
                "launch_backend": launch_backend,
                "command_processor": (
                    command_processor.safe_metadata()
                    if command_processor is not None
                    else None
                ),
                "output_events": output.output_events,
                "output_events_truncated": output.output_events_truncated,
                "output_callback_failures": output.callback_failures,
                "isolated_home": True,
                "shell": False,
            }
            return ProcessExecutionResult(
                executable=identity,
                working_directory=cwd,
                pid=process.pid,
                exit_code=process.returncode,
                stdout=output.stdout,
                stderr=output.stderr,
                stdout_truncated=output.stdout_truncated,
                stderr_truncated=output.stderr_truncated,
                duration_seconds=duration,
                timed_out=timed_out,
                cancelled=cancelled,
                termination_reason=termination_reason,
                resource_usage=usage,
                process_tree=tree.metadata,
                safe_diagnostic_metadata=diagnostic,
            )

    def _launch_command(
        self,
        identity: ExecutableIdentity,
        arguments: tuple[str, ...],
    ) -> tuple[list[str] | str, str, ExecutableIdentity | None, str | None]:
        if os.name != "nt" or identity.path.suffix.lower() not in {
            ".bat",
            ".cmd",
        }:
            return identity.command(arguments), "direct", None, None
        if self._command_processor_catalog is None:
            raise ExecutableValidationError(
                "The trusted Windows batch adapter is unavailable."
            )
        _validate_batch_arguments(arguments)
        command_processor = self._command_processor_catalog.resolve(
            "agentbus-command-processor"
        )
        batch_command = " ".join(
            _quote_batch_token(token)
            for token in (str(identity.path), *arguments)
        )
        encoded_command = f'"{batch_command}"'
        # cmd.exe has different quote rules than Python's list2cmdline encoder.
        # Pin CreateProcess.applicationName while passing a fully validated line.
        command_line = (
            f'"{command_processor.path}" /d /v:off /s /c {encoded_command}'
        )
        if len(command_line) > 32_767:
            raise ExecutableValidationError(
                "Windows batch invocation exceeds the bounded command size."
            )
        return (
            command_line,
            "windows-batch-adapter",
            command_processor,
            str(command_processor.path),
        )

    def _cancelled_before_launch(
        self,
        *,
        identity: ExecutableIdentity,
        cwd: Path,
        budget: ToolResourceBudget,
        effective_timeout: float,
    ) -> ProcessExecutionResult:
        output = _empty_output()
        usage = process_resource_usage(
            budget=budget,
            duration_seconds=0.0,
            output=output,
        )
        return ProcessExecutionResult(
            executable=identity,
            working_directory=cwd,
            pid=None,
            exit_code=None,
            stdout="",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
            duration_seconds=0.0,
            timed_out=False,
            cancelled=True,
            termination_reason="cancelled_before_launch",
            resource_usage=usage,
            process_tree=None,
            safe_diagnostic_metadata={
                "executable": identity.safe_metadata(),
                "working_directory": str(cwd),
                "pid": None,
                "effective_timeout_seconds": effective_timeout,
                "process_started": False,
                "shell": False,
            },
        )


def _validate_arguments(arguments: Sequence[str]) -> tuple[str, ...]:
    if isinstance(arguments, (str, bytes)):
        raise ProcessSupervisionError("Process arguments must be a sequence of strings.")
    normalized = tuple(arguments)
    if any(not isinstance(argument, str) for argument in normalized):
        raise ProcessSupervisionError("Process arguments must be strings.")
    if any("\x00" in argument for argument in normalized):
        raise ProcessSupervisionError("Process arguments must not contain NUL bytes.")
    return normalized


def _validate_batch_arguments(arguments: tuple[str, ...]) -> None:
    for argument in arguments:
        if _BATCH_UNSAFE_ARGUMENT.search(argument):
            raise ExecutableValidationError(
                "Windows batch arguments cannot contain command-language metacharacters."
            )


def _quote_batch_token(value: str) -> str:
    if _BATCH_UNSAFE_ARGUMENT.search(value):
        raise ExecutableValidationError(
            "Windows batch command paths and arguments must be quote-safe."
        )
    return f'"{value}"'


def _start_pipe_readers(
    process: subprocess.Popen[bytes],
    capture: BoundedProcessOutput,
) -> tuple[threading.Thread, threading.Thread]:
    if process.stdout is None or process.stderr is None:
        raise ProcessSupervisionError("Managed process pipes were not created.")
    stdout = threading.Thread(
        target=_drain_pipe,
        args=(process.stdout, ToolOutputStream.STDOUT, capture),
        name=f"agentbus-stdout-{process.pid}",
        daemon=True,
    )
    stderr = threading.Thread(
        target=_drain_pipe,
        args=(process.stderr, ToolOutputStream.STDERR, capture),
        name=f"agentbus-stderr-{process.pid}",
        daemon=True,
    )
    stdout.start()
    stderr.start()
    return stdout, stderr


def _drain_pipe(
    pipe: BinaryIO,
    stream: ToolOutputStream,
    capture: BoundedProcessOutput,
) -> None:
    try:
        read_available = getattr(pipe, "read1", pipe.read)
        for chunk in iter(lambda: read_available(16_384), b""):
            capture.consume(stream, chunk)
    except (OSError, ValueError):
        return
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def _finish_pipe_readers(
    process: subprocess.Popen[bytes],
    readers: tuple[threading.Thread, threading.Thread],
) -> None:
    for reader in readers:
        reader.join(timeout=2)
    for pipe, reader in zip((process.stdout, process.stderr), readers, strict=True):
        if reader.is_alive() and pipe is not None:
            try:
                pipe.close()
            except OSError:
                pass
    for reader in readers:
        reader.join(timeout=1)
    if any(reader.is_alive() for reader in readers):
        raise ProcessSupervisionError(
            "Managed process output readers did not terminate after pipe closure."
        )


def _empty_output() -> ProcessOutputSnapshot:
    return ProcessOutputSnapshot(
        stdout="",
        stderr="",
        stdout_bytes=0,
        stderr_bytes=0,
        retained_stdout_bytes=0,
        retained_stderr_bytes=0,
        stdout_truncated=False,
        stderr_truncated=False,
        output_events=0,
        output_events_truncated=False,
        callback_failures=0,
    )
