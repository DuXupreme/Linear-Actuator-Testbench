from pathlib import Path

from gui.main_window import MainWindow


def test_delete_log_files_only_removes_matching_run(tmp_path: Path) -> None:
    csv_path = tmp_path / "actuator_2026-01-02_03-04-05.csv"
    json_path = csv_path.with_suffix(".json")
    graph_path = csv_path.with_name(f"{csv_path.stem}_graphs.png")
    unrelated = tmp_path / "notes.txt"
    for path in (csv_path, json_path, graph_path, unrelated):
        path.write_text("test", encoding="utf-8")

    assert MainWindow._delete_log_files(csv_path) == 3
    assert not csv_path.exists()
    assert not json_path.exists()
    assert not graph_path.exists()
    assert unrelated.exists()

