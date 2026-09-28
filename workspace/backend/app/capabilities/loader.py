"""Safe first-party loader. Arbitrary third-party imports are intentionally absent."""


def load_native(registry, contracts) -> None:
    for contract in contracts:
        registry.register(contract)
