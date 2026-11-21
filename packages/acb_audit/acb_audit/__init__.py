"""Append-only audit log. Every agent decision + human override lands here.
The Annealer (Phase 4) mines this log for recurring intervention patterns.
"""
from acb_audit.log import AuditEvent, drain, record, refuse_tenantless

__all__ = ["AuditEvent", "drain", "record", "refuse_tenantless"]
