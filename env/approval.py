"""Human-in-the-Loop (HITL) Approval Workflow for Executive Email Copilot.

This module provides approval workflow functionality where certain actions
(escalate, external reply) require human approval before execution.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from .models import ApprovalRequest, ApprovalResponse, ApprovalStatus


# Default approval timeout in seconds (5 minutes)
DEFAULT_APPROVAL_TIMEOUT_SECONDS = 300


class ApprovalRequestStore:
    """In-memory store for approval requests."""

    def __init__(self, timeout_seconds: float = DEFAULT_APPROVAL_TIMEOUT_SECONDS):
        self._requests: dict[str, ApprovalRequest] = {}
        self._responses: dict[str, ApprovalResponse] = {}
        self._timeout_seconds = timeout_seconds

    def submit_request(
        self,
        action_type: str,
        email_id: str,
        content: str | None = None,
        escalate_to: str | None = None,
    ) -> ApprovalRequest:
        """Submit a new approval request."""
        request_id = str(uuid.uuid4())[:8]
        current_time = time.time()
        
        request = ApprovalRequest(
            id=request_id,
            action_type=action_type,  # type: ignore
            email_id=email_id,
            content=content,
            escalate_to=escalate_to,
            requested_at=current_time,
            status="pending",
            approver_id=None,
            expires_at=current_time + self._timeout_seconds,
        )
        
        self._requests[request_id] = request
        return request

    def approve_request(
        self,
        request_id: str,
        approver_id: str,
        comment: str | None = None,
    ) -> ApprovalResponse | None:
        """Approve a pending approval request."""
        request = self._requests.get(request_id)
        if request is None:
            return None
        
        if request.status != "pending":
            return None
        
        # Update request status
        request.status = "approved"
        request.approver_id = approver_id
        
        # Create response
        response = ApprovalResponse(
            request_id=request_id,
            approved=True,
            approver_id=approver_id,
            timestamp=time.time(),
            comment=comment,
        )
        
        self._responses[request_id] = response
        return response

    def reject_request(
        self,
        request_id: str,
        approver_id: str,
        comment: str | None = None,
    ) -> ApprovalResponse | None:
        """Reject a pending approval request."""
        request = self._requests.get(request_id)
        if request is None:
            return None
        
        if request.status != "pending":
            return None
        
        # Update request status
        request.status = "rejected"
        request.approver_id = approver_id
        
        # Create response
        response = ApprovalResponse(
            request_id=request_id,
            approved=False,
            approver_id=approver_id,
            timestamp=time.time(),
            comment=comment,
        )
        
        self._responses[request_id] = response
        return response

    def get_request_status(self, request_id: str) -> ApprovalRequest | None:
        """Get the current status of an approval request."""
        return self._requests.get(request_id)

    def get_pending_requests(self) -> list[ApprovalRequest]:
        """Get all pending approval requests."""
        return [req for req in self._requests.values() if req.status == "pending"]

    def get_request_history(self, limit: int = 50) -> list[ApprovalRequest]:
        """Get approval request history (most recent first)."""
        sorted_requests = sorted(
            self._requests.values(),
            key=lambda r: r.requested_at,
            reverse=True,
        )
        return sorted_requests[:limit]

    def auto_reject_expired(self) -> list[str]:
        """Reject all expired pending requests. Returns list of rejected request IDs."""
        current_time = time.time()
        rejected_ids = []
        
        for request_id, request in self._requests.items():
            if request.status == "pending" and request.expires_at <= current_time:
                request.status = "expired"
                rejected_ids.append(request_id)
                
                # Create rejection response
                response = ApprovalResponse(
                    request_id=request_id,
                    approved=False,
                    approver_id="system",
                    timestamp=current_time,
                    comment="Auto-rejected: approval timeout expired",
                )
                self._responses[request_id] = response
        
        return rejected_ids

    def check_approval_required(self, action_type: str, email_id: str) -> bool:
        """Check if an action requires approval."""
        # Escalate and reply actions require approval
        return action_type in {"escalate", "reply"}

    def get_approval_status(self, request_id: str) -> tuple[bool | None, ApprovalStatus | None]:
        """Get approval status: (is_approved, status)."""
        request = self._requests.get(request_id)
        if request is None:
            return None, None
        
        if request.status == "approved":
            return True, request.status
        elif request.status in ("rejected", "expired"):
            return False, request.status
        else:
            return None, request.status


# Global approval store instance
_approval_store: ApprovalRequestStore | None = None


def get_approval_store() -> ApprovalRequestStore:
    """Get the global approval store instance."""
    global _approval_store
    if _approval_store is None:
        _approval_store = ApprovalRequestStore()
    return _approval_store


def submit_approval_request(
    action_type: str,
    email_id: str,
    content: str | None = None,
    escalate_to: str | None = None,
) -> ApprovalRequest:
    """Submit a new approval request."""
    store = get_approval_store()
    return store.submit_request(action_type, email_id, content, escalate_to)


def approve_request(
    request_id: str,
    approver_id: str,
    comment: str | None = None,
) -> ApprovalResponse | None:
    """Approve a pending request."""
    store = get_approval_store()
    return store.approve_request(request_id, approver_id, comment)


def reject_request(
    request_id: str,
    approver_id: str,
    comment: str | None = None,
) -> ApprovalResponse | None:
    """Reject a pending request."""
    store = get_approval_store()
    return store.reject_request(request_id, approver_id, comment)


def get_request_status(request_id: str) -> ApprovalRequest | None:
    """Get the status of an approval request."""
    store = get_approval_store()
    return store.get_request_status(request_id)


def get_pending_requests() -> list[ApprovalRequest]:
    """Get all pending approval requests."""
    store = get_approval_store()
    # Auto-reject expired requests first
    store.auto_reject_expired()
    return store.get_pending_requests()


def get_request_history(limit: int = 50) -> list[ApprovalRequest]:
    """Get approval request history."""
    store = get_approval_store()
    return store.get_request_history(limit)


def reset_approval_store() -> None:
    """Reset the approval store (for testing)."""
    global _approval_store
    _approval_store = None