"""Safe error categories shared by agent implementations and HTTP adapters."""


class ModelOutputLimitError(RuntimeError):
    """The provider explicitly ended a completion at its output length limit."""

    def __init__(self):
        super().__init__("Model completion reached its output length limit")
