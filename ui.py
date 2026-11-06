"""GUI for Local Code Sanitizer.

Wraps ``local_sanitizer.py`` into a desktop window (CustomTkinter) and streams
the CLI output into the console pane.

Design notes:
  * All paths (CLI script, model, prompts) are resolved from the project folder,
    not from the current working directory, so the GUI can be started from a
    shortcut or from anywhere else.
  * On first launch the GUI makes sure it runs inside the project virtual
    environment (``.sanitizer_venv``); otherwise ``local_sanitizer.py`` would
    re-exec itself in a child process, which breaks the Stop button and always
    reports exit code 0.
"""

import os
import sys
import subprocess
import threading
from pathlib import Path

APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
FROZEN = bool(getattr(sys, "frozen", False))
VENV_DIR = APP_DIR / ".sanitizer_venv"

#: Modules the GUI and the CLI need; checked before the window is created.
REQUIRED_MODULES = ("customtkinter", "llama_cpp", "git", "rich", "pydantic", "yaml")

#: Import name -> pip package name.
PIP_NAMES = {
    "customtkinter": "customtkinter",
    "llama_cpp": "llama-cpp-python",
    "git": "gitpython",
    "rich": "rich",
    "pydantic": "pydantic",
    "yaml": "pyyaml",
}

DEFAULT_MODEL = "model/gemma-4-E2B-it-Q4_K_M.gguf"
DEFAULT_OUTPUT = "sanitized_output"


def resource_path(relative_path: str) -> str:
    """Absolute path to a bundled/project file, independent of the cwd."""
    return str(APP_DIR / relative_path)


def _venv_python() -> Path:
    return VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _missing_modules(python: Path) -> list[str]:
    names = ", ".join(repr(name) for name in REQUIRED_MODULES)
    script = (
        "import importlib\n"
        f"for name in ({names},):\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "    except ImportError:\n"
        "        print(name)\n"
    )
    result = subprocess.run([str(python), "-c", script], capture_output=True, text=True)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _message(text: str) -> None:
    """Show a message even before Tk exists (no console in a windowed run)."""
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, text, "Local Code Sanitizer", 0x40)
            return
        except Exception:
            pass
    print(text, file=sys.stderr)


def _bootstrap_environment() -> None:
    """Run the GUI inside the project venv so the CLI child stays controllable."""
    if FROZEN or sys.prefix != sys.base_prefix:
        return

    python = _venv_python()

    if not python.exists():
        _message(
            "Первый запуск: создаю виртуальное окружение .sanitizer_venv.\n"
            "Это займёт несколько минут, окно появится автоматически."
        )
        subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=False)
        if not python.exists():
            _message(
                "Не удалось создать виртуальное окружение .sanitizer_venv.\n\n"
                "Создайте его вручную из папки проекта:\n"
                "  py -3.11 -m venv .sanitizer_venv\n"
                "  .\\.sanitizer_venv\\Scripts\\python.exe -m pip install -r requirements.txt"
            )
            sys.exit(1)

    missing = _missing_modules(python)
    if missing:
        packages = [PIP_NAMES.get(name, name) for name in missing]
        _message(
            "Первый запуск: устанавливаю зависимости в .sanitizer_venv:\n"
            + ", ".join(packages)
            + "\n\nЭто может занять несколько минут, окно появится автоматически."
        )
        env = os.environ.copy()
        if "llama_cpp" in missing:
            env["CMAKE_ARGS"] = "-DGGML_VULKAN=ON"
            env["FORCE_CMAKE"] = "1"
        subprocess.run([str(python), "-m", "pip", "install", "--upgrade", "pip"], check=False, env=env)
        subprocess.run([str(python), "-m", "pip", "install", *packages], check=False, env=env)

        missing = _missing_modules(python)
        if missing:
            _message(
                "Не удалось установить зависимости: "
                + ", ".join(PIP_NAMES.get(name, name) for name in missing)
                + "\n\nУстановите их вручную:\n"
                "  .\\.sanitizer_venv\\Scripts\\python.exe -m pip install -r requirements.txt"
            )
            sys.exit(1)

    # Relaunch the GUI with the venv interpreter: from now on sys.executable is
    # the venv python, so the CLI runs in-process with a real exit code.
    subprocess.Popen([str(python), str(Path(__file__).resolve()), *sys.argv[1:]], cwd=str(APP_DIR))
    sys.exit(0)


if __name__ == "__main__":
    _bootstrap_environment()

import customtkinter as ctk  # noqa: E402
from tkinter import filedialog, messagebox  # noqa: E402

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("dark-blue")


class SanitizerUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Sanitizer")
        self.geometry("1000x700")
        self.minsize(900, 650)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.model_path = ctk.StringVar(value=str(APP_DIR / DEFAULT_MODEL))
        self.repo_path = ctk.StringVar()
        self.output_dir = ctk.StringVar(value=str(APP_DIR / DEFAULT_OUTPUT))
        self.context_size = ctk.StringVar(value="10000")
        self.gpu_layers = ctk.IntVar(value=42)

        self.cat_scan = ctk.BooleanVar(value=True)
        self.cat_protocol = ctk.BooleanVar(value=True)
        self.cat_algorithm = ctk.BooleanVar(value=True)

        self.flag_remove = ctk.BooleanVar(value=False)
        self.flag_clean = ctk.BooleanVar(value=False)

        self.process = None

        self._build_sidebar()
        self._build_main_area()

    def _build_sidebar(self):
        self.sidebar_frame = ctk.CTkFrame(self, width=250, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(8, weight=1)

        self.logo_label = ctk.CTkLabel(self.sidebar_frame, text="🛡️Sanitizer", font=ctk.CTkFont(size=20, weight="bold"))
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 30))

        ctk.CTkLabel(self.sidebar_frame, text="Модули сканирования:", anchor="w", font=ctk.CTkFont(weight="bold")).grid(row=1, column=0, padx=20, pady=(0, 10), sticky="w")

        self.sw_scan = ctk.CTkSwitch(self.sidebar_frame, text="Секреты и токены", variable=self.cat_scan)
        self.sw_scan.grid(row=2, column=0, padx=20, pady=10, sticky="w")

        self.sw_protocol = ctk.CTkSwitch(self.sidebar_frame, text="Аппаратные протоколы", variable=self.cat_protocol)
        self.sw_protocol.grid(row=3, column=0, padx=20, pady=10, sticky="w")

        self.sw_algo = ctk.CTkSwitch(self.sidebar_frame, text="Алгоритмы (Proprietary)", variable=self.cat_algorithm)
        self.sw_algo.grid(row=4, column=0, padx=20, pady=10, sticky="w")

        ctk.CTkLabel(self.sidebar_frame, text="Поведение:", anchor="w", font=ctk.CTkFont(weight="bold")).grid(row=5, column=0, padx=20, pady=(20, 10), sticky="w")

        self.sw_clean = ctk.CTkSwitch(self.sidebar_frame, text="--clean (только найденное)", variable=self.flag_clean, command=self._on_clean_toggle)
        self.sw_clean.grid(row=6, column=0, padx=20, pady=10, sticky="w")

        self.sw_remove = ctk.CTkSwitch(self.sidebar_frame, text="--remove (удалить всё)", variable=self.flag_remove, command=self._on_remove_toggle)
        self.sw_remove.grid(row=7, column=0, padx=20, pady=10, sticky="w")

        self.btn_run = ctk.CTkButton(self.sidebar_frame, text="▶ ЗАПУСТИТЬ", height=40, font=ctk.CTkFont(weight="bold"), command=self.start_scan)
        self.btn_run.grid(row=9, column=0, padx=20, pady=(10, 10), sticky="ew")

        self.btn_stop = ctk.CTkButton(self.sidebar_frame, text="ОСТАНОВИТЬ", height=40, fg_color="transparent", border_width=2, text_color=("gray10", "#DCE4EE"), state="disabled", command=self.stop_scan)
        self.btn_stop.grid(row=10, column=0, padx=20, pady=(0, 20), sticky="ew")

    def _build_main_area(self):
        self.main_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_frame.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        self.main_frame.grid_rowconfigure(2, weight=1)
        self.main_frame.grid_columnconfigure(0, weight=1)

        paths_frame = ctk.CTkFrame(self.main_frame)
        paths_frame.grid(row=0, column=0, sticky="ew", pady=(0, 20))
        paths_frame.grid_columnconfigure(1, weight=1)

        self._create_path_row(paths_frame, "Файл модели (.gguf):", self.model_path, self._br_model, 0)
        self._create_path_row(paths_frame, "Репозиторий:", self.repo_path, self._br_repo, 1)
        self._create_path_row(paths_frame, "Вывод:", self.output_dir, self._br_output, 2)

        llm_frame = ctk.CTkFrame(self.main_frame)
        llm_frame.grid(row=1, column=0, sticky="ew", pady=(0, 20))
        llm_frame.grid_columnconfigure((0, 1), weight=1)

        ctx_frame = ctk.CTkFrame(llm_frame, fg_color="transparent")
        ctx_frame.grid(row=0, column=0, padx=20, pady=20, sticky="ew")
        ctk.CTkLabel(ctx_frame, text="Размер контекста:").pack(anchor="w")
        self.ctx_entry = ctk.CTkEntry(ctx_frame, textvariable=self.context_size)
        self.ctx_entry.pack(fill="x", pady=(5, 0))

        gpu_frame = ctk.CTkFrame(llm_frame, fg_color="transparent")
        gpu_frame.grid(row=0, column=1, padx=20, pady=20, sticky="ew")
        self.gpu_label = ctk.CTkLabel(gpu_frame, text=f"GPU Слои: {self.gpu_layers.get()}")
        self.gpu_label.pack(anchor="w")
        self.gpu_slider = ctk.CTkSlider(gpu_frame, from_=-1, to=100, variable=self.gpu_layers, command=self._update_gpu_label)
        self.gpu_slider.pack(fill="x", pady=(10, 0))

        self.console = ctk.CTkTextbox(self.main_frame, font=ctk.CTkFont(family="Consolas", size=12))
        self.console.grid(row=2, column=0, sticky="nsew")
        self.console.insert("0.0", "Система готова к работе.")

    def _create_path_row(self, parent, label_text, variable, command, row):
        ctk.CTkLabel(parent, text=label_text).grid(row=row, column=0, padx=20, pady=(15 if row == 0 else 10, 5),
                                                   sticky="w")
        entry = ctk.CTkEntry(parent, textvariable=variable, height=35)
        entry.grid(row=row, column=1, padx=(0, 10), pady=(15 if row == 0 else 10, 5), sticky="ew")
        btn = ctk.CTkButton(parent, text="Выбрать", width=80, height=35, command=command)
        btn.grid(row=row, column=2, padx=(0, 20), pady=(15 if row == 0 else 10, 5))

    def _update_gpu_label(self, value):
        val = int(value)
        text = "MAX (Все слои)" if val == -1 else ("CPU Only" if val == 0 else str(val))
        self.gpu_label.configure(text=f"GPU Слои: {text}")

    def _on_remove_toggle(self):
        if self.flag_remove.get():
            self.flag_clean.set(False)

    def _on_clean_toggle(self):
        if self.flag_clean.get():
            self.flag_remove.set(False)

    def _br_model(self):
        path = filedialog.askopenfilename(filetypes=[("GGUF Models", "*.gguf"), ("All Files", "*.*")])
        if path:
            self.model_path.set(path)

    def _br_repo(self):
        path = filedialog.askdirectory()
        if path:
            self.repo_path.set(path)

    def _br_output(self):
        path = filedialog.askdirectory()
        if path:
            self.output_dir.set(path)

    def start_scan(self):
        cli_path = resource_path("local_sanitizer.py")

        if not os.path.isfile(cli_path):
            messagebox.showerror("Ошибка", f"Не найден local_sanitizer.py:\n{cli_path}")
            return
        if not os.path.isfile(self.model_path.get()):
            messagebox.showerror(
                "Ошибка",
                f"Модель не найдена:\n{self.model_path.get()}\n\n"
                "Скачайте её (см. раздел «Скачивание модели» в README) "
                "или выберите другой .gguf.",
            )
            return
        if not os.path.isdir(self.repo_path.get()):
            messagebox.showerror("Ошибка", "Репозиторий не найден!")
            return

        if not self.context_size.get().strip().isdigit():
            messagebox.showerror("Ошибка", "Размер контекста должен содержать только цифры!")
            return

        cmd = [sys.executable, "-X", "utf8", cli_path]
        cmd.extend(["--model_path", self.model_path.get()])
        cmd.extend(["--repo_path", self.repo_path.get()])
        cmd.extend(["--output_dir", self.output_dir.get()])
        cmd.extend(["--context_size", self.context_size.get().strip()])
        cmd.extend(["--gpu_layers", str(int(self.gpu_layers.get()))])

        cats = []
        if self.cat_scan.get():
            cats.append("scan")
        if self.cat_protocol.get():
            cats.append("protocol")
        if self.cat_algorithm.get():
            cats.append("algorithm")
        if cats:
            cmd.extend(["--categories"] + cats)

        if self.flag_remove.get():
            cmd.append("--remove")
        if self.flag_clean.get():
            cmd.append("--clean")

        self.console.delete("0.0", "end")
        self.append_log(f"> ЗАПУСК...\n> {' '.join(cmd)}\n{'-' * 60}\n")

        self.btn_run.configure(state="disabled")
        self.btn_stop.configure(state="normal")

        self.thread = threading.Thread(target=self._run_subprocess, args=(cmd,), daemon=True)
        self.thread.start()

    def _run_subprocess(self, cmd):
        try:
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"

            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, encoding="utf-8", errors="replace", env=env,
                cwd=str(APP_DIR),
            )

            for line in self.process.stdout:
                self.after(0, self.append_log, line)

            self.process.wait()
            self.after(0, self.append_log, f"\n{'-' * 60}\n> Процесс завершен (Код: {self.process.returncode})\n")

        except Exception as e:
            self.after(0, self.append_log, f"\n> ОШИБКА: {e}\n")
        finally:
            self.after(0, self._reset_ui)

    def stop_scan(self):
        if not self.process or self.process.poll() is not None:
            return
        try:
            if os.name == "nt":
                # Kill the whole tree: the CLI spawns its own children.
                subprocess.run(
                    ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                self.process.terminate()
            self.append_log("\n> [!] Остановлен пользователем.\n")
        except Exception as e:
            self.append_log(f"\n> Не удалось остановить процесс: {e}\n")

    def append_log(self, text):
        self.console.insert("end", text)
        self.console.see("end")

    def _reset_ui(self):
        self.btn_run.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.process = None


if __name__ == "__main__":
    app = SanitizerUI()
    app.mainloop()
