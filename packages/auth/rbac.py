from __future__ import annotations

ROLE_HIERARCHY = ("viewer", "developer", "tenant_admin", "platform_admin")


def role_at_least(role: str, minimum: str) -> bool:
    try:
        return ROLE_HIERARCHY.index(role) >= ROLE_HIERARCHY.index(minimum)
    except ValueError:
        return False


def can_patch_tenant_limits(role: str) -> bool:
    return role_at_least(role, "platform_admin")


def can_approve_tools(role: str) -> bool:
    return role_at_least(role, "platform_admin")


def can_view_tenant_profile(role: str, caller_tenant: str, target_tenant: str) -> bool:
    if caller_tenant == target_tenant:
        return True
    return role_at_least(role, "platform_admin")
