"""Single loading path for source-controlled first-party capability packages."""

import importlib
import pkgutil


def load_native(registry, contracts) -> None:
    for contract in contracts:
        registry.register(contract)


def load_first_party_capabilities(registry) -> None:
    """Load only modules bundled beneath ``app.plugins``.

    Each package exports ``get_capabilities()``. No entry points, filesystem
    paths, uploaded modules, or third-party imports are accepted.
    """
    import app.plugins as plugins

    for module_info in pkgutil.iter_modules(plugins.__path__, plugins.__name__ + "."):
        module = importlib.import_module(module_info.name)
        factory = getattr(module, "get_capabilities", None)
        if factory is None:
            continue
        load_native(registry, factory())
