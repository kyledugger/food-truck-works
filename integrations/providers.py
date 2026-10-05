from dataclasses import dataclass
from typing import Protocol


class DirectoryProvider(Protocol):
    async def locations(self, token: str) -> list[dict]: ...
    async def employees(self, token: str) -> list[dict]: ...


@dataclass(frozen=True)
class ProviderDefinition:
    key: str
    capabilities: dict[str, frozenset[str]]

    def authorized(self, scopes: list[str]) -> set[str]:
        return {name for name, required in self.capabilities.items() if required <= set(scopes)}


SQUARE = ProviderDefinition("square", {
    "locations.read": frozenset({"MERCHANT_PROFILE_READ"}),
    "employees.read": frozenset({"EMPLOYEES_READ"}),
    "sales.read": frozenset({"ORDERS_READ", "PAYMENTS_READ"}),
    "timecards.read": frozenset({"TIMECARDS_READ"}),
    "timecards.write": frozenset({"TIMECARDS_WRITE"}),
    "schedules.read": frozenset({"TIMECARDS_READ"}),
    "schedules.write": frozenset({"TIMECARDS_WRITE"}),
})
PROVIDERS = {"square": SQUARE}
# Request only the directory permissions used by this foundation.
SQUARE_SCOPES = ["MERCHANT_PROFILE_READ", "EMPLOYEES_READ"]
