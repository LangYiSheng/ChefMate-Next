"""Injectable model and food query boundaries."""
from dataclasses import dataclass, field
from typing import Callable, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class RequestContext:
    settings: dict = field(default_factory=dict)
    progress: Callable | None = None
    on_call: Callable | None = None
    on_error: Callable | None = None


class ModelClient(Protocol):
    def request(self, purpose: str, payload: dict, schema: type[T], entity_id: str,
                validator=None, *, system_prompt=None) -> T: ...


class AsyncModelClient(Protocol):
    async def arequest(self, purpose: str, payload: dict, schema: type[T], entity_id: str,
                       validator=None, *, system_prompt=None) -> T: ...


class FoodData(Protocol):
    def search(self, query: str, limit: int = 15) -> list[dict]: ...
    def food(self, fdc_id: str) -> dict: ...
    def provenance(self, fdc_id: str) -> dict: ...
