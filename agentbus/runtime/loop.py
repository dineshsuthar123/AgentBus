import inspect
import json

from agentbus.config import AgentBusConfig
from agentbus.memory.run_log import RunLogger
from agentbus.models.errors import ModelOutputError, ModelProviderError
from agentbus.models.router import ModelRouter
from agentbus.models.types import ModelRole
from agentbus.runtime.prompts import SYSTEM_PROMPT
from agentbus.runtime.schemas import AgentAction
from agentbus.tools.command import CommandTools
from agentbus.tools.filesystem import FileSystemTools
from agentbus.tools.git_tools import GitTools


class AgentLoop:
    def __init__(
        self,
        workspace: str | None = None,
        model_name: str | None = None,
        max_history_chars: int | None = None,
        config: AgentBusConfig | None = None,
        model=None,
        model_router: ModelRouter | None = None,
    ):
        config = config or AgentBusConfig.from_env()
        config = config.with_overrides(
            model_name=model_name,
            workspace_dir=workspace,
            max_steps=None,
        )

        self.config = config
        self.workspace = config.workspace_dir
        self.model_router = model_router
        if model is not None:
            self.model = model
        else:
            self.model_router = model_router or ModelRouter(config)
            self.model = self.model_router.for_role(ModelRole.CODER)
        self.fs = FileSystemTools(workspace=self.workspace)
        self.cmd = CommandTools(
            workspace=self.workspace,
            timeout_seconds=config.command_timeout_seconds,
        )
        self.git = GitTools(workspace=self.workspace)
        self.logger = RunLogger(log_dir=config.runs_dir)
        self.max_history_chars = max_history_chars or config.max_history_chars

    def run(self, user_task: str, max_steps: int | None = None) -> str:
        history = ""
        max_steps = max_steps or self.config.max_steps

        self.logger.log(
            "run_started",
            {
                "task_chars": len(user_task),
                "workspace": self.workspace,
            },
        )

        for step in range(1, max_steps + 1):
            self.logger.log("step_started", {
                "step": step,
            })

            prompt = self._build_prompt(user_task, history)

            raw_action = None
            action = None

            try:
                method = self.model.generate_json
                if _accepts_schema(method):
                    raw_action = method(prompt, schema=AgentAction)
                else:
                    raw_action = method(prompt)
                action = AgentAction(**raw_action)
            except ModelOutputError as error:
                observation = f"Model output error: {error.safe_message}"
                self.logger.log(
                    "model_error",
                    {"step": step, **error.safe_metadata()},
                )
            except ModelProviderError as error:
                self.logger.log(
                    "model_error",
                    {"step": step, **error.safe_metadata()},
                )
                raise
            except Exception as e:
                observation = f"Model output error: {str(e)}"
                self.logger.log(
                    "model_error",
                    {
                        "step": step,
                        "error_type": type(e).__name__,
                        "error_chars": len(str(e)),
                    },
                )
            else:
                self.logger.log(
                    "model_action",
                    {"step": step, **_action_log_metadata(action)},
                )

                try:
                    observation = self._execute(action)
                except Exception as e:
                    observation = f"Tool error: {str(e)}"

                self.logger.log(
                    "tool_observation",
                    {
                        "step": step,
                        "observation_chars": len(observation),
                    },
                )

            history += self._format_history(step, raw_action, observation)
            history = self._trim_history(history)

            if action and action.action == "finish":
                self.logger.log("run_finished", {
                    "summary_chars": len(action.summary or ""),
                })
                return action.summary

        final = "Stopped because max_steps was reached. Check run logs for details."

        self.logger.log("run_stopped", {
            "reason": "max_steps_reached",
            "history_chars": len(history),
        })

        return final

    def _build_prompt(self, user_task: str, history: str) -> str:
        return f"""
{SYSTEM_PROMPT}

User task:
{user_task}

Previous observations:
{history if history.strip() else "No previous observations."}

Return the next JSON action.
"""

    def _execute(self, action: AgentAction) -> str:
        if action.action == "list_files":
            return self.fs.list_files()

        if action.action == "read_file":
            return self.fs.read_file(action.path)

        if action.action == "write_file":
            return self.fs.write_file(action.path, action.content)

        if action.action == "run_command":
            return self.cmd.run_command(action.command)

        if action.action == "git_diff":
            return self.git.git_diff()

        if action.action == "finish":
            return f"Finished: {action.summary}"

        return f"Unknown action: {action.action}"

    def _format_history(self, step: int, raw_action, observation: str) -> str:
        return (
            f"\n--- Step {step} ---\n"
            f"Action:\n{json.dumps(raw_action, indent=2, ensure_ascii=False)}\n"
            f"Observation:\n{observation}\n"
        )

    def _trim_history(self, history: str) -> str:
        if len(history) <= self.max_history_chars:
            return history

        return history[-self.max_history_chars:]


def _accepts_schema(method) -> bool:
    try:
        parameters = inspect.signature(method).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        or parameter.name == "schema"
        for parameter in parameters
    )


def _action_log_metadata(action: AgentAction) -> dict:
    metadata = {"action": action.action}
    if action.path:
        metadata["path"] = action.path
    if action.content is not None:
        metadata["content_chars"] = len(action.content)
    if action.command:
        metadata["command"] = action.command[0]
        metadata["command_arg_count"] = len(action.command) - 1
    if action.summary:
        metadata["summary_chars"] = len(action.summary)
    return metadata
