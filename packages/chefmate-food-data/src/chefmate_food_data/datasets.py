"""Explicit dataset maintenance; network access is confined to download()."""

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from .fooddata import LocalFoodData, SCHEMA_VERSION


@dataclass(frozen=True)
class Dataset:
    data_type: str
    version: str
    filename: str
    root: str
    expected_count: int
    expected_nulls: int = 0

    @property
    def url(self):
        return "https://fdc.nal.usda.gov/fdc-datasets/" + self.filename


DATASETS = (
    Dataset("Foundation", "2026-04", "FoodData_Central_foundation_food_json_2026-04-30.zip",
            "FoundationFoods", 363, 32),
    Dataset("SR Legacy", "2018-04", "FoodData_Central_sr_legacy_food_json_2018-04.zip",
            "SRLegacyFoods", 7793),
)


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_archive(path):
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if sum(m.file_size for m in members) > 512 * 1024 * 1024:
            raise ValueError("USDA 压缩包解压大小超出限制")
        if archive.testzip() is not None:
            raise ValueError("USDA ZIP 校验失败")
        names = [m.filename for m in members if m.filename.endswith(".json")]
        if len(names) != 1:
            raise ValueError("USDA 压缩包必须包含一个 JSON 文件")
        return names[0]


def download(folder, *, datasets=DATASETS, session=None, retries=2, progress=print, sleeper=time.sleep):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    owns_session = session is None
    session = session or requests.Session()
    paths = []
    try:
        for dataset in datasets:
            target = folder / dataset.filename
            part = target.with_suffix(target.suffix + ".part")
            if target.exists():
                validate_archive(target)
                progress(f"已下载：{dataset.filename}")
                paths.append(target)
                continue
            for attempt in range(retries + 1):
                offset = part.stat().st_size if part.exists() else 0
                headers = {"Accept-Encoding": "identity"}
                if offset:
                    headers["Range"] = f"bytes={offset}-"
                try:
                    progress(f"下载 {dataset.data_type}：第 {attempt + 1} 次，从 {offset} 字节开始")
                    with session.get(dataset.url, headers=headers, stream=True, timeout=(15, 60)) as response:
                        if response.status_code == 416:
                            # A completed partial file can survive a crash before rename.
                            try:
                                validate_archive(part)
                            except (OSError, ValueError, zipfile.BadZipFile):
                                part.unlink(missing_ok=True)
                                raise ValueError("续传范围失效，重新下载") from None
                        else:
                            response.raise_for_status()
                            if response.status_code not in (200, 206):
                                raise ValueError("下载响应状态不正确")
                            total = None
                            if response.status_code == 206:
                                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                                if not match or int(match[1]) != offset:
                                    part.unlink(missing_ok=True)
                                    raise ValueError("续传响应范围不一致")
                                total = int(match[3])
                            mode = "ab" if response.status_code == 206 and offset else "wb"
                            received = 0
                            with part.open(mode) as stream:
                                for chunk in response.iter_content(256 * 1024):
                                    if chunk:
                                        stream.write(chunk)
                                        received += len(chunk)
                            length = response.headers.get("Content-Length")
                            if length and received != int(length):
                                raise ValueError("下载未完成，保留断点")
                            if total is not None and part.stat().st_size != total:
                                raise ValueError("下载总长度不一致，保留断点")
                            try:
                                validate_archive(part)
                            except (ValueError, zipfile.BadZipFile):
                                part.unlink(missing_ok=True)
                                raise ValueError("下载内容不是有效 USDA ZIP，将重新下载") from None
                    os.replace(part, target)
                    paths.append(target)
                    break
                except (requests.RequestException, ValueError, OSError):
                    if attempt == retries:
                        raise ValueError(f"下载失败：{dataset.data_type}；可再次运行命令续传") from None
                    sleeper(min(2**attempt, 4))
        return paths
    finally:
        if owns_session:
            session.close()


def import_datasets(folder, database, *, datasets=DATASETS):
    folder, database = Path(folder), Path(database).expanduser().resolve()
    if {d.data_type for d in datasets} != {"Foundation", "SR Legacy"} or len(datasets) != 2:
        raise ValueError("必须同时导入 Foundation 和 SR Legacy")
    hashes = {d.data_type: sha256(folder / d.filename) for d in datasets}
    old = LocalFoodData(database).status()
    if old["ready"] and all(
        any(row["data_type"] == d.data_type and row["sha256"] == hashes[d.data_type]
            and row["version"] == d.version and row["record_count"] == d.expected_count
            for row in old["datasets"]) for d in datasets
    ):
        return {**old, "unchanged": True}
    database.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix="usda-build-", suffix=".db", dir=database.parent)
    os.close(fd)
    temp = Path(temp)
    try:
        db = sqlite3.connect(temp)
        try:
            db.executescript("""
                CREATE TABLE datasets(data_type TEXT PRIMARY KEY, version TEXT NOT NULL, source_url TEXT NOT NULL,
                    sha256 TEXT NOT NULL, imported_at TEXT NOT NULL, record_count INTEGER NOT NULL,
                    skipped_nulls INTEGER NOT NULL);
                CREATE TABLE foods(fdc_id INTEGER PRIMARY KEY, description TEXT NOT NULL,
                    data_type TEXT NOT NULL REFERENCES datasets(data_type), raw_json TEXT NOT NULL);
                CREATE VIRTUAL TABLE foods_fts USING fts5(description, tokenize='porter unicode61');
            """)
            for dataset in datasets:
                path = folder / dataset.filename
                member = validate_archive(path)
                with zipfile.ZipFile(path) as archive:
                    payload = json.loads(archive.read(member))
                rows = payload.get(dataset.root) if isinstance(payload, dict) else None
                if not isinstance(rows, list):
                    raise ValueError(f"{dataset.data_type} JSON 根节点不正确")
                count = skipped = 0
                for food in rows:
                    if food is None:
                        skipped += 1
                        continue
                    if (not isinstance(food, dict) or type(food.get("fdcId")) is not int
                            or not 0 < food["fdcId"] <= 2**63 - 1
                            or not isinstance(food.get("description"), str) or not food["description"].strip()
                            or food.get("dataType") != dataset.data_type
                            or not isinstance(food.get("foodNutrients"), list)):
                        raise ValueError(f"{dataset.data_type} 存在无效食物记录")
                    for nutrient in food["foodNutrients"]:
                        if not isinstance(nutrient, dict) or not isinstance(nutrient.get("nutrient"), dict):
                            raise ValueError("USDA 营养字段结构不正确")
                    db.execute("INSERT INTO foods VALUES(?,?,?,?)", (
                        food["fdcId"], food["description"], dataset.data_type, json.dumps(food, ensure_ascii=False),
                    ))
                    db.execute("INSERT INTO foods_fts(rowid,description) VALUES(?,?)", (food["fdcId"], food["description"]))
                    count += 1
                if count != dataset.expected_count or skipped != dataset.expected_nulls:
                    raise ValueError(f"{dataset.data_type} 条目数不符合版本清单：{count} 条，{skipped} 个空项")
                if sha256(path) != hashes[dataset.data_type]:
                    raise ValueError("导入期间源文件发生变化")
                db.execute("INSERT INTO datasets VALUES(?,?,?,?,?,?,?)", (
                    dataset.data_type, dataset.version, dataset.url, hashes[dataset.data_type],
                    datetime.now(timezone.utc).isoformat(), count, skipped,
                ))
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            db.commit()
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("USDA 数据库完整性校验失败")
            db.execute("INSERT INTO foods_fts(foods_fts) VALUES('integrity-check')")
            db.commit()
        finally:
            db.close()
        LocalFoodData(temp).require_ready()
        os.replace(temp, database)
    finally:
        temp.unlink(missing_ok=True)
    return {**LocalFoodData(database).require_ready(), "unchanged": False}


download_datasets = download


def install_datasets(folder, database, *, datasets=DATASETS, **download_options):
    download_datasets(folder, datasets=datasets, **download_options)
    return import_datasets(folder, database, datasets=datasets)
