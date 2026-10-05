"""Public text/Markdown recipe processing, independent of persistence and UI."""
from copy import deepcopy

from chefmate_contracts import RecipeDocument, validate_recipe_vocabulary, vocabulary_snapshot

from .execution import io, run, arun, bind_model
from .estimation import _estimate_missing, estimate_missing, aestimate_missing, ESTIMATE_KEY, valid_estimate
from .nutrition import calculate, keep_snapshots
from .processor import RecipeProcessor
from .schemas import (
    RecipeSource, ProcessingOptions, ProcessingResult, ProcessingIssue, MatchingResult, TagThresholds,
)


def validate_recipe(document, *, vocabulary=None) -> RecipeDocument:
    if isinstance(document, (str, bytes)):
        document = RecipeDocument.model_validate_json(document)
    else:
        document = RecipeDocument.model_validate(document)
    if vocabulary is not None:
        validate_recipe_vocabulary(document, vocabulary)
    return document


def _source(value):
    return RecipeSource(content=value) if isinstance(value, str) else RecipeSource.model_validate(value)


def extract_recipe(source, *, ai, vocabulary, context=None, progress=None, **options):
    source = _source(source)
    options.setdefault("entity_id", source.source_id or "")
    return RecipeProcessor(ai).extract(source.content, vocabulary=vocabulary,
                                       context=context, progress=progress, **options)


async def aextract_recipe(source, *, ai, vocabulary, context=None, progress=None, **options):
    source = _source(source)
    options.setdefault("entity_id", source.source_id or "")
    return await RecipeProcessor(ai).aextract(source.content, vocabulary=vocabulary,
                                             context=context, progress=progress, **options)


def _matching_result(result):
    issues = [ProcessingIssue(stage="match", status=value["status"], ingredient_key=key,
                              message=value.get("error") or value["status"])
              for key, value in result["snapshots"].items()
              if key != ESTIMATE_KEY and value.get("status") in ("unmatched", "request_failed", "processing_failed")]
    return MatchingResult(**result, issues=issues)


def match_ingredients(document, *, ai, food_data, context=None, progress=None, **options):
    return _matching_result(RecipeProcessor(ai, food_data).enrich(
        validate_recipe(document), context=context, progress=progress, **options))


async def amatch_ingredients(document, *, ai, food_data, context=None, progress=None, **options):
    return _matching_result(await RecipeProcessor(ai, food_data).aenrich(
        validate_recipe(document), context=context, progress=progress, **options))


def calculate_nutrition(document, snapshots, *, estimates=None, thresholds=None):
    document = validate_recipe(document)
    snapshots = keep_snapshots(document, deepcopy(snapshots))
    if estimates is not None:
        snapshots[ESTIMATE_KEY] = deepcopy(estimates)
    return calculate(document, snapshots, thresholds or TagThresholds())


def _process(source, *, ai, vocabulary, food_data=None, options=None, thresholds=None,
             context=None, progress=None, snapshots=None, confirmed_lookup=None,
             prompt=None, feedback="", current_json=None):
    source = _source(source)
    options = ProcessingOptions.model_validate(options or {})
    model = bind_model(ai, context=context, progress=progress)
    # Take one immutable vocabulary/configuration view for the entire invocation.
    vocabulary = vocabulary_snapshot(vocabulary)
    processor = RecipeProcessor(model, food_data, progress=getattr(model, "progress", progress))
    document = yield from processor._extract(source.content, vocabulary=vocabulary,
                                             prompt=prompt, feedback=feedback, current_json=current_json,
                                             entity_id=source.source_id or "")
    matches = keep_snapshots(document, deepcopy(snapshots or {}))
    issues = []
    if options.with_nutrition:
        if food_data is None:
            raise ValueError("营养匹配需要提供 food_data")
        result = yield from processor._enrich(document, snapshots=matches,
                                              confirmed_lookup=confirmed_lookup, entity_id=source.source_id or "")
        matched = _matching_result(result)
        matches, issues = matched.snapshots, matched.issues
    if options.estimate_missing:
        try:
            estimate = yield from _estimate_missing(document, matches, model, source.source_id or "")
            if estimate:
                matches[ESTIMATE_KEY] = estimate
        except Exception as exc:
            issues.append(ProcessingIssue(stage="estimate", status="processing_failed",
                                          message=f"{type(exc).__name__}：估算失败，已获得结果保留"))
    else:
        matches.pop(ESTIMATE_KEY, None)
    nutrition = (yield io(calculate_nutrition, document, matches, thresholds=thresholds)) if (
        options.with_nutrition or options.estimate_missing) else None
    return ProcessingResult(recipe=document,
                            ingredient_matches={k: v for k, v in matches.items() if k != ESTIMATE_KEY},
                            estimates=valid_estimate(document, matches) or None,
                            nutrition=nutrition, issues=issues,
                            processing_metadata={"source_kind": source.kind, "source_id": source.source_id,
                                                 "package_version": "0.1.0", "vocabulary_sha256": vocabulary.sha256,
                                                 "model": getattr(model, "settings", {}).get("model")})


def process_recipe(source, **kwargs) -> ProcessingResult:
    return run(_process(source, **kwargs))


async def aprocess_recipe(source, **kwargs) -> ProcessingResult:
    return await arun(_process(source, **kwargs))


__all__ = ["validate_recipe", "extract_recipe", "aextract_recipe", "match_ingredients", "amatch_ingredients",
           "calculate_nutrition", "estimate_missing", "aestimate_missing", "process_recipe", "aprocess_recipe"]
