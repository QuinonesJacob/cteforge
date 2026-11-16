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
