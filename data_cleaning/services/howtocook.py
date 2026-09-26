"""Conservative HowToCook Markdown reader. No semantic renaming or nutrition guesses."""

import re
from pathlib import Path

from data_cleaning.schemas.recipe import Ingredient, Recipe

PARSER_VERSION = "howtocook-v2"
LIST_ITEM = re.compile(r"^\s*(?:[-*+] |\d+[.)、]\s*)(.+)$")
AMOUNT = re.compile(
    r"^(?P<name>.+?)\s*(?:[：:]\s*)?"
    r"(?P<amount>(?:约|大约)?\s*(?:\d+(?:\.\d+)?|[一二三四五六七八九十两半]+)"
    r"(?:\s*[~～\-至到]\s*\d+(?:\.\d+)?)?\s*"
    r"(?P<unit>kg|ml|g|L|l|千克|公斤|毫升|克|斤|两|个|颗|只|根|片|勺|茶匙|汤匙|杯|碗|袋|盒|瓣|块|升)(?:.*)"
    r"|适量|少许|若干|一些)$",
    re.IGNORECASE,
)
TOOL_NAMES = {
    "锅",
    "炒锅",
    "平底锅",
    "铁锅",
    "砂锅",
    "蒸锅",
    "烤箱",
    "空气炸锅",
    "微波炉",
    "电饭煲",
    "高压锅",
    "刀",
    "菜刀",
    "砧板",
    "案板",
    "漏勺",
    "打蛋器",
}


def discover(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise ValueError(f"数据目录不存在：{directory}")
    # A checkout root is supported, as is a dishes subtree or a fixture directory.
    root = directory / "dishes" if (directory / "dishes").is_dir() else directory
    return sorted(
        p
        for p in root.rglob("*.md")
        if p.name.lower() != "readme.md" and not p.is_symlink() and p.resolve().is_relative_to(directory.resolve())
    )


def sections(text: str) -> tuple[str, dict[str, str]]:
    title = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if not title:
        raise ValueError("缺少一级菜谱标题")
    parts: dict[str, str] = {}
    current = "前言"
    parents: list[tuple[int, str]] = []
    for line in text.splitlines():
        heading = re.match(r"^(#{2,6})\s+(.+)$", line)
        if heading:
            level = len(heading[1])
            while parents and parents[-1][0] >= level:
                parents.pop()
            parents.append((level, heading[2].strip()))
            current = " / ".join(title for _, title in parents)
            while current in parts:
                current += "（续）"
        else:
            parts[current] = parts.get(current, "") + line + "\n"
    return title[1].strip(), parts


def ingredient(line: str, section: str) -> Ingredient:
    match = AMOUNT.match(line)
    return Ingredient(
        raw_name=match["name"].strip(" ：:") if match else line,
        raw_amount=match["amount"].strip() if match else None,
        raw_unit=match["unit"] if match else None,
        raw_text=line,
        source_section=section,
    )


def parse(text: str, recipe_id: str, source: str) -> Recipe:
    name, parts = sections(text)
    inventories = [(k, v) for k, v in parts.items() if any(x in k for x in ("原料", "食材", "材料"))]
    calculations = [(k, v) for k, v in parts.items() if "计算" in k or "用量" in k]
    operations = [(k, v) for k, v in parts.items() if any(x in k for x in ("操作", "步骤", "做法"))]
    if not inventories or not operations:
        raise ValueError("无法识别原料或操作章节，需要人工结构化")
    warnings: list[str] = []
    tools: list[str] = []
    ingredients: list[Ingredient] = []
    selected = (
        calculations if any(LIST_ITEM.search(line) for _, v in calculations for line in v.splitlines()) else inventories
    )
    for heading, body in selected:
        for line in body.splitlines():
            item = LIST_ITEM.match(line)
            if not item:
                continue
            value = item[1].strip()
            parsed = ingredient(value, heading)
            if parsed.raw_name in TOOL_NAMES:
                tools.append(value)
            else:
                ingredients.append(parsed)
    if selected == calculations:
        warnings.append("优先提取计算章节；原料章节完整保留在 raw_sections，请核对遗漏、别名和用量冲突。")
    for heading, body in parts.items():
        if "工具" in heading or "厨具" in heading:
            for line in body.splitlines():
                item = LIST_ITEM.match(line)
                if item and ("原料" not in heading or ingredient(item[1], heading).raw_name in TOOL_NAMES):
                    tools.append(item[1].strip())
    steps: list[str] = []
    for _, body in operations:
        for line in body.splitlines():
            if not line.strip():
                continue
            item = LIST_ITEM.match(line)
            if item and not line.startswith(("  ", "\t")):
                steps.append(item[1].strip())
            elif steps:
                steps[-1] += "\n" + line
            else:
                steps.append(line)
    intro = parts.get("前言", "")
    description = "\n".join(line for line in intro.splitlines() if not line.startswith("#")).strip()
    servings = re.search(
        r"^.*(?:\d+|[一二三四五六七八九十两]+)\s*(?:人份|人食用|人食|人量|盘的份量|人份量).*$", text, re.MULTILINE
    )
    difficulty = re.search(r"(?:预估烹饪难度|难度)\s*[：:]\s*(.+)", text)
    times: dict[str, float | None] = {}
    for field, label in (
        ("prep_time_min", "准备时间"),
        ("cook_time_min", "烹饪时间"),
        ("total_time_min", "总时间|总耗时"),
    ):
        match = re.search(rf"(?:{label})\s*[：:]\s*(\d+(?:\.\d+)?)\s*分钟", text)
        times[field] = float(match[1]) if match else None
    if any(i.raw_amount is None for i in ingredients):
        warnings.append("部分食材无法可靠分离名称和用量，保留整条文本，请人工核对。")
    return Recipe(
        recipe_id=recipe_id,
        name=name,
        source=source,
        description=description or None,
        original_servings=servings[0].strip() if servings else None,
        difficulty=difficulty[1].strip() if difficulty else None,
        ingredients=ingredients,
        steps=steps,
        required_tools=list(dict.fromkeys(tools)),
        raw_sections=parts,
        extraction_warnings=warnings,
        **times,
    )
