from .contract import CapabilityContract
from .manifest import validate


class CapabilityRegistry:
    def __init__(self):
        self._items: dict[str, CapabilityContract] = {}

    def register(self, contract: CapabilityContract) -> CapabilityContract:
        validate(contract)
        if contract.id in self._items:
            raise ValueError(f"Duplicate capability: {contract.id}")
        self._items[contract.id] = contract
        return contract

    def get(self, capability_id: str) -> CapabilityContract | None:
        return self._items.get(capability_id)

    def all(self) -> tuple[CapabilityContract, ...]:
        return tuple(self._items.values())
