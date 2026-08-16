# H3 Scribe

[![Test](https://github.com/last-git/h3_scribe/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/last-git/h3_scribe/actions/workflows/test.yml)

MiniMax H3用の、ComfyUI内で完結するプロンプト作成支援ノードです。
画像をQwenで解析し、編集可能なAuthoringを経由して、MiniMax H3用の最終プロンプトを生成します。

**画像解析結果とAuthoringは日本語で出力・編集します。最終H3 Promptは英語で生成されます。**

## インストール

ComfyUI Managerで `H3 Scribe` を検索してインストールしてください。

依存:
- ComfyUI_Simple_Qwen3-VL-gguf
- 各workflowで使用するモデル

## できること

- **I2VA**: Initial画像を開始フレームとして動画プロンプトを作成
- **Ref2VA**: Initial / Cast画像からReference-to-Video用プロンプトを作成
- Initial + Cast 1-N、Initialのみ、Cast 1-Nのみをサポート
- Subject Appearance / Initial / Style / Throughout / Motion / Camera / Shotsを編集
- Analyzeをやり直しても、ユーザーが書いたMotion / Camera / Throughout / Shotsを保持
- 再AnalyzeでSubject構成が変わっても編集途中では止めず、必要なSubject参照を直してからComposeできます
- Final H3 Promptを自由に編集し、その内容をそのままGenerationへ渡す
- ComfyUI標準のcache / partial executionを利用し、必要な部分だけ再実行
- 置き換え可能なMiniMax H3 generation workflow例を同梱

## できないこと / 対象外

- H3 Scribe自体は動画生成エンジンではありません。Generation部分はComfyUI native MiniMax H3の例です。
- Subjectとして扱うのは人物・人型キャラクターです。一般物体をSubjectとして管理しません。
- Cast画像は1枚につき1人物を前提とします。複数画像間の人物同一性を自動判定しません。
- Qwenのモデルロード・実行・停止はH3 Scribeではなく `ComfyUI_Simple_Qwen3-VL-gguf` が担当します。

## 必要なもの

- MiniMax H3 native nodesを含む新しいComfyUI
- [ComfyUI_Simple_Qwen3-VL-gguf](https://github.com/KLL535/ComfyUI_Simple_Qwen3-VL-gguf)
- このH3 Scribe node pack
- Qwen解析モデル + mmproj
- 使用するworkflowに対応したMiniMax H3モデル

モデルの保存先とダウンロードリンクは、**各workflowの一番左にある Markdown Note** にまとめています。

## 使い方

1. 用途に合うworkflowを開く。
2. Qwen model / mmprojとInitial / Cast画像を選ぶ。
3. **▶ ① ANALYZE** を押す。
4. AuthoringのMotion / Camera / Throughoutなどを必要に応じて編集する。
5. **▶ ② COMPOSE** を押す。
6. Final H3 Promptを確認・必要なら編集する。
7. 右側のExample Generationを実行するか、自分のgeneration graphへ接続する。

### workflowの選び方

- `H3_I2VA_UI.json` — Initial画像を開始フレームとして使う
- `H3_Ref2VA_InitialOnly_0Cast_UI.json` — Initialのみ
- `H3_Ref2VA_InitialPlus_1-N_Casts_UI.json` — Initial + Cast 1-N
- `H3_Ref2VA_CastsOnly_1-N_UI.json` — Cast 1-Nのみ

Castを増やす場合はCast `Load Image`を複製し、`Cast images 1-N` の次の入力と、Generationの次の `Picture` 入力の両方へ接続します。

---

# English

H3 Scribe is a ComfyUI-native authoring layer for MiniMax H3. It analyzes reference images with Qwen, lets you edit the semantic Authoring state, and composes a final MiniMax H3 prompt.

**Image-analysis drafts and Authoring are produced/editable in Japanese. The final H3 prompt is generated in English.**

## Installation

Search for `H3 Scribe` in ComfyUI Manager and install it.

Requirements:
- `ComfyUI_Simple_Qwen3-VL-gguf`
- The models required by each workflow

## What it can do

- **I2VA**: use an Initial image as the opening frame
- **Ref2VA**: author prompts from Initial and/or Cast references
- Support Initial + 1-N Casts, Initial only, and 1-N Casts only
- Edit Subject Appearance / Initial / Style / Throughout / Motion / Camera / Shots
- Re-analyze images while retaining user-authored Motion / Camera / Throughout / Shots
- If re-analysis changes the Subject set, Authoring remains editable so you can repair Subject references before Compose
- Edit the Final H3 Prompt and pass that exact text to generation
- Use ComfyUI's native cache and partial execution
- Include a replaceable native MiniMax H3 generation example

## What it does not do

- H3 Scribe is not a video-generation backend; the bundled generation graph is only an example using ComfyUI's native MiniMax H3 nodes.
- Subjects are people or human-like characters, not arbitrary objects.
- Each Cast image is expected to contain one intended person. H3 Scribe does not perform cross-image identity matching.
- Qwen model loading/runtime is owned by `ComfyUI_Simple_Qwen3-VL-gguf`, not H3 Scribe.

## Requirements

- A recent ComfyUI with native MiniMax H3 nodes
- [ComfyUI_Simple_Qwen3-VL-gguf](https://github.com/KLL535/ComfyUI_Simple_Qwen3-VL-gguf)
- This H3 Scribe node pack
- The Qwen analysis GGUF + mmproj
- The MiniMax H3 model files required by the chosen workflow

Download links and model-folder locations are listed in the **Markdown Note at the far left of every workflow**.

## Usage

1. Open the workflow matching your task.
2. Select the Qwen model/mmproj and Initial/Cast images.
3. Click **▶ ① ANALYZE**.
4. Edit Motion / Camera / Throughout or other Authoring fields as needed.
5. Click **▶ ② COMPOSE**.
6. Review or edit the Final H3 Prompt.
7. Run the example generation graph, or connect the prompt to your own generation workflow.
