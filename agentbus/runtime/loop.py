import json

from agentbus.config import AgentBusConfig
from agentbus.memory.run_log import RunLogger
from agentbus.models.ollama import OllamaModel
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
    ):
        config = config or AgentBusConfig.from_env()
        config = config.with_overrides(
            model_name=model_name,
            workspace_dir=workspace,
            max_steps=None,
        )

        self.config = config
        self.workspace = config.workspace_dir
        self.model = OllamaModel(config=config)
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

        self.logger.log("run_started", {
            "task": user_task,
            "workspace": self.workspace,
        })

        for step in range(1, max_steps + 1):
            self.logger.log("step_started", {
                "step": step,
            })

            prompt = self._build_prompt(user_task, history)

            raw_action = None
            action = None

            try:
                raw_action = self.model.generate_json(prompt)
                action = AgentAction(**raw_action)
            except Exception as e:
                observation = f"Model output error: {str(e)}"
                self.logger.log("model_error", {
                    "step": step,
                    "error": str(e),
                    "raw_action": raw_action,
                })
            else:
                self.logger.log("model_action", {
                    "step": step,
                    "action": raw_action,
                })

                try:
                    observation = self._execute(action)
                except Exception as e:
                    observation = f"Tool error: {str(e)}"

                self.logger.log("tool_observation", {
                    "step": step,
                    "observation": observation,
                })


            history += self._format_history(step, raw_action, observation)
            history = self._trim_history(history)

            if action and action.action == "finish":
                self.logger.log("run_finished", {
                    "summary": action.summary,
                })
                return action.summary

        final = "Stopped because max_steps was reached. Check run logs for details."

        self.logger.log("run_stopped", {
            "reason": "max_steps_reached",
            "history_tail": history[-5000:],
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
