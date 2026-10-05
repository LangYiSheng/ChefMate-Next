"""Readers retain a coherent dataset while an import waits to replace it."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event


def test_active_reader_blocks_replacement_and_returns_one_dataset(
    postgres_store, make_recipe, make_export, monkeypatch,
):
    postgres_store.replace_from_export(make_export([make_recipe("old")]), data_version="old")
    replacement = make_export([make_recipe("new")])
    reader_locked, release_reader, writer_started, writer_done = Event(), Event(), Event(), Event()
    original = postgres_store._connection

    @contextmanager
    def controlled_connection(*, read=False):
        if not read:
            writer_started.set()
        with original(read=read) as connection:
            if read:
                reader_locked.set()
                assert release_reader.wait(10), "Test did not release reader"
            yield connection

    monkeypatch.setattr(postgres_store, "_connection", controlled_connection)

    def replace():
        try:
            return postgres_store.replace_from_export(replacement, data_version="new")
        finally:
            writer_done.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        reader = executor.submit(postgres_store.search)
        assert reader_locked.wait(5)
        writer = executor.submit(replace)
        try:
            assert writer_started.wait(5)
            assert not writer_done.wait(0.2), "Replacement committed while reader held dataset lock"
        finally:
            release_reader.set()
        old_page = reader.result(timeout=5)
        assert old_page["total"] == 1
        assert [row["recipe_id"] for row in old_page["items"]] == ["old"]
        assert writer.result(timeout=5)["data_version"] == "new"
    assert [row["recipe_id"] for row in postgres_store.search()["items"]] == ["new"]
