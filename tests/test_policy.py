from screenkaffee.policy import load_policy, apply_policy


def test_policy(tmp_path):
    f = tmp_path / "policy.json"
    f.write_text('{"locked": {"capture_cursor": true}, "disabled": ["settings_import"]}', encoding="utf-8")
    policy = load_policy(f)
    assert apply_policy({"capture_cursor": False, "x": 1}, policy) == {"capture_cursor": True, "x": 1}
    assert policy["disabled"] == ["settings_import"]
    assert load_policy(tmp_path / "none.json") == {"locked": {}, "disabled": []}
    f.write_text("kaputt", encoding="utf-8")
    assert load_policy(f)["locked"] == {}
