class RepositoryIntelligenceError(RuntimeError):
    """Base error for local repository intelligence operations."""


class IndexSchemaError(RepositoryIntelligenceError):
    """Raised when an index schema is incompatible or invalid."""


class IndexCorruptedError(RepositoryIntelligenceError):
    """Raised when persisted index integrity checks fail."""


class IndexBusyError(RepositoryIntelligenceError):
    """Raised when a fenced index operation is already active."""


class IndexUnavailableError(RepositoryIntelligenceError):
    """Raised when an optional repository index cannot be used."""


class IndexPersistenceError(RepositoryIntelligenceError):
    """Raised when portable index records cannot be stored safely."""


class ParserCompatibilityError(RepositoryIntelligenceError):
    """Raised when parser ownership or versions are incompatible."""


class ParserUnavailableError(RepositoryIntelligenceError):
    """Raised when no safe local parser owns a requested language."""


class UnsafeRepositoryPathError(RepositoryIntelligenceError):
    """Raised when a repository-relative path is unsafe."""


class QueryLimitError(RepositoryIntelligenceError):
    """Raised when a graph or retrieval query exceeds a hard bound."""


class RepositoryQueryError(RepositoryIntelligenceError):
    """Raised when a bounded repository query cannot be resolved safely."""
