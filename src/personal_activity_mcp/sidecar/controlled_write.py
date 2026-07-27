"""Shared control flow for one external write operation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from personal_activity_mcp.common import ToolContractError
from personal_activity_mcp.sidecar.write_control import (
    AuditWrite,
    ReservationDecision,
    SidecarStateConflict,
    WriteControl,
)


@dataclass(frozen=True)
class ControlledWrite:
    control: WriteControl
    idempotency_key: str
    operation: str
    request_hash: str
    confirmed_by_user: bool
    resource_name: str

    def reserve(self) -> ReservationDecision:
        decision = self.control.reserve_operation(
            idempotency_key=self.idempotency_key,
            operation=self.operation,
            request_hash=self.request_hash,
            confirmed_by_user=self.confirmed_by_user,
        )
        self.control.audit_non_executable_reservation(
            decision,
            operation=self.operation,
            request_hash=self.request_hash,
            confirmed_by_user=self.confirmed_by_user,
        )
        if decision.status == "conflict":
            raise ValueError("idempotency_key conflicts with different request")
        if decision.status == "in_progress":
            raise ToolContractError(
                code="OPERATION_IN_PROGRESS",
                message="The same write operation is already in progress",
                retryable=True,
                public_message="The same write operation is already in progress",
            )
        if decision.status == "external_state_unknown":
            raise self.external_state_unknown_error()
        return decision

    def record_blocked(self, *, target_item_id: str | None, error_code: str) -> str:
        return self.control.record_blocked(
            operation=self.operation,
            target_item_id=target_item_id,
            request_hash=self.request_hash,
            error_code=error_code,
            confirmed_by_user=self.confirmed_by_user,
        )

    def backend_failed(self, error: Exception) -> None:
        if getattr(error, "external_state_changed", None) is False:
            self._finalize_failure(status="failed", error_code="BACKEND_FAILURE")
            raise error
        self._finalize_failure(
            status="external_state_unknown",
            error_code="EXTERNAL_STATE_UNKNOWN",
        )
        raise self.external_state_unknown_error() from error

    def preflight_failed(self, error: Exception) -> None:
        self._finalize_failure(status="failed", error_code="BACKEND_FAILURE")
        raise error

    def unverified_result(self) -> None:
        self._finalize_failure(
            status="external_state_unknown",
            error_code="EXTERNAL_STATE_UNKNOWN",
        )
        raise self.external_state_unknown_error()

    def external_state_changed(self) -> None:
        self._finalize_failure(
            status="failed",
            error_code="EXTERNAL_STATE_CHANGED",
        )
        message = f"The {self.resource_name} changed after it was read"
        raise ToolContractError(
            code="EXTERNAL_STATE_CHANGED",
            message=message,
            retryable=False,
            public_message=message,
        )

    def external_state_unknown_error(self) -> ToolContractError:
        return ToolContractError(
            code="EXTERNAL_STATE_UNKNOWN",
            message="The external write result could not be verified",
            retryable=False,
            public_message=(
                "The external write result is unknown and will not be retried automatically"
            ),
        )

    def finalization_failed(
        self,
        error: Exception,
        *,
        external_write_attempted: bool,
    ) -> None:
        if isinstance(error, SidecarStateConflict):
            if external_write_attempted:
                self.unverified_result()
            self.external_state_changed()
        if external_write_attempted:
            raise self.external_state_unknown_error() from error
        raise ToolContractError(
            code="LOCAL_PERSISTENCE_FAILURE",
            message="The local write result could not be persisted",
            retryable=True,
            public_message="The local write result could not be persisted; retry is safe",
        ) from error

    def _finalize_failure(
        self,
        *,
        status: Literal["failed", "external_state_unknown"],
        error_code: str,
    ) -> None:
        self.control.finalize_failure(
            idempotency_key=self.idempotency_key,
            operation=self.operation,
            status=status,
            error_code=error_code,
            audit=AuditWrite(
                request_hash=self.request_hash,
                result_status=status,
                error_code=error_code,
                confirmed_by_user=self.confirmed_by_user,
            ),
        )


def request_hash(payload: dict[str, object]) -> str:
    """Return a stable SHA-256 hash for a normalized request payload."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()
