"""Typed exceptions for the reminders service."""


class RemindersError(Exception):
    """Base class for all domain errors."""


class NotFound(RemindersError):
    """Reminder does not exist."""


class InvalidTransition(RemindersError):
    """State transition is not allowed from the current state."""


class InvalidTimezone(RemindersError):
    """IANA timezone name is not recognized."""


class InvalidInput(RemindersError):
    """Request payload is malformed."""


class AlreadyTerminal(InvalidTransition):
    """Reminder is in a terminal state and cannot be modified."""
