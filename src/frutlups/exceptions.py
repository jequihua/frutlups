"""Package-specific exceptions."""


class FrutlupsError(Exception):
    """Base class for package errors."""


class ProjectNotFoundError(FrutlupsError):
    """Raised when a project root cannot be discovered."""
