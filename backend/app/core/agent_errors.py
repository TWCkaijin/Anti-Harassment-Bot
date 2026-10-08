"""Safe error categories shared by agent implementations and HTTP adapters."""


class ModelOutputLimitError(RuntimeError):
    """The provider explicitly ended a completion at its output length limit."""

    def __init__(self):
        super().__init__("Model completion reached its output length limit")


class ApplicableLawsContractMismatchError(RuntimeError):
    """A prepared admin test no longer matches the actual prompt/catalog snapshot."""

    def __init__(self):
        super().__init__("Applicable-law contract fingerprint does not match the prepared test")


class ChatTestContractMismatchError(RuntimeError):
    """A prepared behavior test no longer matches the actual v4 contract."""

    def __init__(self):
        super().__init__("Chat-test contract fingerprint does not match the prepared test")
