"""Tests for Human-in-the-Loop (HITL) Approval Workflow."""

import time
import pytest

from env.approval import (
    ApprovalRequestStore,
    submit_approval_request,
    approve_request,
    reject_request,
    get_request_status,
    get_pending_requests,
    get_request_history,
    reset_approval_store,
)


@pytest.fixture(autouse=True)
def clean_store():
    reset_approval_store()
    yield
    reset_approval_store()


class TestApprovalRequestStore:
    def test_submit_request_creates_pending_request(self):
        store = ApprovalRequestStore()
        req = store.submit_request(
            action_type="escalate",
            email_id="e1",
            escalate_to="legal_team",
        )
        
        assert req.id is not None
        assert req.action_type == "escalate"
        assert req.email_id == "e1"
        assert req.status == "pending"
        assert req.escalate_to == "legal_team"

    def test_approve_request_changes_status(self):
        store = ApprovalRequestStore()
        req = store.submit_request(action_type="reply", email_id="e2", content="Hello")
        
        response = store.approve_request(req.id, approver_id="admin")
        
        assert response is not None
        assert response.approved is True
        assert response.approver_id == "admin"
        
        updated = store.get_request_status(req.id)
        assert updated.status == "approved"
        assert updated.approver_id == "admin"

    def test_reject_request_changes_status(self):
        store = ApprovalRequestStore()
        req = store.submit_request(action_type="escalate", email_id="e3", escalate_to="chief_of_staff")
        
        response = store.reject_request(req.id, approver_id="manager", comment="Not needed")
        
        assert response is not None
        assert response.approved is False
        assert response.comment == "Not needed"
        
        updated = store.get_request_status(req.id)
        assert updated.status == "rejected"

    def test_get_pending_requests_filters_correctly(self):
        store = ApprovalRequestStore()
        req1 = store.submit_request(action_type="escalate", email_id="e1", escalate_to="legal")
        req2 = store.submit_request(action_type="reply", email_id="e2", content="Hi")
        
        store.approve_request(req1.id, approver_id="admin")
        
        pending = store.get_pending_requests()
        
        assert len(pending) == 1
        assert pending[0].id == req2.id

    def test_auto_reject_expired(self):
        store = ApprovalRequestStore(timeout_seconds=1)
        req = store.submit_request(action_type="escalate", email_id="e1", escalate_to="legal")
        
        time.sleep(1.1)
        
        rejected = store.auto_reject_expired()
        
        assert req.id in rejected
        updated = store.get_request_status(req.id)
        assert updated.status == "expired"

    def test_cannot_approve_already_approved(self):
        store = ApprovalRequestStore()
        req = store.submit_request(action_type="reply", email_id="e1", content="Test")
        
        store.approve_request(req.id, approver_id="admin")
        second_response = store.approve_request(req.id, approver_id="admin2")
        
        assert second_response is None

    def test_cannot_reject_non_pending(self):
        store = ApprovalRequestStore()
        req = store.submit_request(action_type="reply", email_id="e1", content="Test")
        
        store.reject_request(req.id, approver_id="admin")
        second_response = store.reject_request(req.id, approver_id="admin2")
        
        assert second_response is None


class TestApprovalModuleFunctions:
    def test_submit_approval_request_function(self):
        req = submit_approval_request(
            action_type="escalate",
            email_id="e5",
            escalate_to="legal_team",
        )
        
        assert req.id is not None
        assert req.status == "pending"

    def test_get_request_status_function(self):
        req = submit_approval_request(action_type="reply", email_id="e6", content="Reply")
        
        status = get_request_status(req.id)
        
        assert status is not None
        assert status.id == req.id

    def test_get_pending_requests_function(self):
        submit_approval_request(action_type="escalate", email_id="e7", escalate_to="legal")
        
        pending = get_pending_requests()
        
        assert len(pending) >= 1

    def test_approve_and_get_history(self):
        req = submit_approval_request(action_type="reply", email_id="e8", content="Hello")
        approve_request(req.id, approver_id="supervisor")
        
        history = get_request_history(limit=10)
        
        assert len(history) >= 1
        found = any(r.id == req.id for r in history)
        assert found is True


class TestApprovalTimeout:
    def test_default_timeout_is_300_seconds(self):
        store = ApprovalRequestStore()
        
        assert store._timeout_seconds == 300

    def test_custom_timeout(self):
        store = ApprovalRequestStore(timeout_seconds=60)
        
        assert store._timeout_seconds == 60