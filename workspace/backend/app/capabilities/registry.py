from .contract import CapabilityContract
from .manifest import validate


class CapabilityRegistry:
    def __init__(self):
        self._items: dict[str, CapabilityContract] = {}
        self._task_owners: dict[str, str] = {}

    def register(self, contract: CapabilityContract) -> CapabilityContract:
        validate(contract)
        if contract.id in self._items:
            raise ValueError(f"Duplicate capability: {contract.id}")
        duplicates = sorted(set(contract.owns_task_types) & set(self._task_owners))
        if duplicates:
            owner = self._task_owners[duplicates[0]]
            raise ValueError(f"Duplicate task ownership: {duplicates[0]} already owned by {owner}")
        self._items[contract.id] = contract
        for task_type in contract.owns_task_types:
            self._task_owners[task_type] = contract.id
        return contract

    def get(self, capability_id: str) -> CapabilityContract | None:
        return self._items.get(capability_id)

    def all(self) -> tuple[CapabilityContract, ...]:
        return tuple(self._items.values())

    def owner_for_task_type(self, task_type: str | None) -> CapabilityContract | None:
        capability_id = self._task_owners.get(task_type or "")
        return self._items.get(capability_id) if capability_id else None
