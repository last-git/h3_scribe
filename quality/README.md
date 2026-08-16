# H3 Scribe real-Qwen quality diagnostics

This directory is development-only and excluded from the Comfy Registry package by `.comfyignore`.

The core rule is: **quality runs use normal ComfyUI execution and normal H3 Scribe / Simple Qwen nodes**. The runner does not import `qwen3vl_run.py`, call `run_inference_direct()`, discover GGUF paths itself, or restore the old H3 Studio runtime.

Two human-openable workflows are the topology source of truth:

```text
quality/workflows/composer_quality.json
quality/workflows/analyze_quality.json
```

## Development environment

The repository uses one uv environment for unit/static tests and quality diagnostics:

```powershell
uv sync
uv run pytest
```

No separate Python environment is required for quality runs.

## Start ComfyUI normally

Start the same ComfyUI installation used for H3 Scribe. It must have H3 Scribe and `ComfyUI_Simple_Qwen3-VL-gguf` loaded. The default API address is `http://127.0.0.1:8188`.

## Composer suite

Composer remains the default suite.

```powershell
uv run python quality/run.py --dry-run
uv run python quality/run.py
```

Q4 comparison / fallback:

```powershell
uv run python quality/run.py `
  --model Qwen3.6-27B-Uncensored-HauhauCS-Balanced-Q4_K_P.gguf
```

Open `quality/workflows/composer_quality.json` in ComfyUI to inspect the exact graph:

```text
Authoring -> H3 Compose -> Simple Qwen -> RAW QWEN OUTPUT
    |                                  |
    +----------> Validate & Render ----+-> FINAL H3 PROMPT
```

The runner executes to RAW first, then feeds the captured text into production `H3Scribe_ValidateAndRender` in a second cheap Comfy prompt. Qwen is not rerun during validation.

## Analyze suite

Analyze uses the restored old H3 Studio quality fixtures:

```text
quality/fixtures/builder_quality/two_silver_black.png
quality/fixtures/builder_quality/single_brown.png
```

Dry-run:

```powershell
uv run python quality/run.py --suite analyze --dry-run
```

Q3 default:

```powershell
uv run python quality/run.py --suite analyze
```

Q4 comparison / fallback:

```powershell
uv run python quality/run.py --suite analyze `
  --model Qwen3.6-27B-Uncensored-HauhauCS-Balanced-Q4_K_P.gguf
```

Open `quality/workflows/analyze_quality.json` in ComfyUI to inspect the exact graph. It contains separate Initial and Cast branches:

```text
fixture PNG -> LoadImage -> H3 Initial/Cast -> Simple Qwen -> RAW QWEN OUTPUT
                                                        |
                                                        v
                                     H3 Canonicalize References
                                                        |
                                                        v
                                           H3 Authoring Editor
```

For real runs, `quality/run.py` uploads each fixed PNG to ComfyUI's normal `/upload/image` endpoint and replaces only the corresponding `LoadImage.image` value. It first runs to RAW Qwen output. A second cheap Comfy prompt replaces the linked canonicalizer input with that captured JSON, which prunes LoadImage/Qwen from the closure and exercises production canonicalization + Authoring bootstrap without a second inference.

Analyze sentinels cover the historical image-quality regressions:

- Initial fixture: exactly two visible subjects in stable order; silver/white hair + star accessory for the left subject; black/dark hair + glasses for the right subject; both local aliases present in `initial_ja`; non-empty style.
- Cast fixture: brown/reddish hair + crescent/moon accessory; no pose/scene leakage into Appearance.
- Production canonicalization: local `subject_N` aliases become `<Subject N>`; Initial/Cast picture provenance is preserved; Appearance/style are not rewritten.

## Model/runtime configuration

Model/mmproj values go through `H3Scribe_QwenModelSelector`, so ComfyUI resolves the actual registered files. `--qwen-mode auto` prefers Simple Qwen's native `keep_vram` mode when available.

Both quality workflows pin only the workflow-level `chat_handler=qwen35` setting. H3's production request nodes apply the current H3-owned deterministic settings (JSON response, thinking disabled, seed 0, etc.). Installed node schemas/defaults are read from ComfyUI `/object_info/...`; the quality runner does not keep a second Simple Qwen runtime configuration.

## Reports

Every report records:

- exact workflow path + SHA256
- model/mmproj + inferred quantization label
- raw Qwen output
- parsed structured output when valid
- production downstream output (Final H3 Prompt or analyzed Authoring)
- semantic sentinel PASS/FAIL
- inference/downstream wall time
- best-effort NVIDIA memory baseline/peak from `nvidia-smi`

Token counts and decode tok/s are intentionally not scraped from Simple Qwen internals. Use normal Comfy/Simple Qwen console logs until upstream exposes those as node/API outputs.
