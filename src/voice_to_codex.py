"""Voice input bridge for the Codex CLI.

This script adds a minimal voice-input workflow on top of the regular Codex CLI
by recording short audio clips, transcribing them with OpenAI's speech-to-text
API, and forwarding the resulting text to an interactive `codex` subprocess.
"""

from __future__ import annotations

import argparse
import io
import os
import signal
import sys
import threading
from dataclasses import dataclass
from typing import Optional, Sequence, cast

import numpy as np
import sounddevice as sd
import soundfile as sf
from dotenv import load_dotenv
from openai import AuthenticationError, OpenAI
from rich.console import Console
from rich.prompt import Prompt

if os.name == "nt":
    try:
        from winpty import PtyProcess, WinptyError
    except ImportError:  # pragma: no cover - handled at runtime
        PtyProcess = None  # type: ignore[assignment]
        WinptyError = Exception  # type: ignore[assignment]
else:
    import pexpect


console = Console()


def _install_ctrl_c_handler(stop_event: threading.Event) -> None:
    """Ensure Ctrl+C stops recording as expected."""

    def handler(signum, _frame):
        stop_event.set()

    try:
        signal.signal(signal.SIGINT, handler)
    except ValueError:
        # Happens on non-main threads; ignore.
        pass


def record_audio(duration: float, sample_rate: int) -> tuple[np.ndarray, int]:
    """Record audio from the system microphone for a fixed duration."""
    frames = int(duration * sample_rate)
    console.print(
        f"[cyan]Recording for {duration:.1f} seconds... speak now (Ctrl+C to cancel)[/cyan]"
    )
    stop_event = threading.Event()
    _install_ctrl_c_handler(stop_event)

    try:
        audio = sd.rec(frames, samplerate=sample_rate, channels=1, dtype="float32")
        sd.wait()
        return audio, sample_rate
    except KeyboardInterrupt:
        console.print("[yellow]Recording cancelled[/yellow]")
        raise
    finally:
        stop_event.set()


def to_wav_buffer(audio: np.ndarray, sample_rate: int) -> io.BytesIO:
    """Convert a NumPy audio buffer to an in-memory WAV file."""
    buffer = io.BytesIO()
    sf.write(buffer, audio, sample_rate, format="wav")
    buffer.seek(0)
    buffer.name = "voice-input.wav"  # type: ignore[attr-defined]
    return buffer


def transcribe_audio(
    client: OpenAI, buffer: io.BytesIO, model: str
) -> Optional[str]:
    """Send audio to OpenAI's speech-to-text API and return the transcript."""
    try:
        response = client.audio.transcriptions.create(model=model, file=buffer)
    except AuthenticationError as exc:
        console.print(
            "[red]OpenAI authentication failed. "
            "Ensure OPENAI_API_KEY is set correctly.[/red]"
        )
        console.print(f"[red]{exc}[/red]")
        return None
    except Exception as exc:  # pragma: no cover
        console.print(f"[red]Transcription failed: {exc}[/red]")
        return None
    text = getattr(response, "text", None)
    if not text:
        console.print("[red]No transcription returned.[/red]")
        return None
    return text.strip()


@dataclass
class CodexBridge:
    """Wrapper around an interactive `codex` CLI using a pseudo-terminal."""

    command: Sequence[str]
    process: Optional[object] = None
    _stdout_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Launch Codex within a PTY so it thinks it is connected to a terminal."""
        if not self.command:
            raise FileNotFoundError("No command provided")

        if os.name == "nt":
            if PtyProcess is None:  # pragma: no cover - runtime guard
                raise RuntimeError(
                    "pywinpty (winpty) is required on Windows. "
                    "Install it via `pip install pywinpty`."
                )
            self.process = PtyProcess.spawn(list(self.command))
        else:
            executable, *args = self.command
            self.process = pexpect.spawn(
                executable,
                args=args,
                encoding="utf-8",
                codec_errors="ignore",
                echo=False,
            )

        self._stdout_thread = threading.Thread(
            target=self._pump_stdout, name="codex-stdout", daemon=True
        )
        self._stdout_thread.start()

    def _pump_stdout(self) -> None:
        assert self.process is not None

        if os.name == "nt":
            proc = cast("PtyProcess", self.process)
            while True:
                try:
                    line = proc.readline()
                except EOFError:
                    break
                if not line:
                    continue
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="ignore")
                console.print(line.rstrip("\r\n"), markup=False, highlight=False)
        else:
            proc = cast("pexpect.spawnbase.SpawnBase", self.process)
            while True:
                try:
                    line = proc.readline()
                except pexpect.EOF:
                    break
                except pexpect.TIMEOUT:
                    continue
                if line:
                    console.print(line.rstrip("\r\n"), markup=False, highlight=False)

        console.print("[magenta]Codex session ended.[/magenta]")

    def send_line(self, text: str) -> None:
        if not self.process:
            raise RuntimeError("Codex process is not running")

        if os.name == "nt":
            proc = cast("PtyProcess", self.process)
            if not proc.isalive():
                raise RuntimeError("Codex process is not running")
            try:
                proc.write(text + "\r\n")
            except WinptyError as exc:
                console.print(f"[red]Failed to send input to Codex: {exc}[/red]")
        else:
            proc = cast("pexpect.spawnbase.SpawnBase", self.process)
            if not proc.isalive():
                raise RuntimeError("Codex process is not running")
            try:
                proc.sendline(text)
            except (pexpect.ExceptionPexpect, OSError) as exc:
                console.print(f"[red]Failed to send input to Codex: {exc}[/red]")

    def terminate(self) -> None:
        if not self.process:
            return

        if os.name == "nt":
            proc = cast("PtyProcess", self.process)
            try:
                proc.terminate(force=True)
            except WinptyError:
                pass
        else:
            proc = cast("pexpect.spawnbase.SpawnBase", self.process)
            try:
                proc.terminate(force=True)
            except pexpect.ExceptionPexpect:
                pass


def interactive_loop(
    bridge: CodexBridge,
    client: OpenAI,
    duration: float,
    sample_rate: int,
    model: str,
    auto_send: bool,
) -> None:
    console.print(
        "[bold green]Voice input bridge is ready.[/bold green] "
        "Press Enter to record voice, type text to send manually, or /quit to exit."
    )
    while True:
        try:
            user_input = Prompt.ask(
                "[bold blue]Input[/bold blue]",
                default="",
                show_default=False,
            ).strip()
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted. Shutting down.[/yellow]")
            break

        if user_input.lower() in {"/quit", "/exit"}:
            break

        if user_input:
            bridge.send_line(user_input)
            continue

        try:
            audio, sr = record_audio(duration, sample_rate)
        except KeyboardInterrupt:
            continue

        buffer = to_wav_buffer(audio, sr)
        transcription = transcribe_audio(client, buffer, model)
        if not transcription:
            continue

        console.print(f"[green]Transcribed:[/green] {transcription}")
        if not auto_send:
            decision = Prompt.ask(
                "Send transcription? (y/n/edit)",
                choices=["y", "n", "edit"],
                default="y",
            )
            if decision == "n":
                continue
            if decision == "edit":
                transcription = Prompt.ask("Edit text", default=transcription)

        bridge.send_line(transcription)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add voice input to the Codex CLI via OpenAI speech-to-text."
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=float(os.getenv("VOICE_INPUT_DURATION", 8)),
        help="Recording duration in seconds (default: 8).",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=16000,
        help="Sample rate for microphone recording (default: 16000).",
    )
    parser.add_argument(
        "--model",
        default="gpt-4o-mini-transcribe",
        help="OpenAI transcription model to use (default: gpt-4o-mini-transcribe).",
    )
    parser.add_argument(
        "--auto-send",
        action="store_true",
        help="Send transcripts without asking for confirmation.",
    )
    parser.add_argument(
        "--codex-cmd",
        nargs=argparse.REMAINDER,
        default=["codex"],
        help="Command used to launch Codex (default: `codex`). Everything "
        "after `--codex-cmd` is passed as-is.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    load_dotenv()
    args = parse_args(argv)

    if not args.codex_cmd:
        console.print("[red]No Codex command provided.[/red]")
        return 1

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        console.print(
            "[red]OPENAI_API_KEY is not set. "
            "Set it in your environment or a .env file before running.[/red]"
        )
        return 1

    client = OpenAI(api_key=api_key)

    bridge = CodexBridge(command=args.codex_cmd)
    try:
        bridge.start()
    except FileNotFoundError:
        console.print(
            f"[red]Failed to launch Codex command: {' '.join(args.codex_cmd)}[/red]"
        )
        return 1

    try:
        interactive_loop(
            bridge=bridge,
            client=client,
            duration=args.duration,
            sample_rate=args.sample_rate,
            model=args.model,
            auto_send=args.auto_send,
        )
    finally:
        bridge.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
