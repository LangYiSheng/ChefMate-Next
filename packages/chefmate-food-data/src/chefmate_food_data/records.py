from chefmate_contracts import StrictModel, Nutrients


class FoodRecord(StrictModel):
    fdc_id: str
    description: str
    data_type: str
    nutrients_per_100g: Nutrients
    provenance: dict
