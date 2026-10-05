"""USDA data is always read from the local catalogue."""
from chefmate_core.fooddata import DATA_TYPES as DATA_TYPES, FoodDataError, LocalFoodData
from chefmate_core.nutrition import NUTRIENT_IDS as NUTRIENT_IDS, parse_nutrients as parse_nutrients
from data_cleaning.config import usda_database_path

USDARequestError = FoodDataError

class USDAService(LocalFoodData):
    def __init__(self, repo=None, *, path=None):
        super().__init__(path if path is not None else usda_database_path())
        self.repo = repo

    def search(self, query: str, limit: int = 15) -> list[dict]:
        result = super().search(query, limit)
        if self.repo:
            self.repo.service_call("USDA", "local_search", "", "approved", {"query": query}, result)
        return result

    def food(self, fdc_id: str, *, refresh: bool = False) -> dict:
        result = super().food(fdc_id, refresh=refresh)
        parse_nutrients(result)
        if self.repo:
            self.repo.cache_food(result)
        if self.repo:
            self.repo.service_call("USDA", "local_food", "", "approved", {"fdc_id": str(fdc_id)},
                                   {"dataset": self.provenance(fdc_id)})
        return result

def local_status() -> dict:
    return USDAService().status()
