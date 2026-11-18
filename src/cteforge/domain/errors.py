"""Error hierarchy. Raise these, never bare strings or generic exceptions."""


class CteforgeError(Exception):
    """Base class for all cteforge errors."""


class VariableValueError(CteforgeError):
    """A variable value does not match its type's strict input format."""


class AnnotationError(CteforgeError):
    """Annotations don't match the query (unknown literal IDs, wrong shapes, types)."""


class RenderError(CteforgeError):
    """A template could not be rendered (missing values, leftover placeholders)."""


class CodecError(CteforgeError):
    """A definition or annotation file is malformed or has an unsupported schema."""


class SqlParseError(CteforgeError):
    """SQL could not be parsed in the given dialect."""


class WarehouseError(CteforgeError):
    """A warehouse call failed. Messages never contain credentials."""


class StoreError(CteforgeError):
    """A persistence operation failed (duplicate id, missing row, bad state)."""


class StaleVersionError(StoreError):
    """A write was based on a version that is no longer the latest."""


class AuditError(CteforgeError):
    """The audit log could not be written or read."""


class NotFoundError(CteforgeError):
    """A definition, join, proposal or user does not exist."""


class PermissionDeniedError(CteforgeError):
    """The actor's role does not allow this action (ADR 0018)."""


class PolicyError(CteforgeError):
    """Refused by policy: strict mode, draft definition, non-read-only SQL, ..."""


class EmbeddingError(CteforgeError):
    """The embedding model could not be loaded or used (meaning search; ADR 0023)."""
