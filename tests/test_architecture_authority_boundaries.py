import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_guard_repository_never_mutates_device_authority_fields() -> None:
    """Guard configuration may reference a Device, but cannot approve or mount it."""
    path = ROOT / "eidolon_data/repositories/guard_bindings.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {
        "owner_id",
        "bound_companion_id",
        "status",
        "approved_at",
        "approved_by",
        "revoked_at",
        "interaction_mode",
    }
    violations = []
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id in {"device", "old_device"}
                and target.attr in forbidden
            ):
                violations.append(
                    f"{target.value.id}.{target.attr} at line {target.lineno}"
                )
    assert violations == []


def test_guard_claim_does_not_auto_create_companion_or_workspace() -> None:
    path = ROOT / "eidolon_data/repositories/guard_bindings.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    claim = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "claim"
    )
    called = {
        node.func.id
        for node in ast.walk(claim)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert called.isdisjoint({"_new_guard_companion", "_ensure_guard_workspace"})


def test_global_audit_contract_is_not_owned_by_eidolon_data() -> None:
    assert not (ROOT / "eidolon_data/audit/contracts.py").exists()
    for relative_path in (
        "eidolon_data/audit/outbox.py",
        "eidolon_data/audit/dispatcher.py",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "from eidolon_sdk.biz.audit import" in source
    assert not (ROOT / "eidolon_data/audit/index.py").exists()
    assert not (ROOT / "eidolon_data/audit/nats.py").exists()
    assert not (ROOT / "eidolon_data/audit/cli.py").exists()
    assert "nats-py" not in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
