"""Validated filters and parameterized queries over normalized recipe tables."""

from dataclasses import dataclass
from math import isfinite

NUTRIENTS = ("calories_kcal", "protein_g", "fat_g", "carbohydrate_g", "sugar_g", "sodium_mg")


def _number(value: float | None, name: str) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, (float, int))
                              or not isfinite(value) or value < 0):
        raise ValueError(f"{name} must be a finite nonnegative number")


@dataclass(frozen=True)
class NutrientRange:
    name: str
    minimum: float | None = None
    maximum: float | None = None
    basis: str = "per_serving"

    def __post_init__(self):
        if self.name not in NUTRIENTS or self.basis not in ("total", "per_serving"):
            raise ValueError("Unsupported nutrient or basis")
        _number(self.minimum, "minimum")
        _number(self.maximum, "maximum")
        if self.minimum is None and self.maximum is None:
            raise ValueError("A nutrient range needs at least one bound")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum must not exceed maximum")


@dataclass(frozen=True)
class RecipeFilter:
    name: str | None = None
    ingredient_ids: tuple[str, ...] = ()
    exclude_ingredient_ids: tuple[str, ...] = ()
    taste_tags: tuple[str, ...] = ()
    cooking_methods: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    nutrition_tags: tuple[str, ...] = ()
    exclude_allergens: tuple[str, ...] = ()
    max_total_time_min: float | None = None
    min_servings: float | None = None
    ready_for_recommendation: bool | None = True
    nutrients: tuple[NutrientRange, ...] = ()

    def __post_init__(self):
        if self.name is not None and (not isinstance(self.name, str) or not self.name.strip()):
            raise ValueError("name must be nonempty text")
        for name in ("ingredient_ids", "exclude_ingredient_ids", "taste_tags", "cooking_methods", "required_tools",
                     "nutrition_tags", "exclude_allergens"):
            values = getattr(self, name)
            if not isinstance(values, (tuple, list)) or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError(f"{name} must contain nonempty strings")
            object.__setattr__(self, name, tuple(dict.fromkeys(values)))
        _number(self.max_total_time_min, "max_total_time_min")
        _number(self.min_servings, "min_servings")
        if self.ready_for_recommendation is not None and type(self.ready_for_recommendation) is not bool:
            raise ValueError("ready_for_recommendation must be bool or None")
        if not isinstance(self.nutrients, (tuple, list)) or any(not isinstance(v, NutrientRange) for v in self.nutrients):
            raise ValueError("nutrients must contain NutrientRange values")
        object.__setattr__(self, "nutrients", tuple(self.nutrients))


def where_clause(filters: RecipeFilter) -> tuple[str, list]:
    clauses, args = [], []
    if filters.name is not None:
        clauses.append("r.name ILIKE %s ESCAPE '\\'")
        args.append("%" + filters.name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if filters.ready_for_recommendation is not None:
        clauses.append("r.ready_for_recommendation = %s")
        args.append(filters.ready_for_recommendation)
    for value in filters.ingredient_ids:
        clauses.append("EXISTS (SELECT 1 FROM recipe_ingredients i WHERE i.recipe_id=r.recipe_id AND i.ingredient_id=%s)")
        args.append(value)
    if filters.exclude_ingredient_ids:
        clauses.append("NOT EXISTS (SELECT 1 FROM recipe_ingredients i WHERE i.recipe_id=r.recipe_id "
                       "AND i.ingredient_id=ANY(%s))")
        args.append(list(filters.exclude_ingredient_ids))
    for kind, values in (("taste", filters.taste_tags), ("cooking_method", filters.cooking_methods),
                         ("tool", filters.required_tools), ("nutrition", filters.nutrition_tags)):
        for value in values:
            clauses.append("EXISTS (SELECT 1 FROM recipe_labels l WHERE l.recipe_id=r.recipe_id AND l.kind=%s "
                           "AND l.value=%s)")
            args.extend((kind, value))
    if filters.exclude_allergens:
        clauses.append("NOT EXISTS (SELECT 1 FROM ingredient_allergens a WHERE a.recipe_id=r.recipe_id "
                       "AND a.allergen=ANY(%s))")
        args.append(list(filters.exclude_allergens))
    if filters.max_total_time_min is not None:
        clauses.append("r.total_time_min <= %s")
        args.append(filters.max_total_time_min)
    if filters.min_servings is not None:
        clauses.append("r.servings >= %s")
        args.append(filters.min_servings)
    for bound in filters.nutrients:
        # Column fragments only come from the validated closed vocabulary above.
        column = f"n.{bound.basis}_{bound.name}"
        if bound.minimum is not None:
            clauses.append(f"{column} >= %s")
            args.append(bound.minimum)
        if bound.maximum is not None:
            clauses.append(f"{column} <= %s")
            args.append(bound.maximum)
    return " AND ".join(clauses) or "TRUE", args
