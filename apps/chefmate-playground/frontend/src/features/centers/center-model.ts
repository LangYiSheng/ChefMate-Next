export type Data = Record<string, any>;
export const macros = [['calories_kcal', '热量', 'kcal'], ['protein_g', '蛋白质', 'g'], ['fat_g', '脂肪', 'g'], ['carbohydrate_g', '碳水', 'g']] as const;
export const nutritionKeys = [...macros.map(([key]) => key), 'sugar_g', 'sodium_mg'];
export const mealNames: Record<string, string> = {breakfast: '早餐', lunch: '午餐', dinner: '晚餐', snack: '加餐', unspecified: '未分类'};
export const numeric = (value: string | number | null) => value === '' || value === null ? null : Number(value);
export const lines = (text: string) => text.split('\n').map(value => value.trim()).filter(Boolean);
export const rounded = (value: number) => Math.round(value * 1000) / 1000;
export const numberText = (value: any, unit = '') => Number.isFinite(value) ? `${Math.round(value * 10) / 10}${unit ? ` ${unit}` : ''}` : '未知';
export function intakeMetric(summary: any, key: string) {
  const full = Number.isFinite(summary?.total?.[key]) ? summary.total[key] : null;
  const known = Number.isFinite(summary?.known_total?.[key]) ? summary.known_total[key] : null;
  return {value: full ?? known, partial: full === null && known !== null, coverage: summary?.coverage?.[key]};
}
export function recipeDraft(record: Data) {
  const recipe = record.document;
  return {name: recipe.name, description: recipe.description || '', servings: recipe.servings, servings_evidence: recipe.servings_evidence || '', prep_time_min: recipe.prep_time_min, cook_time_min: recipe.cook_time_min, total_time_min: recipe.total_time_min, time_evidence: record.time_evidence || '', source_kind: record.source_kind, source_text: record.source_text || '', warnings: [...(recipe.warnings || [])], steps: [...recipe.steps], ingredients: recipe.ingredients.map((food: Data) => ({name: food.canonical_name || '', amount: food.raw_amount ? `${food.raw_amount}${food.raw_unit || ''}` : '适量', grams: food.grams, amount_evidence: food.amount_evidence || '', scalable: food.scalable, usda_query: food.usda_query || '', allergens: food.allergens || []}))};
}
export function updateRecipeIngredient(previous: Data, key: string, value: string, reference: Data = previous) {
  const next = {...previous, [key]: value}, sameName = next.name.trim() === reference.name.trim(), unchanged = sameName && next.amount.trim() === reference.amount.trim();
  return {...next, grams: unchanged ? reference.grams : null, amount_evidence: unchanged ? reference.amount_evidence : '', usda_query: sameName ? reference.usda_query : '', allergens: sameName ? reference.allergens : []};
}
export function emptyFoodItem() {
  return {name: '', portion: '', amount_g: null, portion_basis: 'unknown', nutrition: Object.fromEntries(nutritionKeys.map(key => [key, null])), nutrition_basis: 'unknown', evidence: '', assumptions: []};
}
export function updateFoodItem(previous: Data, key: string, value: any, reference: Data = previous): Data {
  const item = structuredClone(previous);
  if (key === 'name' || key === 'portion') {
    if (item[key] === value) return item;
    item[key] = value; item.amount_g = null; item.nutrition = Object.fromEntries(nutritionKeys.map(key => [key, null])); item.nutrition_basis = 'unknown'; item.evidence = ''; item.assumptions = [];
    if (key === 'portion') item.portion_basis = value.trim() ? 'user_provided' : 'unknown';
    if (key === 'name' && item.portion_basis === 'estimated') item.assumptions = ['份量描述沿用原估计；食物名称已修改，仍需重新核对。'];
  } else if (key === 'amount_g') {
    const grams = numeric(value);
    if (grams !== null && grams > 0 && reference.amount_g > 0) {
      item.nutrition = Object.fromEntries(nutritionKeys.map(key => [key, Number.isFinite(reference.nutrition[key]) ? rounded(reference.nutrition[key] * grams / reference.amount_g) : null]));
      const known = nutritionKeys.some(key => item.nutrition[key] !== null);
      item.nutrition_basis = known ? 'estimated' : 'unknown';
      item.evidence = known ? '按原克重与营养同比例换算，未重新测量食材或烹饪差异。' : '';
      if (known) item.assumptions = [...(item.assumptions || []).filter((line: string) => !line.startsWith('假定食材组成和烹饪用油比例不变，按 ')), `假定食材组成和烹饪用油比例不变，按 ${reference.amount_g} 克换算至 ${grams} 克。`];
    } else {item.nutrition = Object.fromEntries(nutritionKeys.map(key => [key, null])); item.nutrition_basis = 'unknown'; item.evidence = '';}
    item.amount_g = grams;
    if (grams !== null && grams > 0) {item.portion = `${grams} 克`; item.portion_basis = 'user_provided';}
  } else item[key] = value;
  return item;
}
export function validateFood(document: Data) {
  if (!document.title.trim()) return '请填写记录名称。';
  if (!document.items.length || document.items.length > 30) return '每份记录需要 1–30 项食物。';
  for (let index = 0; index < document.items.length; index++) {
    const item = document.items[index], hasNutrition = nutritionKeys.some(key => item.nutrition[key] !== null);
    let error = '';
    if (!item.name.trim()) error = '请填写食物名称。';
    else if ((item.amount_g !== null || hasNutrition) && (!item.portion.trim() || item.portion_basis === 'unknown')) error = '请先写明你吃的份量，或将未知的克重、营养留空。';
    else if (item.amount_g !== null && (!Number.isFinite(item.amount_g) || item.amount_g <= 0)) error = '克重需要大于 0，未知可以留空。';
    else if (hasNutrition && !item.evidence.trim()) error = '请补充营养依据。';
    else if ((item.portion_basis === 'estimated' || hasNutrition && item.nutrition_basis === 'estimated') && !item.assumptions.some((line: string) => line.trim())) error = '请说明估算份量或营养时采用的假设。';
    else if (item.assumptions.length > 12 || item.assumptions.some((line: string) => line.length > 400)) error = '估算假设最多 12 条，每条不超过 400 字。';
    if (error) return `第 ${index + 1} 项：${error}`;
  }
  return '';
}
