class CapabilityAccessDenied(PermissionError):
    pass


# Host-owned grant for Operator. Application submission is intentionally
# absent; adding it later must include an explicit approval design.
OPERATOR_PLATFORM_PERMISSIONS = frozenset({
    "files.read", "files.write", "web.read", "browser.read", "browser.write",
    "application.prepare",
    "profile.propose",
})


def require_permissions(required, granted) -> None:
    missing = set(required) - set(granted or ())
    if missing:
        raise CapabilityAccessDenied(f"missing permission: {sorted(missing)[0]}")
