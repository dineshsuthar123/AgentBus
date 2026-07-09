from typing import Literal, Optional
from pydantic import BaseModel, model_validator


class AgentAction(BaseModel):
    action: Literal[
        "list_files",
        "read_file",
        "write_file",
        "run_command",
        "git_diff",
        "finish",
    ]

    path: Optional[str] = None
    content: Optional[str] = None
    command: Optional[list[str]] = None
    summary: Optional[str] = None

    @model_validator(mode="after")
    def validate_required_fields(self):
        if self.action == "read_file" and not self.path:
            raise ValueError("read_file requires path")

        if self.action == "write_file":
            if not self.path:
                raise ValueError("write_file requires path")
            if self.content is None:
                raise ValueError("write_file requires content")

        if self.action == "run_command":
            if not self.command:
                raise ValueError("run_command requires command as list[str]")

        if self.action == "finish" and not self.summary:
            raise ValueError("finish requires summary")

        return self