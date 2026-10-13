"""AST checker for bounded-context ownership and the narrow TF-37 seam."""

import ast
import os
import sys

SELECTED_CONSUMER = os.path.join(
    "backend", "apps", "contracts", "tests", "test_files_finance_boundary.py"
)
ALLOWED_SUPPORT_IMPORTS = {
    ("apps.files.tests.factories", "make_file"),
    ("apps.finance.tests.factories", "make_posted_transaction"),
}
SUPPORT_MODULES = {module for module, _ in ALLOWED_SUPPORT_IMPORTS}
USER_FACTORY_MODULE = "apps.users.tests.iam_factories"
USER_FACTORY_SYMBOL = "make_contracts_boundary_actor"
USER_FACTORY_CONSUMERS = {
    os.path.join("backend", "apps", "contracts", "tests", "test_files_finance_boundary.py"),
    os.path.join("backend", "apps", "contracts", "tests", "test_closure_service.py"),
}


def _selected(path: str) -> bool:
    return os.path.normpath(path) == SELECTED_CONSUMER


def _user_factory_allowed(node: ast.ImportFrom, path: str) -> bool:
    return (
        os.path.normpath(path) in USER_FACTORY_CONSUMERS
        and node.module == USER_FACTORY_MODULE
        and len(node.names) == 1
        and node.names[0].name == USER_FACTORY_SYMBOL
        and node.names[0].asname is None
    )


def _allowed_support_import(node: ast.ImportFrom, path: str) -> bool:
    if not _selected(path) or node.module is None:
        return False
    expected = dict(ALLOWED_SUPPORT_IMPORTS).get(node.module)
    return (
        expected is not None
        and len(node.names) == 1
        and node.names[0].name == expected
        and node.names[0].asname is None
    )


def _dynamic_support_reference(node: ast.Call, dynamic_names: set[str]) -> bool:
    if isinstance(node.func, ast.Name) and node.func.id in dynamic_names:
        return True
    return isinstance(node.func, ast.Attribute) and node.func.attr in {"import_module", "__import__"}


def check_ddd_boundary_violations():
    """[ИСПРАВЛЕНО] Автоматический AST-линтер архитектурных границ Bounded Contexts."""
    base_apps_dir = "backend/apps"
    violations = []
    for root, _, files in os.walk(base_apps_dir):
        for file in files:
            if not file.endswith(".py") or "migrations" in root:
                continue
            file_path = os.path.join(root, file)
            relative_path = os.path.relpath(file_path, base_apps_dir)
            path_parts = relative_path.split(os.sep)
            if not path_parts or path_parts[0] in ["common", "config"]:
                continue
            current_domain = path_parts[0]
            with open(file_path, "r", encoding="utf-8") as f:
                try:
                    node = ast.parse(f.read(), filename=file_path)
                except SyntaxError:
                    continue
            selected = _selected(file_path)
            dynamic_names = {"import_module", "__import__"}
            for statement in node.body:
                if isinstance(statement, ast.ImportFrom) and statement.module == "importlib":
                    for alias in statement.names:
                        if alias.name == "import_module":
                            dynamic_names.add(alias.asname or alias.name)
            for subnode in ast.walk(node):
                if isinstance(subnode, (ast.ImportFrom, ast.Import)):
                    imported_module = None
                    if isinstance(subnode, ast.ImportFrom) and subnode.module:
                        imported_module = subnode.module
                    elif isinstance(subnode, ast.Import) and subnode.names:
                        imported_module = subnode.names[0].name
                    if not imported_module or not imported_module.startswith("apps."):
                        continue
                    if imported_module == USER_FACTORY_MODULE:
                        if isinstance(subnode, ast.ImportFrom):
                            names = {alias.name for alias in subnode.names}
                            if USER_FACTORY_SYMBOL not in names:
                                if "*" not in names:
                                    continue
                            elif _user_factory_allowed(subnode, file_path):
                                continue
                            violations.append(
                                f"TF-37 users factory import forbidden: {file_path}:{subnode.lineno}: {imported_module}"
                            )
                            continue
                    is_support = imported_module in SUPPORT_MODULES or any(
                        imported_module.startswith(f"{module}.") for module in SUPPORT_MODULES
                    )
                    if is_support:
                        if (
                            selected
                            and isinstance(subnode, ast.ImportFrom)
                            and _allowed_support_import(subnode, file_path)
                        ):
                            continue
                        violations.append(
                            f"TF-37 support import forbidden: {file_path}:{subnode.lineno}: {imported_module}"
                        )
                        continue
                    import_parts = imported_module.split(".")
                    if len(import_parts) <= 1:
                        continue
                    target_domain = import_parts[1]
                    if target_domain == current_domain:
                        continue
                    if target_domain == "common":
                        continue
                    if "models" in import_parts or "services" in import_parts:
                        violations.append(
                            f"Файл '{file_path}' (Домен: {current_domain}) напрямую импортирует "
                            f"внутренности чужого домена '{target_domain}' ({imported_module})."
                        )
                elif isinstance(subnode, ast.Call):
                    if _dynamic_support_reference(subnode, dynamic_names):
                        if not subnode.args or not isinstance(subnode.args[0], ast.Constant):
                            if os.path.normpath(file_path) in USER_FACTORY_CONSUMERS or selected:
                                violations.append(
                                    f"TF-37 support dynamic import unresolved: {file_path}:{subnode.lineno}"
                                )
                        elif (
                            any(module in str(subnode.args[0].value) for module in SUPPORT_MODULES)
                            or USER_FACTORY_MODULE in str(subnode.args[0].value)
                        ):
                            violations.append(f"TF-37 support dynamic import forbidden: {file_path}:{subnode.lineno}")
            if selected:
                for statement in node.body:
                    if isinstance(statement, ast.Assign) and any(
                        isinstance(target, ast.Name) and target.id == "__all__" for target in statement.targets
                    ):
                        violations.append(f"TF-37 support re-export forbidden: {file_path}:{statement.lineno}")
    if violations:
        print("🚷 ARCHITECTURAL BOUNDARY VIOLATIONS DETECTED (EVENTUAL SPAGHETTI BLOCKER):")
        for violation in violations:
            print(f"  🚨 {violation}")
        print("\nМердж заблокирован по правилу Bounded-Context Ownership Rules [ADR 0030].")
        print("Используйте слои 'apps/common/contracts/' (Internal SDK) или Transactional Outbox события.")
        sys.exit(1)
    print("✅ Свод правил DDD полностью соблюден. Спагетти-связи отсутствуют.")
    sys.exit(0)


if __name__ == "__main__":
    check_ddd_boundary_violations()
