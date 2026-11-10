from curator_service.app import names
from curator_service.app.project import state


def test_project_people_are_the_human_authors(tmp_path, monkeypatch):
    class P:
        def all(self, sql, params=()):
            assert "is_bot = 0" in sql
            return [{"name": "420yolocoon", "display_name": "Jake"}]
    monkeypatch.setattr(state, "project", P())
    assert names.project_people() == [["Jake", "420yolocoon"]]
    monkeypatch.setattr(state, "project", None)
    assert names.project_people() == []
