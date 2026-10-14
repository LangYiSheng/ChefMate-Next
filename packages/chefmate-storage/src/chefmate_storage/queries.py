"""Validated filters and parameterized queries over normalized recipe tables."""

from dataclasses import dataclass
from math import isfinite

NUTRIENTS = ("calories_kcal", "protein_g", "fat_g", "carbohydrate_g", "sugar_g", "sodium_mg")
SORT_FIELDS = ("recipe_id", "name", "total_time_min", "difficulty", *(
    f"{basis}_{name}" for basis in ("total", "per_serving") for name in NUTRIENTS
))


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
    allow_estimated: bool = True

    def __post_init__(self):
        if self.name not in NUTRIENTS or self.basis not in ("total", "per_serving"):
            raise ValueError("Unsupported nutrient or basis")
        _number(self.minimum, "minimum")
        _number(self.maximum, "maximum")
        if self.minimum is None and self.maximum is None:
            raise ValueError("A nutrient range needs at least one bound")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum must not exceed maximum")
        if type(self.allow_estimated) is not bool:
            raise ValueError("allow_estimated must be bool")


@dataclass(frozen=True)
class IngredientSelector:
    ingredient_id: str
    canonical_name: str | None = None

    def __post_init__(self):
        if not isinstance(self.ingredient_id, str) or not self.ingredient_id.strip():
            raise ValueError("ingredient_id must be nonempty text")
        if self.canonical_name is not None and (not isinstance(self.canonical_name, str)
                                                or not self.canonical_name.strip()):
            raise ValueError("canonical_name must be nonempty text")


@dataclass(frozen=True)
class IngredientGroup:
    any_of: tuple[IngredientSelector, ...]

    def __post_init__(self):
        if not isinstance(self.any_of, (list, tuple)) or not self.any_of or any(
                not isinstance(value, IngredientSelector) for value in self.any_of):
            raise ValueError("any_of must contain IngredientSelector values")
        object.__setattr__(self, "any_of", tuple(dict.fromkeys(self.any_of)))


@dataclass(frozen=True)
class RecipeSort:
    field: str = "recipe_id"
    direction: str = "asc"

    def __post_init__(self):
        if self.field not in SORT_FIELDS or self.direction not in ("asc", "desc"):
            raise ValueError("Unsupported sort field or direction")


def order_clause(sort: RecipeSort) -> str:
    if not isinstance(sort, RecipeSort):
        raise ValueError("sort must be RecipeSort")
    if sort.field == "difficulty":
        column = "CASE r.difficulty WHEN 'beginner' THEN 0 WHEN 'normal' THEN 1 WHEN 'advanced' THEN 2 END"
    elif sort.field in ("recipe_id", "name", "total_time_min"):
        column = "r." + sort.field
    else:
        column = "n." + sort.field
    return f"{column} {sort.direction.upper()} NULLS LAST, r.recipe_id ASC"


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
    ingredient_groups: tuple[IngredientGroup, ...] = ()
    exclude_ingredients: tuple[IngredientSelector, ...] = ()
    min_total_time_min: float | None = None
    difficulties: tuple[str, ...] = ()
    taste_tags_any: tuple[str, ...] = ()
    exclude_taste_tags: tuple[str, ...] = ()
    cooking_methods_any: tuple[str, ...] = ()
    available_tools: tuple[str, ...] | None = None

    def __post_init__(self):
        if self.name is not None and (not isinstance(self.name, str) or not self.name.strip()):
            raise ValueError("name must be nonempty text")
        for name in ("ingredient_ids", "exclude_ingredient_ids", "taste_tags", "cooking_methods", "required_tools",
                     "nutrition_tags", "exclude_allergens", "difficulties", "taste_tags_any",
                     "exclude_taste_tags", "cooking_methods_any"):
            values = getattr(self, name)
            if not isinstance(values, (tuple, list)) or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError(f"{name} must contain nonempty strings")
            object.__setattr__(self, name, tuple(dict.fromkeys(values)))
        _number(self.max_total_time_min, "max_total_time_min")
        _number(self.min_total_time_min, "min_total_time_min")
        if (self.min_total_time_min is not None and self.max_total_time_min is not None
                and self.min_total_time_min > self.max_total_time_min):
            raise ValueError("min_total_time_min must not exceed max_total_time_min")
        if any(value not in ("beginner", "normal", "advanced") for value in self.difficulties):
            raise ValueError("Unsupported difficulty")
        if self.available_tools is not None:
            if not isinstance(self.available_tools, (list, tuple)) or any(
                    not isinstance(value, str) or not value.strip() for value in self.available_tools):
                raise ValueError("available_tools must contain nonempty strings or be None")
            object.__setattr__(self, "available_tools", tuple(dict.fromkeys(self.available_tools)))
        for name, expected in (("ingredient_groups", IngredientGroup), ("exclude_ingredients", IngredientSelector)):
            values = getattr(self, name)
            if not isinstance(values, (list, tuple)) or any(not isinstance(value, expected) for value in values):
                raise ValueError(f"{name} contains invalid values")
            object.__setattr__(self, name, tuple(dict.fromkeys(values)))
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
    for group in filters.ingredient_groups:
        predicate, selector_args = selector_clause(group.any_of)
        clauses.append("EXISTS (SELECT 1 FROM recipe_ingredients i WHERE i.recipe_id=r.recipe_id AND "
                       + predicate + ")")
        args.extend(selector_args)
    if filters.exclude_ingredients:
        predicate, selector_args = selector_clause(filters.exclude_ingredients)
        clauses.append("NOT EXISTS (SELECT 1 FROM recipe_ingredients i WHERE i.recipe_id=r.recipe_id AND "
                       + predicate + ")")
        args.extend(selector_args)
    for kind, values in (("taste", filters.taste_tags), ("cooking_method", filters.cooking_methods),
                         ("tool", filters.required_tools), ("nutrition", filters.nutrition_tags)):
        for value in values:
            clauses.append("EXISTS (SELECT 1 FROM recipe_labels l WHERE l.recipe_id=r.recipe_id AND l.kind=%s "
                           "AND l.value=%s)")
            args.extend((kind, value))
    for kind, values, negate in (("taste", filters.taste_tags_any, False),
                                 ("taste", filters.exclude_taste_tags, True),
                                 ("cooking_method", filters.cooking_methods_any, False)):
        if values:
            clauses.append(("NOT " if negate else "") + "EXISTS (SELECT 1 FROM recipe_labels l WHERE "
                           "l.recipe_id=r.recipe_id AND l.kind=%s AND l.value=ANY(%s))")
            args.extend((kind, list(values)))
    if filters.available_tools is not None:
        clauses.append("NOT EXISTS (SELECT 1 FROM recipe_labels l WHERE l.recipe_id=r.recipe_id "
                       "AND l.kind='tool' AND NOT (l.value=ANY(%s)))")
        args.append(list(filters.available_tools))
    if filters.difficulties:
        clauses.append("r.difficulty=ANY(%s)")
        args.append(list(filters.difficulties))
    if filters.exclude_allergens:
        clauses.append("NOT EXISTS (SELECT 1 FROM ingredient_allergens a WHERE a.recipe_id=r.recipe_id "
                       "AND a.allergen=ANY(%s))")
        args.append(list(filters.exclude_allergens))
    if filters.max_total_time_min is not None:
        clauses.append("r.total_time_min <= %s")
        args.append(filters.max_total_time_min)
    if filters.min_total_time_min is not None:
        clauses.append("r.total_time_min >= %s")
        args.append(filters.min_total_time_min)
    if filters.min_servings is not None:
        clauses.append("r.servings >= %s")
        args.append(filters.min_servings)
    for bound in filters.nutrients:
        # Column fragments only come from the validated closed vocabulary above.
        column = f"n.{bound.basis}_{bound.name}"
        if not bound.allow_estimated:
            clauses.append(f"NOT n.estimated_{bound.basis}_{bound.name}")
        if bound.minimum is not None:
            clauses.append(f"{column} >= %s")
            args.append(bound.minimum)
        if bound.maximum is not None:
            clauses.append(f"{column} <= %s")
            args.append(bound.maximum)
    return " AND ".join(clauses) or "TRUE", args


def selector_clause(selectors: tuple[IngredientSelector, ...]) -> tuple[str, list]:
    """Exact occurrence selectors, OR within one expression; no name/ID guessing."""
    clauses, args = [], []
    for selector in selectors:
        part = "i.ingredient_id=%s"
        args.append(selector.ingredient_id)
        if selector.canonical_name is not None:
            part += " AND i.canonical_name=%s"
            args.append(selector.canonical_name)
        clauses.append("(" + part + ")")
    return "(" + " OR ".join(clauses) + ")", args
