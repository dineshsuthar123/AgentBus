class WorktreeError(RuntimeError):
    """Base error for safe Syndra-owned Git worktree operations."""


class WorktreeAlreadyExistsError(WorktreeError):
    pass


class WorktreeCommandTimeout(WorktreeError):
    def __init__(self, operation: str, *, tree_stopped: bool) -> None:
        super().__init__(f"Git worktree operation '{operation}' timed out.")
        self.tree_stopped = tree_stopped


class WorktreeOwnershipError(WorktreeError):
    pass


class WorktreeRepositoryMismatchError(WorktreeError):
    pass


class WorktreeDirtyError(WorktreeError):
    pass


class WorktreeRemovalUnsafeError(WorktreeError):
    pass
