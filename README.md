# Codex Voice Input Bridge

このリポジトリは、Codex 5 CLI (VS Code からの利用を含む) に音声入力ワークフローを追加する試験実装です。Codex CLI 自体には公式な音声入力機能がないため、マイクから録音した音声を OpenAI の音声認識 API で文字起こしし、その結果を `codex` の対話セッションへ転送します。

## 機能概要
- 音声入力 → OpenAI 音声認識 (`gpt-4o-mini-transcribe`) でテキスト化
- 文字起こし結果を確認・編集したうえで Codex CLI に送信
- 手入力と音声入力を同じループで切り替え可能
- `CodexBridge` が背後で `codex` サブプロセスを起動し、対話の出力をストリーミング表示

## 事前準備
- Python 3.10 以降 (開発環境では 3.13 で検証)
- Codex CLI がインストール済み (`codex --help` で確認)
- マイクが接続された Windows / macOS / Linux 環境
- OpenAI API キー (`OPENAI_API_KEY`)

## セットアップ
1. 仮想環境を作成 (任意):
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
2. 依存ライブラリをインストール:
   ```powershell
   pip install -r requirements.txt
   ```
3. `.env.example` をコピーして OpenAI の API キーなどを設定:
   ```powershell
   Copy-Item .env.example .env
   # .env を編集して OPENAI_API_KEY をセット
   ```

## 使い方
```powershell
python -m src.voice_to_codex
```

- Enter キーだけ押すと録音を開始 (既定 8 秒)。音声入力を文字起こし後、送信前に確認.
- テキストを直接入力すると、そのまま Codex に送信.
- `--auto-send` で確認を省略, `--duration` で録音秒数を変更.
- `--codex-cmd -- codex --model o3` のように指定すると、Codex の起動オプションをカスタマイズ可能.
- Codex CLI が PATH に無い場合は `--codex-cmd "C:\Users\<you>\.vscode\extensions\...\codex.exe"` のようにフルパスを指定するか、PowerShell で `$env:Path += ";<codex.exe のあるフォルダ>"` を実行してから起動してください。

### VS Code からの利用例
1. VS Code のターミナルで仮想環境を有効化
2. 上記コマンドを実行
3. Codex から実行コマンドの承認が求められた場合は、プロンプトに手入力で応答

## 仕組み
1. `sounddevice` + `soundfile` でマイク入力を WAV に変換
2. `openai` SDK 経由で `client.audio.transcriptions.create()` を呼び出しテキスト化
3. 対応する文字列を `CodexBridge` が起動中の `codex` プロセスへ書き込み
4. Codex からの標準出力を別スレッドで受け取り、そのままコンソールへ出力

## GitHub リポジトリの作成
ローカルで `git init` 済みです。GitHub に公開する場合は、以下いずれかでリモートを追加してください。

1. GitHub CLI (`gh`) を利用:
   ```powershell
   gh auth login
   gh repo create <your-account>/voiceinputting --source . --public
   git push -u origin main
   ```
2. ブラウザから空のリポジトリを作成し、表示される手順で `git remote add origin ...` → `git push -u origin main`

## 今後の拡張アイデア
- 録音の開始/停止をホットキーや音声検出で自動化
- OpenAI 以外のローカル音声認識エンジン (Whisper.cpp など) のサポート
- VS Code の `tasks.json` や拡張機能経由でのキーバインド
- 音声 → Codex → 音声応答 (Text-to-Speech) まで含めた完全な対話

## 現状の制約と残作業
- **Windows ネイティブ CLI** は `stdout is not a terminal` で終了するため、WSL2 上で Codex を動かす前提を整える必要があります。
- スクリプト側で `--codex-cmd wsl -e codex` など WSL 経由の起動を自動化する処理を追加する。
- OpenAI API エラーやネットワーク断に対するリトライ／リカバリ処理を強化する。
- GitHub リモートを作成し、`git push -u origin main` で進捗を共有する。
- 使い方や制約の英語版ドキュメント、ライセンスの整備。

## ライセンス
現時点では未指定。公開する場合は用途に合わせて `LICENSE` を追加してください。
