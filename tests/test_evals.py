import dataclasses

import evals


def test_eval_script_passes_in_mocked_mode(tmp_path, capsys):
    assert evals.main(["--output-dir", str(tmp_path)]) == 0
    assert "3/3 scenarios passed" in capsys.readouterr().out
    assert sorted(p.name for p in tmp_path.iterdir()) == ["prep_m_001.md", "prep_m_002.md", "prep_m_003.md"]


def test_eval_covers_normal_new_contact_and_no_agenda():
    assert [(s.name, s.meeting_id) for s in evals.SCENARIOS] == [
        ("normal", "m_001"), ("new_contact", "m_002"), ("no_agenda", "m_003")]


def test_eval_fails_when_a_key_string_is_missing(tmp_path, capsys, monkeypatch):
    broken = dataclasses.replace(evals.SCENARIOS[2], must_include=["a string no brief contains"],
                                 must_not_include=["John Smith"])
    monkeypatch.setattr(evals, "SCENARIOS", [broken])
    assert evals.main(["--output-dir", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "FAIL  no_agenda" in out
    assert "must include: 'a string no brief contains'" in out and "must not include: 'John Smith'" in out


def test_eval_flags_urls_not_from_search_results():
    s = evals.SCENARIOS[2]
    text = "\n".join(evals.CORE_SECTIONS + s.must_include + s.mocked_only) + "\nhttps://evil.example/x"
    assert evals.check_brief(s, text, live=False) == ["URLs not from search results: ['https://evil.example/x']"]
    assert evals.check_brief(s, text, live=True) == []
