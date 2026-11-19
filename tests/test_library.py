from pathlib import Path

import pytest

from resplandy.harness import start_run
from resplandy.tools import literature


def _listing(folder):
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))


def test_inventory_gives_each_file_its_status(cfg, lib_folder):
    run = start_run(cfg)
    files, notes = literature.list_library(run, lib_folder)
    by_name = {f.file: f for f in files}
    assert (by_name["a.txt"].status, by_name["a.txt"].pages) == ("unmatched", 1)
    assert by_name["empty.txt"].status == "unreadable"       # listed, never read
    assert by_name["sub/c.txt"].status == "unmatched"        # subfolders are included
    assert notes == [] and run.trace.read()[-1]["name"] == "list_library"


def test_home_root_and_missing_folders_are_refused():
    for bad in (Path.home(), Path("/"), Path("/no/such/folder/for/these/tests")):
        with pytest.raises(literature.LibraryError):
            literature.check_folder(bad)


def test_a_symlink_that_leaves_the_folder_is_skipped(cfg, lib_folder, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("TEST PAPER OUTSIDE the paper folder.", encoding="utf-8")
    (lib_folder / "link.txt").symlink_to(outside)
    files, notes = literature.list_library(start_run(cfg), lib_folder)
    assert "link.txt" not in {f.file for f in files}
    assert any("link.txt" in note for note in notes)


def test_the_listing_stops_at_max_files_and_says_so(cfg, lib_folder):
    cfg.library.max_files = 2
    files, notes = literature.list_library(start_run(cfg), lib_folder)
    assert len(files) == 2 and any("max_files" in note for note in notes)


def test_search_and_read_work_and_never_write_into_the_folder(cfg, lib_folder):
    before, run = _listing(lib_folder), start_run(cfg)
    hits = literature.search_library(run, lib_folder, "Testium sorbent working capacity")
    assert hits and (hits[0]["file"], hits[0]["page"]) == ("a.txt", 1)
    assert "Testium-1" in literature.read_page(run, lib_folder, "a.txt", 1)
    assert _listing(lib_folder) == before
    assert [e["name"] for e in run.trace.read()] == ["search_library", "read_page"]
