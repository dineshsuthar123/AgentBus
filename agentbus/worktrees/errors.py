class WorktreeError(RuntimeError):
    """Base error for safe Syndra-owned Git worktree operations."""


class WorktreeAlreadyExistsError(WorktreeError):
    pass


class WorktreeOwnershipError(WorktreeError):
    pass


class WorktreeRepositoryMismatchError(WorktreeError):
    pass


class WorktreeDirtyError(WorktreeError):
    pass


class WorktreeRemovalUnsafeError(WorktreeError):
    pass
