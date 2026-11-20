import os
import re
import subprocess
from pathlib import Path
from typing import Optional
from git import RemoteProgress
from rich.progress import (
    Progress,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeElapsedColumn,
    FileSizeColumn,
    TransferSpeedColumn,
    TimeRemainingColumn,
    SpinnerColumn,
)
from loguru import logger

ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
CARGO_PROGRESS_RE = re.compile(r"Building\s*\[\s*=*>*\s*\]\s*(\d+)/(\d+)(?::\s*([^\r\n]+))?")


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


class RichCloneProgress(RemoteProgress):
    def __init__(self):
        super().__init__()
        self.progress = Progress(
            TextColumn("[bold blue]{task.fields[filename]}", justify="right"),
            BarColumn(bar_width=None),
            "[progress.percentage]{task.percentage:>3.1f}%",
            "•",
            FileSizeColumn(),
            "•",
            TransferSpeedColumn(),
            "•",
            TimeRemainingColumn(),
        )
        self.tasks = {}
        self.progress.start()

    def update(self, op_code, cur_count, max_count=None, message=""):
        if not max_count:
            max_count = cur_count

        if op_code & self.BEGIN:
            description = message or "Cloning"
            task_id = self.progress.add_task(
                description, total=float(max_count), filename=description
            )
            self.tasks[op_code & self.OP_MASK] = task_id // 1

        task_id = self.tasks.get(op_code & self.OP_MASK)

        if task_id is not None and max_count:
            self.progress.update(
                task_id, completed=float(cur_count), total=float(max_count)
            )

        if op_code & self.END:
            if task_id is not None:
                self.progress.update(task_id, completed=float(max_count))

    def __call__(self, op_code, cur_count, max_count=None, message=""):
        return self.update(op_code, cur_count, max_count, message)

    def __del__(self):
        if hasattr(self, "progress"):
            self.progress.stop()


def run_process_with_progress(
    cmd: list[str],
    description: str,
    cwd: Optional[Path | str] = None,
    env: dict[str, str] | None = None,
) -> int:
    output_log = []
    process_env = env.copy() if env else os.environ.copy()
    
    # Принуждаем Cargo отдавать прогресс сборки в пайп
    process_env["CARGO_TERM_PROGRESS_WHEN"] = "always"
    process_env["CARGO_TERM_PROGRESS_WIDTH"] = "80"

    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=cwd,
        env=process_env,
    ) as process:
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=35),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TextColumn("[dim]{task.fields[status]}"),
            transient=True,
        ) as progress:
            task = progress.add_task(description, total=None, status="Preparing...")

            if not process.stdout:
                logger.warning("No stdout from process")
                return 1

            while True:
                # Читаем построчно, универсальный перенос строки обрабатывает как \n так и \r от Cargo
                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                if not line:
                    continue

                clean_line = strip_ansi(line).strip()
                if not clean_line:
                    continue

                output_log.append(clean_line)

                # 1. Проверяем процент прогресса компиляции Rust: Building [==> ] 12/80: crate_name
                progress_match = CARGO_PROGRESS_RE.search(clean_line)
                if progress_match:
                    completed = int(progress_match.group(1))
                    total = int(progress_match.group(2))
                    crate = progress_match.group(3) or ""
                    status_text = f"Compiling {crate}".strip() if crate else "Compiling crates..."
                    progress.update(task, total=total, completed=completed, status=status_text)
                    continue

                # 2. Обычные строки Compiling
                if clean_line.startswith("Compiling "):
                    parts = clean_line.split()
                    crate = parts[1] if len(parts) > 1 else ""
                    progress.update(task, status=f"Compiling {crate}")

                # 3. Завершение Rust-сборки и переход к генерации docit
                elif "Finished " in clean_line:
                    progress.update(task, status="Generating documentation...")

                # 4. Вывод ошибок и предупреждений без ломания статус-бара
                elif clean_line.startswith(("error:", "warning:")):
                    progress.console.print(clean_line)
                
                # 5. Этапы docit
                else:
                    progress.update(task, status=clean_line[:50])

    if process.returncode != 0:
        logger.error(f"Command failed with code {process.returncode}")
        print("\n".join(output_log[-30:]))

    return process.returncode


def ensure_directories(paths: list[Path]):
    for d in paths:
        if d.exists():
            continue
        logger.info(f"Creating {d}")
        d.mkdir(parents=True, exist_ok=True)