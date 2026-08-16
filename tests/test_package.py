from pathlib import Path


def test_reviewer_is_not_part_of_the_implementation():
    root = Path(__file__).resolve().parents[1]
    implementation = "\n".join(path.read_text(encoding="utf-8") for path in (root / "h3_scribe").glob("*.py")).casefold()
    assert "reviewer" not in implementation
    assert "revised_prompt" not in implementation


def test_prompt_files_are_present():
    root = Path(__file__).resolve().parents[1]
    expected = {
        "builder_appearance_rules.md",
        "builder_extract_initial.md",
        "builder_extract_cast.md",
        "builder_compose_i2va.md",
        "builder_compose_ref2va.md",
    }
    assert {path.name for path in (root / "prompts").glob("*.md")} == expected


def test_authoring_frontend_persists_structured_user_state_in_node_properties():
    root = Path(__file__).resolve().parents[1]
    js = (root / "web" / "editable_text.js").read_text(encoding="utf-8")
    assert 'const PROP_VALUE = "h3_persistent_value"' in js
    assert 'const PROP_SOURCE = "h3_source_snapshot"' in js
    assert 'function installPersistenceHooks' in js
    assert 'node.onSerialize = function (info)' in js
    assert 'node.onConfigure = function (info)' in js
    assert 'node.addDOMWidget("h3_authoring_form"' in js


def test_final_prompt_uses_native_multiline_widget_and_only_persists_source_snapshot_explicitly():
    root = Path(__file__).resolve().parents[1]
    js = (root / "web" / "editable_text.js").read_text(encoding="utf-8")
    section = js[js.index("function installTextEditor"):]
    assert 'const text = widgetByName(node, "text")' in section
    assert 'hideBackingWidget(snapshot)' in section
    assert 'hideBackingWidget(text)' not in section
    assert 'addDOMWidget' not in section
    assert 'setBacking(text, value)' in section


def test_phase_buttons_delegate_to_comfy_partial_execution():
    root = Path(__file__).resolve().parents[1]
    js = (root / "web" / "editable_text.js").read_text(encoding="utf-8")
    assert 'node.addWidget("button", label, "", () =>' in js
    assert 'command.execute("Comfy.QueueSelectedOutputNodes")' in js
    assert 'app.canvas?.selectItems' in js




def test_distribution_tree_has_no_machine_local_or_secret_residue():
    import re

    root = Path(__file__).resolve().parents[1]
    scan_roots = [root / "h3_scribe", root / "web", root / "prompts", root / "workflows"]
    files = [root / "README.md", root / "pyproject.toml", root / "requirements.txt", root / "__init__.py"]
    for scan_root in scan_roots:
        files.extend(
            path for path in scan_root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".py", ".js", ".md", ".txt", ".json"}
        )
    text = "\n".join(path.read_text(encoding="utf-8", errors="strict") for path in files)

    literal_forbidden = [
        "C:" + "\\" + "Users" + "\\",
        "/" + "Users" + "/",
        "/" + "mnt" + "/" + "data" + "/",
        "-----BEGIN " + "PRIVATE KEY-----",
        "-----BEGIN RSA " + "PRIVATE KEY-----",
        "-----BEGIN OPENSSH " + "PRIVATE KEY-----",
    ]
    for value in literal_forbidden:
        assert value not in text, value
    home_pattern = "/" + "home" + r"/[A-Za-z0-9._-]+/"
    assert re.search(home_pattern, text) is None
    assert re.search(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{16,}", text) is None

    assert not (root / "quality").exists()
    assert "H3Scribe_" + "AnalyzeOutput" not in text
    assert "H3Scribe_" + "ComposeOutput" not in text
