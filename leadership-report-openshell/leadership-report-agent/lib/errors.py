"""Shared error types and exit codes for the leadership report agent."""

EXIT_OK = 0 # everything fine
EXIT_USAGE = 1 # setup failure
EXIT_PERMANENT = 2 # trying again wont work
EXIT_TRANSIENT = 3 # trying again might work


class AgentError(Exception): # custom error for exceptions: provides a message and retriable/not retryable
    """Base exception for agent pipeline failures."""

    def __init__(self, message: str, retriable: bool = False):
        super().__init__(message)
        self.retriable = retriable


class AuthError(AgentError): # copy agent error, use the same rules, just seperates exceptions from authentication failures
    """Raised when Google OAuth2 authentication fails."""
    pass
