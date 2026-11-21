"""Structured JSON Logger for Google Cloud Logging."""

import datetime
import json
import logging
import sys
from typing import Any, Optional


class CloudLoggingJsonFormatter(logging.Formatter):
    """Custom formatter producing Google Cloud Logging-compatible JSON records."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "severity": self._map_severity(record.levelname),
            "message": record.getMessage(),
            "component": getattr(record, "component", record.name),
            "logger": record.name,
        }

        # Contextual Cloud Logging attributes
        if hasattr(record, "session_id") and record.session_id:
            log_entry["session_id"] = record.session_id
        if hasattr(record, "request_id") and record.request_id:
            log_entry["request_id"] = record.request_id
        if hasattr(record, "event_type") and record.event_type:
            log_entry["event_type"] = record.event_type
        if hasattr(record, "extra_payload") and isinstance(record.extra_payload, dict):
            log_entry["payload"] = record.extra_payload

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)

    @staticmethod
    def _map_severity(level_name: str) -> str:
        mapping = {
            "DEBUG": "DEBUG",
            "INFO": "INFO",
            "WARNING": "WARNING",
            "ERROR": "ERROR",
            "CRITICAL": "CRITICAL",
        }
        return mapping.get(level_name.upper(), "INFO")


def setup_telemetry(log_level: str = "INFO") -> None:
    """Configures the root and application loggers to output Cloud Logging JSON."""
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level.upper())

    # Remove existing handlers to prevent duplicate lines
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(CloudLoggingJsonFormatter())
    root_logger.addHandler(handler)


class StructuredLogger:
    """Convenient wrapper for emitting structured Cloud Logging events with metadata."""

    def __init__(self, component: str):
        self.component = component
        self._logger = logging.getLogger(component)

    def log_event(
        self,
        severity: str,
        message: str,
        event_type: str,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        **extra_payload: Any,
    ) -> None:
        """Emits a structured JSON log entry for key business events."""
        level = getattr(logging, severity.upper(), logging.INFO)
        extra = {
            "component": self.component,
            "session_id": session_id,
            "request_id": request_id,
            "event_type": event_type,
            "extra_payload": extra_payload,
        }
        self._logger.log(level, message, extra=extra)

    def info(self, message: str, **kwargs: Any) -> None:
        self.log_event("INFO", message, event_type=kwargs.pop("event_type", "info"), **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        self.log_event("WARNING", message, event_type=kwargs.pop("event_type", "warning"), **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        self.log_event("ERROR", message, event_type=kwargs.pop("event_type", "error"), **kwargs)

    def debug(self, message: str, **kwargs: Any) -> None:
        self.log_event("DEBUG", message, event_type=kwargs.pop("event_type", "debug"), **kwargs)


def get_logger(component: str) -> StructuredLogger:
    """Factory function for StructuredLogger."""
    return StructuredLogger(component=component)
