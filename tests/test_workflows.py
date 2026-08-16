import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "example_workflows"
WORKFLOWS = [
    "H3_I2VA_UI.json",
    "H3_Ref2VA_InitialOnly_0Cast_UI.json",
    "H3_Ref2VA_InitialPlus_1-N_Casts_UI.json",
    "H3_Ref2VA_CastsOnly_1-N_UI.json",
]
SUBGRAPH_NAMES = ["Analyze", "Compose", "Example MiniMax H3 Generation"]


def _load(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def _root_nodes(data):
    return {node["id"]: node for node in data["nodes"]}


def _subgraphs(data):
    return data.get("definitions", {}).get("subgraphs", [])


def _subgraph(data, name):
    matches = [sg for sg in _subgraphs(data) if sg["name"] == name]
    assert len(matches) == 1, (name, [sg["name"] for sg in _subgraphs(data)])
    return matches[0]


def _all_nodes(data):
    nodes = list(data["nodes"])
    for sg in _subgraphs(data):
        nodes.extend(sg["nodes"])
    return nodes


def _node(data, *, type=None, title=None, root_only=False):
    pool = data["nodes"] if root_only else _all_nodes(data)
    matches = [
        node for node in pool
        if (type is None or node["type"] == type)
        and (title is None or node.get("title") == title)
    ]
    assert len(matches) == 1, (type, title, [(n["id"], n["type"], n.get("title")) for n in matches])
    return matches[0]


def _sg_node(sg, *, type=None, title=None):
    matches = [
        node for node in sg["nodes"]
        if (type is None or node["type"] == type)
        and (title is None or node.get("title") == title)
    ]
    assert len(matches) == 1, (sg["name"], type, title)
    return matches[0]


def _host(data, sg):
    return _node(data, type=sg["id"], root_only=True)


def _input(node, name):
    return next(item for item in node.get("inputs", []) if item["name"] == name)


def _sg_input(sg, name):
    return next(item for item in sg["inputs"] if item["name"] == name)


def _root_linked_source(data, node, input_name):
    link_id = _input(node, input_name).get("link")
    assert link_id is not None, (node["id"], input_name)
    link = next(link for link in data["links"] if link[0] == link_id)
    return _root_nodes(data)[link[1]], link[2]


def _root_ancestors(data, node_id):
    reverse = {}
    for _, source, _, target, _, _ in data["links"]:
        reverse.setdefault(target, set()).add(source)
    out = set()
    stack = list(reverse.get(node_id, ()))
    while stack:
        current = stack.pop()
        if current in out:
            continue
        out.add(current)
        stack.extend(reverse.get(current, ()))
    return out


def _assert_root_links(data):
    nodes = _root_nodes(data)
    for link_id, source, source_slot, target, target_slot, link_type in data["links"]:
        assert source in nodes and target in nodes
        src = nodes[source]
        dst = nodes[target]
        assert link_id in (src["outputs"][source_slot].get("links") or [])
        assert dst["inputs"][target_slot].get("link") == link_id
        assert src["outputs"][source_slot]["type"] in {link_type, "*"}
        assert dst["inputs"][target_slot]["type"] in {link_type, "*"}


def _assert_subgraph_links(sg):
    nodes = {node["id"]: node for node in sg["nodes"]}
    inputs = sg["inputs"]
    outputs = sg["outputs"]
    link_ids = {link["id"] for link in sg["links"]}

    for link in sg["links"]:
        lid = link["id"]
        src = link["origin_id"]
        ss = link["origin_slot"]
        dst = link["target_id"]
        ts = link["target_slot"]
        typ = link["type"]

        if src == -10:
            assert 0 <= ss < len(inputs)
            assert lid in inputs[ss]["linkIds"]
            assert inputs[ss]["type"] in {typ, "*"}
        else:
            assert src in nodes
            assert lid in (nodes[src]["outputs"][ss].get("links") or [])
            assert nodes[src]["outputs"][ss]["type"] in {typ, "*"}

        if dst == -20:
            assert 0 <= ts < len(outputs)
            assert lid in outputs[ts]["linkIds"]
            assert outputs[ts]["type"] in {typ, "*"}
        else:
            assert dst in nodes
            assert nodes[dst]["inputs"][ts].get("link") == lid
            assert nodes[dst]["inputs"][ts]["type"] in {typ, "*"}

    assert {lid for item in inputs for lid in item["linkIds"]} <= link_ids
    assert {lid for item in outputs for lid in item["linkIds"]} <= link_ids


def test_native_subgraphs_and_all_links_are_structurally_consistent():
    for name in WORKFLOWS:
        data = _load(name)
        uuid.UUID(data["id"])
        assert [sg["name"] for sg in _subgraphs(data)] == SUBGRAPH_NAMES
        for sg in _subgraphs(data):
            uuid.UUID(sg["id"])

        assert data["extra"]["h3NaturalFullRun"] is True
        assert data["extra"]["h3EditorsArePhaseOutputs"] is True
        assert data["extra"]["h3NativePhaseButtons"] is True
        assert data["extra"]["h3NativeGenerationSubgraph"] is True
        assert data["extra"]["h3CoreOnlyResolution"] is True

        all_node_ids = [node["id"] for node in _all_nodes(data)]
        assert len(all_node_ids) == len(set(all_node_ids))
        assert data["last_node_id"] == max(all_node_ids)

        all_link_ids = [link[0] for link in data["links"]]
        for sg in _subgraphs(data):
            all_link_ids.extend(link["id"] for link in sg["links"])
        assert len(all_link_ids) == len(set(all_link_ids))
        assert data["last_link_id"] == max(all_link_ids)

        _assert_root_links(data)
        for sg in _subgraphs(data):
            _assert_subgraph_links(sg)
            assert sg["inputNode"]["id"] == -10
            assert sg["outputNode"]["id"] == -20
            assert sg["state"]["lastNodeId"] == data["last_node_id"]
            assert sg["state"]["lastLinkId"] == data["last_link_id"]
            host = _host(data, sg)
            assert [(x["name"], x["type"]) for x in host.get("inputs", [])] == [
                (x["name"], x["type"]) for x in sg["inputs"]
            ]
            assert [(x["name"], x["type"]) for x in host.get("outputs", [])] == [
                (x["name"], x["type"]) for x in sg["outputs"]
            ]


def test_workflows_have_no_machine_local_paths_fixture_residue_or_dasiwa():
    for name in WORKFLOWS:
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "D:" + chr(92) not in text
        assert "C:" + chr(92) not in text
        assert "single_brown.png" not in text
        assert "two_silver_black.png" not in text
        assert "DaSiWa" not in text
        assert "H3Scribe_AnalyzeOutput" not in text
        assert "H3Scribe_ComposeOutput" not in text


def test_qwen_runtime_details_stay_hidden_inside_analyze_and_compose():
    expected_runtime = [
        "", "", 8192, 512, 512, -1, 0, 8, True, False, True,
        "qwen35", "none", False, False, False, False, "F16", "F16",
    ]
    for name in WORKFLOWS:
        data = _load(name)
        selector = _node(data, type="H3Scribe_QwenModelSelector", root_only=True)
        assert selector["widgets_values"] == [
            "Qwen3.6-27B-Uncensored-HauhauCS-Balanced-Q3_K_P.gguf",
            "mmproj-Qwen3.6-27B-Uncensored-HauhauCS-Balanced-f16.gguf",
        ]
        assert not any(node["type"] == "Qwen3VL_ModelConfig" for node in data["nodes"])

        for phase_name in ["Analyze", "Compose"]:
            sg = _subgraph(data, phase_name)
            base = _sg_node(sg, type="Qwen3VL_ModelConfig")
            assert base["widgets_values"] == expected_runtime
            host = _host(data, sg)
            source, _ = _root_linked_source(data, host, "config_override")
            assert source["id"] == selector["id"]


def test_editors_are_the_root_phase_outputs_and_keep_natural_dependency_chain():
    for name in WORKFLOWS:
        data = _load(name)
        authoring = _node(data, type="H3Scribe_AuthoringEditor", root_only=True)
        final = _node(data, type="H3Scribe_TextEditor", root_only=True)
        analyze_host = _host(data, _subgraph(data, "Analyze"))
        compose_host = _host(data, _subgraph(data, "Compose"))

        assert _root_linked_source(data, authoring, "source")[0]["id"] == analyze_host["id"]
        assert _root_linked_source(data, compose_host, "authoring_json")[0]["id"] == authoring["id"]
        assert _root_linked_source(data, final, "source")[0]["id"] == compose_host["id"]
        assert _input(authoring, "authoring_json")["link"] is None
        assert _input(authoring, "source_snapshot")["link"] is None
        assert _input(final, "text")["link"] is None
        assert _input(final, "source_snapshot")["link"] is None
        assert authoring["title"].startswith("① ANALYZE / AUTHORING")
        assert final["title"].startswith("② COMPOSE / FINAL H3 PROMPT")


def test_extract_qwen_uses_keep_vram_and_compose_remains_isolated_subprocess():
    for name in WORKFLOWS:
        data = _load(name)
        analyze = _subgraph(data, "Analyze")
        compose = _subgraph(data, "Compose")
        extract_qwen = [node for node in analyze["nodes"] if node["type"] == "SimpleQwenVLggufV2"]
        assert extract_qwen
        for node in extract_qwen:
            assert node["widgets_values"] == ["None", "None", "", 0, "fixed", True, "keep_vram"]
        compose_qwen = _sg_node(compose, title="Compose Qwen")
        assert compose_qwen["widgets_values"] == ["None", "None", "", 0, "fixed", True, "subprocess"]
        assert _sg_node(analyze, type="SimpleQwenUnload")["widgets_values"] == ["keep_vram"]


def test_ref2va_one_to_n_casts_use_core_create_list_for_analysis_and_matching_generation_sockets():
    for name in [
        "H3_Ref2VA_InitialPlus_1-N_Casts_UI.json",
        "H3_Ref2VA_CastsOnly_1-N_UI.json",
    ]:
        data = _load(name)
        create_list = _node(data, type="CreateList", root_only=True)
        assert [item["name"] for item in create_list["inputs"]] == ["inputs.input0", "inputs.input1"]
        assert _input(create_list, "inputs.input0")["link"] is not None
        assert _input(create_list, "inputs.input1")["link"] is None
        analyze_host = _host(data, _subgraph(data, "Analyze"))
        assert _root_linked_source(data, analyze_host, "cast_images")[0]["id"] == create_list["id"]

        generation = _host(data, _subgraph(data, "Example MiniMax H3 Generation"))
        picture_inputs = [_input(generation, f"picture_{i}") for i in range(1, 10)]
        assert len(picture_inputs) == 9
        if "InitialPlus" in name:
            assert picture_inputs[0]["link"] is not None
            assert picture_inputs[1]["link"] is not None
            assert picture_inputs[2]["link"] is None
        else:
            assert picture_inputs[0]["link"] is not None
            assert picture_inputs[1]["link"] is None


def test_one_to_n_cast_list_sits_inside_analyze_group_left_of_analyze_node():
    for name in [
        "H3_Ref2VA_InitialPlus_1-N_Casts_UI.json",
        "H3_Ref2VA_CastsOnly_1-N_UI.json",
    ]:
        data = _load(name)
        create_list = _node(data, type="CreateList", root_only=True)
        analyze = _host(data, _subgraph(data, "Analyze"))
        groups = {group["title"]: group for group in data["groups"]}
        x, y, w, h = groups["① ANALYZE / AUTHORING"]["bounding"]
        cx, cy = create_list["pos"]
        assert x <= cx <= x + w and y <= cy <= y + h
        assert create_list["pos"][0] < analyze["pos"][0]
        assert create_list["title"] == "Cast images 1–N"


def test_zero_cast_and_cast_only_analyze_semantics_remain_distinct():
    zero = _load("H3_Ref2VA_InitialOnly_0Cast_UI.json")
    assert "CreateList" not in {node["type"] for node in _all_nodes(zero)}
    assert "H3Scribe_CastQwenRequest" not in {node["type"] for node in _all_nodes(zero)}
    canonicalize = _node(zero, type="H3Scribe_CanonicalizeReferences")
    assert _input(canonicalize, "cast_json")["link"] is None

    cast_only = _load("H3_Ref2VA_CastsOnly_1-N_UI.json")
    assert "H3Scribe_InitialQwenRequest" not in {node["type"] for node in _all_nodes(cast_only)}
    canonicalize = _node(cast_only, type="H3Scribe_CanonicalizeReferences")
    assert canonicalize["widgets_values"] == ["ref2va", 0]
    assert _input(canonicalize, "initial_json")["link"] is None


def test_generation_is_one_native_subgraph_plus_root_savevideo_and_uses_final_prompt_directly():
    required_native = {
        "UNETLoader", "CLIPLoader", "VAELoader", "RandomNoise", "KSamplerSelect",
        "BasicScheduler", "BasicGuider", "SamplerCustomAdvanced", "VAEDecode",
        "VAEDecodeAudio", "CreateVideo",
    }
    for name in WORKFLOWS:
        data = _load(name)
        gen = _subgraph(data, "Example MiniMax H3 Generation")
        host = _host(data, gen)
        save = _node(data, type="SaveVideo", root_only=True)
        root_types = {node["type"] for node in data["nodes"]}
        gen_types = {node["type"] for node in gen["nodes"]}

        assert required_native <= gen_types
        assert "MiniMaxH3ImageToVideo" in gen_types or "MiniMaxH3ReferenceToVideo" in gen_types
        assert not (required_native & root_types)
        assert "SaveVideo" in root_types
        assert save["title"] == "▶ GENERATE — Video Output / SaveVideo"
        assert _root_linked_source(data, save, "video")[0]["id"] == host["id"]
        assert _root_linked_source(data, host, "prompt")[0]["type"] == "H3Scribe_TextEditor"

        ancestors = _root_ancestors(data, save["id"])
        roots = _root_nodes(data)
        assert any(roots[x]["type"] == "H3Scribe_AuthoringEditor" for x in ancestors)
        assert any(roots[x]["type"] == "H3Scribe_TextEditor" for x in ancestors)

        assert _sg_node(gen, type="KSamplerSelect")["widgets_values"] == ["res_multistep"]
        assert _sg_node(gen, type="BasicScheduler")["widgets_values"] == ["beta", 20, 1.0]
        assert _sg_node(gen, type="CreateVideo")["widgets_values"][0] == 24


def test_generation_host_exposes_only_meaningful_parameters_and_model_paths():
    for name in WORKFLOWS:
        data = _load(name)
        host = _host(data, _subgraph(data, "Example MiniMax H3 Generation"))
        names = [x["name"] for x in host["inputs"]]
        for required in ["prompt", "megapixels", "length", "seed", "steps", "unet_name", "clip_name", "video_vae", "audio_vae"]:
            assert required in names
        assert "width" not in names and "height" not in names
        assert "scheduler" not in names and "denoise" not in names
        if name == "H3_I2VA_UI.json":
            assert "initial_image" in names
            assert "aspect_ratio" not in names
        else:
            assert "aspect_ratio" in names
            assert "ref_image_size" in names
            assert [n for n in names if n.startswith("picture_")] == [f"picture_{i}" for i in range(1, 10)]


def test_generation_surface_uses_native_proxy_widgets_instead_of_manual_host_values():
    for name in WORKFLOWS:
        data = _load(name)
        gen = _subgraph(data, "Example MiniMax H3 Generation")
        host = _host(data, gen)
        assert host["widgets_values"] == []
        proxy = host["properties"]["proxyWidgets"]
        proxied = {(node_id, widget) for node_id, widget in proxy}

        h3 = next(node for node in gen["nodes"] if node["type"] in {"MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo"})
        noise = _sg_node(gen, type="RandomNoise")
        scheduler = _sg_node(gen, type="BasicScheduler")
        unet = _sg_node(gen, type="UNETLoader")
        clip = _sg_node(gen, type="CLIPLoader")
        vaes = [node for node in gen["nodes"] if node["type"] == "VAELoader"]
        video_vae = next(node for node in vaes if "video VAE" in node.get("title", ""))
        audio_vae = next(node for node in vaes if "audio VAE" in node.get("title", ""))

        assert (str(h3["id"]), "length") in proxied
        assert (str(noise["id"]), "noise_seed") in proxied
        assert (str(noise["id"]), "control_after_generate") in proxied
        assert (str(scheduler["id"]), "steps") in proxied
        assert (str(unet["id"]), "unet_name") in proxied
        assert (str(clip["id"]), "clip_name") in proxied
        assert (str(video_vae["id"]), "vae_name") in proxied
        assert (str(audio_vae["id"]), "vae_name") in proxied

        if name == "H3_I2VA_UI.json":
            scale = _sg_node(gen, type="ImageScaleToTotalPixels")
            assert (str(scale["id"]), "megapixels") in proxied
        else:
            resolution = _sg_node(gen, type="ResolutionSelector")
            assert (str(resolution["id"]), "aspect_ratio") in proxied
            assert (str(resolution["id"]), "megapixels") in proxied
            assert (str(h3["id"]), "ref_image_size") in proxied


def test_i2va_uses_only_core_scale_to_total_pixels_and_derived_size_so_first_frame_is_not_stretched():
    data = _load("H3_I2VA_UI.json")
    initial = _node(data, title="Initial image", root_only=True)
    gen = _subgraph(data, "Example MiniMax H3 Generation")
    host = _host(data, gen)
    scale = _sg_node(gen, type="ImageScaleToTotalPixels")
    get_size = _sg_node(gen, type="GetImageSize")
    h3 = _sg_node(gen, type="MiniMaxH3ImageToVideo")

    assert scale["widgets_values"] == ["lanczos", 0.65, 32]
    assert _root_linked_source(data, host, "initial_image")[0]["id"] == initial["id"]

    # The same scaled image defines both canvas dimensions and first_frame.
    scale_out = set(scale["outputs"][0]["links"])
    size_link = _input(get_size, "image")["link"]
    first_frame_link = _input(h3, "first_frame")["link"]
    assert {size_link, first_frame_link} <= scale_out
    assert _input(h3, "width")["link"] in get_size["outputs"][0]["links"]
    assert _input(h3, "height")["link"] in get_size["outputs"][1]["links"]
    assert not any(node["type"] == "ResizeAndPadImage" for node in _all_nodes(data))


def test_ref2va_uses_core_resolution_selector_and_direct_optional_picture_inputs():
    for name in WORKFLOWS[1:]:
        data = _load(name)
        gen = _subgraph(data, "Example MiniMax H3 Generation")
        resolution = _sg_node(gen, type="ResolutionSelector")
        h3 = _sg_node(gen, type="MiniMaxH3ReferenceToVideo")
        assert resolution["widgets_values"] == ["16:9 (Widescreen)", 0.65, 32]
        assert h3["widgets_values"][-1] == "match"
        refs = [x for x in h3["inputs"] if x["name"].startswith("ref_images.ref_image_")]
        assert len(refs) == 9
        assert all(x.get("shape") == 7 for x in refs)
        assert not any(node["type"] in {"ResizeAndPadImage", "ImageScaleToTotalPixels"} for node in gen["nodes"])


def test_generation_model_pair_matches_mode_inside_single_generation_subgraph():
    i2v = _load("H3_I2VA_UI.json")
    assert _sg_node(_subgraph(i2v, "Example MiniMax H3 Generation"), type="UNETLoader")["widgets_values"][0] == \
        "minimax_h3_fl2va_pruned_int8_convrot.safetensors"

    for name in WORKFLOWS[1:]:
        data = _load(name)
        assert _sg_node(_subgraph(data, "Example MiniMax H3 Generation"), type="UNETLoader")["widgets_values"][0] == \
            "minimax_h3_ref2va_pruned_int8_convrot.safetensors"


def test_horizontal_layout_has_only_the_intended_public_boxes():
    for name in WORKFLOWS:
        data = _load(name)
        groups = {group["title"]: group for group in data.get("groups", [])}
        assert set(groups) == {
            "H3 Scribe — Inputs",
            "① ANALYZE / AUTHORING",
            "② COMPOSE / FINAL PROMPT",
            "Example MiniMax H3 Generation — replace / customize freely",
        }
        authoring = _node(data, type="H3Scribe_AuthoringEditor", root_only=True)
        final = _node(data, type="H3Scribe_TextEditor", root_only=True)
        generation = _host(data, _subgraph(data, "Example MiniMax H3 Generation"))
        assert authoring["pos"][0] < final["pos"][0] < generation["pos"][0]
        assert not any(node.get("title", "").startswith("③") for node in data["nodes"])


def test_frontend_uses_native_litegraph_phase_buttons_and_only_authoring_has_custom_dom_editor():
    source = (Path(__file__).resolve().parents[1] / "web" / "editable_text.js").read_text(encoding="utf-8")
    assert 'node.addWidget("button", label, "", () =>' in source
    assert 'command.execute("Comfy.QueueSelectedOutputNodes")' in source
    assert 'installRunButton(node, "▶ ① ANALYZE")' in source
    assert 'installRunButton(node, "▶ ② COMPOSE")' in source
    assert 'button.computeSize = (width) => [width, 46]' in source
    assert 'widgets.unshift(button)' in source
    assert 'node.addDOMWidget("h3_authoring_form"' in source
    assert 'h3_text_editor' not in source
    assert 'document.createElement("button")' in source  # Authoring Add/Remove Shot controls remain domain UI.
    assert "H3Scribe_AnalyzeOutput" not in source
    assert "H3Scribe_ComposeOutput" not in source


def test_quick_start_note_is_far_left_and_contains_clickable_model_tree():
    required_links = [
        "HauhauCS/Qwen3.6-27B-Uncensored-HauhauCS-Balanced/resolve/main/",
        "Comfy-Org/MiniMax-H3/resolve/main/text_encoders/",
        "Comfy-Org/MiniMax-H3/resolve/main/vae/",
        "KLL535/ComfyUI_Simple_Qwen3-VL-gguf",
    ]
    for name in WORKFLOWS:
        data = _load(name)
        note = _node(data, type="MarkdownNote", root_only=True)
        assert note["pos"][0] < min(node["pos"][0] for node in data["nodes"] if node["id"] != note["id"])
        markdown = note["widgets_values"][0]
        assert "## 使い方" in markdown
        assert "## モデル配置 / Download" in markdown
        assert "画像解析 / Authoringは日本語" in markdown
        assert "[Qwen3.6-27B" in markdown
        for link in required_links:
            assert link in markdown
        if name == "H3_I2VA_UI.json":
            assert "minimax_h3_fl2va_pruned_int8_convrot.safetensors" in markdown
        else:
            assert "minimax_h3_ref2va_pruned_int8_convrot.safetensors" in markdown
