class ProviderAuthError(Exception):
    """A provider (geocoder or LLM) rejected our credentials.

    A configuration error, not a bad address: the run stops instead of flagging
    every row for review.
    """
