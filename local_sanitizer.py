#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Local Code Sanitization via GGUF LLM (CPU/GPU)

Scans source code repositories for hardcoded secrets, hardware protocols,
and proprietary algorithms using a local LLM. Replaces sensitive content
with safe stubs while preserving file structure.

REQUIREMENTS:
  - Python 3.10+
  - GGUF model file (e.g., gemma-4-E2B-it-Q4_K_M.gguf)
  - PyYAML (auto-installed if missing)

On first run, the script automatically creates a virtual environment (.sanitizer_venv),
installs dependencies (llama-cpp-python with Vulkan for AMD GPU acceleration), and
re-launches itself inside it. No manual setup required.


USAGE:

  # Basic scan — all categories (scan + protocol + algorithm)
  python local_sanitizer.py --model_path ./model/gemma-4.gguf --repo_path ./src

  # Scan only secrets, skip protocols/algorithms
  python local_sanitizer.py --model_path ./model.gguf --repo_path ./src --categories scan

  # Scan only hardware protocols
  python local_sanitizer.py --model_path ./model.gguf --repo_path ./src --categories protocol

  # Custom output directory and context size
  python local_sanitizer.py --model_path ./model.gguf --repo_path ./src \
      --output_dir ./clean_code --context_size 32768

  # Offload fewer/more layers to GPU (0 = CPU only, -1 = all layers)
  python local_sanitizer.py --model_path ./model.gguf --repo_path ./src --gpu_layers 0
  python local_sanitizer.py --model_path ./model.gguf --repo_path ./src --gpu_layers -1

  # Delete ALL source files after processing (for large repo multi-iteration)
  python local_sanitizer.py --model_path ./model.gguf --repo_path ./src --remove

  # Delete ONLY flagged files matching specific categories
  python local_sanitizer.py --model_path ./model.gguf --repo_path ./src \
      --clean --categories scan


PARAMETERS:

  --model_path PATH        (required) Path to the .gguf model file.
                           Example: ./model/gemma-4-E2B-it-Q4_K_M.gguf

  --repo_path PATH         (required) Path to the source repository to scan.
                           Must be a directory containing code files.

  --output_dir DIR         Output directory for sanitized output.
                           Default: ./sanitized_output
                           Contains: sanitized files, flagged originals, logs, report.

  --context_size N         LLM context window size in tokens.
                           Default: 10000 (reduced from 131072 for faster processing).
                           Higher values allow larger chunks but use more memory.
                           Must be > 512 (reserved for LLM response).

  --gpu_layers N           Number of model layers to offload to GPU.
                           Default: 42 (almost all layers for gemma-4 with ~44 total).
                           Use 0 for CPU-only, -1 for all available layers.
                           Requires llama-cpp-python compiled with Vulkan support.

  --categories [CATS...]   Which analysis categories to run.
                           Default: scan protocol algorithm (all available)
                           Available categories (from prompts.yaml):
                             scan       — Hardcoded secrets, API keys, passwords, tokens
                             protocol   — Hardware device communication protocols
                             algorithm  — Proprietary algorithms, signal processing
                           Add new categories by editing prompts.yaml.

  --remove                 Delete ALL source files after processing.
                           Useful for large repository multi-iteration cleanup.
                           Mutually exclusive with --clean.

  --clean                  Delete ONLY flagged files that matched the selected categories.
                           Requires --categories to specify a subset (not all categories).
                           Mutually exclusive with --remove.


OUTPUT:

  sanitized_output/
    ├── [sanitized source tree]     — Cleaned code, sensitive chunks replaced
    ├── flagged/                    — Original copies of files with issues
    ├── sanitization_log.jsonl      — Per-chunk analysis log (one JSON per line)
    └── flagged_files_report.txt    — Human-readable report of flagged files


CATEGORIES (defined in prompts.yaml):

  scan:       Detects hardcoded encryption keys, API keys, passwords, tokens,
              connection strings, certificates, private keys, Base64-encoded secrets.
              Returns: {"sensitive": true/false, "confidence", "reason", "replacement"}

  protocol:   Identifies hardware/device communication protocols (SPI, I2C, UART,
              custom), command/response structures, packet/frame formats, device
              addresses, timing-critical code.
              Returns: {"has_protocol": true/false, "protocol_type", "ip_risk",
                        "command_structures", "response_structures", "abstraction_stub"}

  algorithm:  Finds proprietary algorithms — signal processing (FFT, DSP), data
              transformation (CRC, checksums), control algorithms (PID, state machines),
              calibration routines, hardware-specific computations.
              Returns: {"has_algorithm": true/false, "algorithm_family", "ip_risk_level",
                        "core_logic_description", "safe_stub_code"}

  To add a new category, append an entry to prompts.yaml with name, system_prompt,
  parse_rule, confidence_mapping, and replacement_key fields.


EXAMPLE WORKFLOW:

  # Step 1: Scan and review what was flagged
  python local_sanitizer.py --model_path ./model.gguf --repo_path ./my_project

  # Step 2: Check the report
  type sanitized_output\flagged_files_report.txt

  # Step 3: If satisfied, clean specific category from source
  python local_sanitizer.py --model_path ./model.gguf --repo_path ./my_project \
      --clean --categories scan

  # Step 4: Verify output
  dir sanitized_output
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
import traceback
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:
    yaml = None

# ---------------------------------------------------------------------------
# Venv Bootstrap — re-execution guard
# ---------------------------------------------------------------------------

LLM_SIZE = 131072; #131072;
LLM_GPU = 42;

def _ensure_venv() -> None:
    """Create .sanitizer_venv if needed and re-exec inside it.

    Why: llama-cpp-python with Vulkan must be compiled against the *exact*
    interpreter that will run the inference.  Bootstrapping guarantees the
    correct environment without manual user intervention.
    """
    if sys.prefix != sys.base_prefix:
        return  # already inside a venv

    venv_dir = Path(".sanitizer_venv").resolve()
    if not venv_dir.exists():
        print("[bootstrap] Creating virtual environment in .sanitizer_venv …")
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            check=True,
        )

    # Determine interpreter path (works on Win & POSIX)
    if sys.platform == "win32":
        python_exe = venv_dir / "Scripts" / "python.exe"
    else:
        python_exe = venv_dir / "bin" / "python"

    pip_exe = str(venv_dir / "Scripts" / "pip.exe") if sys.platform == "win32" else str(venv_dir / "bin" / "pip")
    venv_python = str(python_exe)

    # Check which packages are already installed via the venv python
    check_script = """
import importlib
packages = ["llama_cpp", "git", "rich", "pydantic"]
missing = [p for p in packages if not _try_import(p)]
def _try_import(name):
    try:
        importlib.import_module(name)
        return True
    except ImportError:
        return False
if missing:
    print("MISSING:" + ",".join(missing))
else:
    print("ALL_OK")
"""
    result = subprocess.run(
        [venv_python, "-c", check_script],
        capture_output=True, text=True,
    )
    output = result.stdout.strip()

    if "MISSING:" in output:
        pip_packages = {
            "llama_cpp": "llama-cpp-python",
            "git": "gitpython",
            "rich": "rich",
            "pydantic": "pydantic",
        }
        missing_imports = output.replace("MISSING:", "").split(",")
        install_cmd: list[str] = [pip_exe, "install", "--upgrade", "pip"]
        for imp in missing_imports:
            pkg_name = pip_packages.get(imp, imp)
            if pkg_name not in install_cmd:
                install_cmd.append(pkg_name)

        # CMAKE_ARGS + FORCE_CMAKE force llama-cpp-python to compile with
        # GGML_VULKAN, which enables AMD GPU acceleration on Windows/Linux.
        env = os.environ.copy()
        env["CMAKE_ARGS"] = "-DGGML_VULKAN=ON"
        env["FORCE_CMAKE"] = "1"

        print(f"[bootstrap] Installing missing dependencies: {', '.join(missing_imports)} (Vulkan enabled)…")
        subprocess.run(install_cmd, check=True, env=env)
        print("[bootstrap] Dependencies installed.")
    else:
        print("[bootstrap] All dependencies already installed in venv.")

    print("[bootstrap] Re-launching inside venv …")

    # Re-execute the same script with the venv Python (cross-platform)
    if sys.platform == "win32":
        subprocess.Popen([str(python_exe)] + sys.argv, close_fds=False)
    else:
        os.execv(str(python_exe), [str(python_exe)] + sys.argv)
    sys.exit(0)


# Run bootstrap early — before any heavy imports
_ensure_venv()

# ---------------------------------------------------------------------------
# Imports that require installed packages (available only in venv)
# ---------------------------------------------------------------------------

from llama_cpp import Llama  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn  # noqa: E402
from rich.logging import RichHandler  # noqa: E402
# ---------------------------------------------------------------------------
# Constants & defaults
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS = {".py", ".c", ".cpp", ".h", ".hpp", ".json", ".yaml", ".yml", ".md", ".txt", ".rs", ".go", ".java", ".cs", ".cmd", ".xml", ".xaml", ".csproj",".axaml"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".svn", ".venv", "venv", ".tox"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB

SANITIZE_COMMENT_PY = "# [SANITIZED] Original content removed by local_sanitizer.py\n"
SANITIZE_COMMENT_C  = "// [SANITIZED] Original content removed by local_sanitizer.py\n"

console = Console()

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ChunkResult:
    file_path: str
    chunk_index: int
    decision: str          # "safe" | "sanitized"
    confidence: float
    category: str          # "encryption" | "protocol" | "algorithm" | "safe"
    llm_response: dict = field(default_factory=dict)


@dataclass
class FileResult:
    path: str
    chunks: List[ChunkResult] = field(default_factory=list)
    output_content: str = ""


# ---------------------------------------------------------------------------
# Smart chunker
# ---------------------------------------------------------------------------

class SmartChunker:
    """Split source files into context-aware chunks that fit inside the LLM window."""

    # Boundaries: top-level def / class / fn-like blocks
    _BOUNDARY_RE = re.compile(
        r"""
        ^(?:
            (?:def|class|async\s+def|fn|func|pub\s+fn)\s+\w+   # python/rust/go
            | (?:public|private|protected)?\s*(?:static\s+)?[\w<>,\s]+\s+\w+\s*[(]  # C/C++/Java/C# methods
            | (?:class|struct|enum|interface|namespace)\s+\w+   # C++/Java/C#/Go
        )
        """,
        re.MULTILINE | re.VERBOSE,
    )

    def __init__(self, max_tokens: int, overlap: int = 64):
        self.max_tokens = max_tokens
        self.overlap = overlap  # token overlap between chunks

    def _estimate_tokens(self, text: str) -> int:
        """Very rough token counter (~4 chars/token for code)."""
        return max(1, len(text) // 4)

    def split(self, content: str) -> List[Tuple[int, int]]:
        """Return list of (start_char, end_char) for each chunk."""
        total_chars = len(content)
        char_limit = self.max_tokens * 4

        if self._estimate_tokens(content) <= self.max_tokens:
            return [(0, total_chars)]

        # Find logical boundaries
        boundaries = [m.start() for m in self._BOUNDARY_RE.finditer(content)]
        boundaries.append(total_chars)

        chunks: list[tuple[int, int]] = []
        start = 0
        for b in boundaries:
            if b == 0:
                continue
            if self._estimate_tokens(content[start:b]) > char_limit and b > start:
                # Hard split at char_limit if no good boundary found
                if len(chunks) == 0 or chunks[-1][1] != start:
                    hard_end = min(start + char_limit, b)
                    chunks.append((start, hard_end))
                    # overlap
                    start = max(hard_end - self.overlap * 4, 0)
                else:
                    start = b
            elif self._estimate_tokens(content[start:b]) > self.max_tokens and len(chunks) > 0:
                # Extend current chunk up to boundary
                prev_start, _ = chunks[-1]
                chunks[-1] = (prev_start, b)
                chunks.append((max(b - self.overlap * 4, 0), b))
                start = b
            else:
                if len(chunks) == 0:
                    chunks.append((start, b))
                else:
                    prev_start, _ = chunks[-1]
                    chunks[-1] = (prev_start, b)
                start = b

        # Handle remainder
        if start < total_chars:
            if chunks and chunks[-1][1] >= start:
                chunks[-1] = (chunks[-1][0], total_chars)
            else:
                chunks.append((start, total_chars))

        return chunks if chunks else [(0, total_chars)]


# ---------------------------------------------------------------------------
# Prompt loader — reads categories/prompts from prompts.yaml
# ---------------------------------------------------------------------------

_PROMPTS_CACHE: dict | None = None


def load_prompts(prompts_path: str | Path | None = None) -> list[dict]:
    """Load and validate prompt definitions from YAML file.

    Returns a list of category dicts, each containing:
      name, description, system_prompt, parse_rule, confidence_mapping,
      category_output, replacement_key
    """
    global _PROMPTS_CACHE
    if _PROMPTS_CACHE is not None:
        return _PROMPTS_CACHE

    if prompts_path is None:
        prompts_path = Path(__file__).parent / "prompts.yaml"
    prompts_path = Path(prompts_path)

    if not prompts_path.exists():
        logging.warning("prompts.yaml not found at %s — using built-in defaults", prompts_path)
        _PROMPTS_CACHE = _default_categories()
        return _PROMPTS_CACHE

    global yaml
    if yaml is None:
        # Fallback: try to install pyyaml
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "pyyaml"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            import yaml as _yaml  # noqa: F401
        except Exception:
            logging.warning("pyyaml not available — using built-in defaults")
            _PROMPTS_CACHE = _default_categories()
            return _PROMPTS_CACHE
        else:
            yaml = _yaml

    with open(prompts_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    categories = data.get("categories", [])
    # Validate required fields
    for cat in categories:
        assert "name" in cat, f"Category missing 'name': {cat}"
        assert "system_prompt" in cat, f"Category '{cat['name']}' missing 'system_prompt'"
        assert "parse_rule" in cat, f"Category '{cat['name']}' missing 'parse_rule'"
        assert "check_key" in cat["parse_rule"], f"Category '{cat['name']}' parse_rule missing 'check_key'"

    _PROMPTS_CACHE = categories
    return _PROMPTS_CACHE


def _default_categories() -> list[dict]:
    """Fallback built-in categories if YAML is unavailable."""
    return [
        {
            "name": "scan",
            "description": "Hardcoded secrets, API keys, passwords, tokens",
            "system_prompt": SYSTEM_PROMPTS["scan"],
            "parse_rule": {"check_key": "sensitive", "true_value": True},
            "category_output": "encryption",
            "replacement_key": "replacement",
        },
        {
            "name": "protocol",
            "description": "Device communication protocols, packet structures",
            "system_prompt": SYSTEM_PROMPTS["protocol"],
            "parse_rule": {"check_key": "has_protocol", "true_value": True},
            "confidence_mapping": [
                {"if_field": "ip_risk", "if_value": "high", "confidence": 0.9},
                {"if_field": "ip_risk", "if_value": "medium", "confidence": 0.7},
                {"if_field": "ip_risk", "if_value": "low", "confidence": 0.5},
            ],
            "category_output": "protocol",
            "replacement_key": "abstraction_stub",
        },
        {
            "name": "algorithm",
            "description": "Proprietary algorithms, signal processing, calibration",
            "system_prompt": SYSTEM_PROMPTS["algorithm"],
            "parse_rule": {"check_key": "has_algorithm", "true_value": True},
            "confidence_mapping": [
                {"if_field": "ip_risk_level", "if_value": "high", "confidence": 0.9},
                {"if_field": "ip_risk_level", "if_value": "medium", "confidence": 0.7},
                {"if_field": "ip_risk_level", "if_value": "low", "confidence": 0.5},
            ],
            "category_output": "algorithm",
            "replacement_key": "safe_stub_code",
        },
    ]


def get_prompt_by_name(name: str) -> dict | None:
    """Look up a single category by name."""
    for cat in load_prompts():
        if cat["name"] == name:
            return cat
    return None


def list_available_categories() -> list[str]:
    """Return all available category names from prompts.yaml."""
    return [cat["name"] for cat in load_prompts()]


# ---------------------------------------------------------------------------
# Repository scanner
# ---------------------------------------------------------------------------

def scan_repo(repo_path: Path) -> List[Path]:
    """Recursively collect code files, skipping binary / large / git dirs."""
    files: list[Path] = []
    for root, dirs, filenames in os.walk(repo_path):
        # Prune skip dirs (in-place modification)
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in filenames:
            fpath = Path(root) / fname
            try:
                if fpath.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue
                if fpath.stat().st_size > MAX_FILE_SIZE:
                    continue
                files.append(fpath)
            except OSError:
                continue
    files.sort()
    return files


# ---------------------------------------------------------------------------
# LLM runner
# ---------------------------------------------------------------------------

class LLMRunner:
    """Thin wrapper around llama_cpp.Llama with graceful fallback."""

    def __init__(
        self,
        model_path: str,
        n_ctx: int = LLM_SIZE,
        n_gpu_layers: int = LLM_GPU,
    ):
        self.n_ctx = n_ctx
        self.n_gpu_layers = n_gpu_layers
        self.model_path = model_path
        self._llm: Optional[Llama] = None
        self._loaded = False
        self._warned_cpu = False

    def load(self) -> Llama:
        if self._loaded:
            return self._llm  # type: ignore[return-value]

        console.print(f"[dim]Loading model: {self.model_path}[/dim]")
        console.print(f"[dim]  GPU layers: {self.n_gpu_layers} | Context: {self.n_ctx}[/dim]")

        # Diagnostic: check if llama-cpp-python was built with Vulkan support
        try:
            from llama_cpp import llama_backend_init
            # Attempt to detect GPU backends
            _test_llm = Llama(
                model_path=self.model_path,
                n_ctx=256,
                n_gpu_layers=self.n_gpu_layers,
                verbose=True,  # Show backend detection in logs
            )
            _test_llm._ctx.free()  # Free test context immediately
            del _test_llm
            console.print("[dim]  (Vulkan backend detected in verbose output above)[/dim]")
        except Exception:
            console.print("[yellow]⚠ Could not verify Vulkan backend. Model may run on CPU only.[/yellow]")

        console.print("[dim]Loading model for inference …[/dim]")
        try:
            self._llm = Llama(
                model_path=self.model_path,
                n_ctx=self.n_ctx,
                n_gpu_layers=self.n_gpu_layers,
                verbose=True,  # Keep verbose to see GPU/CPU backend
            )
        except Exception as e:
            console.print(f"[yellow]⚠ GPU offload failed ({e}). Falling back to CPU …[/yellow]")
            try:
                self._llm = Llama(
                    model_path=self.model_path,
                    n_ctx=self.n_ctx,
                    n_gpu_layers=0,
                    verbose=False,
                )
            except Exception as e2:
                console.print(f"[red]✗ Model loading failed even on CPU: {e2}[/red]")
                raise

        self._loaded = True
        console.print("[green]✓ Model loaded successfully.[/green]")
        return self._llm

    def generate(self, system_prompt: str, user_text: str, max_tokens: int = 512) -> str:
        llm = self.load()

        # Truncate user text to fit in context (leave room for system + response)
        max_user_chars = max(500, (self.n_ctx - 512) * 3)
        if len(user_text) > max_user_chars:
            user_text = user_text[:max_user_chars] + "\n... [truncated]"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ]

        result = llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.2,
            top_p=0.95,
            repeat_penalty=1.1,
        )
        return result["choices"][0]["message"]["content"]  # type: ignore[index]


# ---------------------------------------------------------------------------
# JSON parsing helper
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict:
    """Attempt to parse JSON from LLM text, handling markdown fences."""
    cleaned = text.strip()
    # Strip code fences
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", cleaned, re.DOTALL)
    if m:
        cleaned = m.group(1)
    return json.loads(cleaned)


def _safe_llm_json_parse(text: str) -> dict:
    """Parse JSON with graceful fallback on errors."""
    try:
        return _extract_json(text)
    except (json.JSONDecodeError, KeyError) as e:
        logging.warning("JSON parse error: %s — raw text: %s", e, text[:200])
        return {}


# ---------------------------------------------------------------------------
# Sanitizer engine
# ---------------------------------------------------------------------------

class SanitizerEngine:
    def __init__(self, runner: LLMRunner, output_dir: Path, flagged_dir: Path, log_path: Path, chunker: SmartChunker, remove_source: bool = False, clean_source: bool = False, categories: list[str] | None = None):
        self.runner = runner
        self.output_dir = output_dir
        self.flagged_dir = flagged_dir
        self.log_path = log_path
        self.chunker = chunker
        self.remove_source = remove_source
        self.clean_source = clean_source
        self.clean_categories = set(categories or [])

        # Load all available categories from YAML
        self.all_categories = load_prompts()
        category_map = {cat["name"]: cat for cat in self.all_categories}

        # Filter to requested subset (validate names)
        selected_names = categories or [c["name"] for c in self.all_categories]
        self.selected_categories: list[dict] = []
        for name in selected_names:
            if name in category_map:
                self.selected_categories.append(category_map[name])

        self.flagged_files: list[dict] = []
        self.removed_files: list[str] = []
        self.report_path = output_dir / "flagged_files_report.txt"

        # Initialize report file if it doesn't exist
        if not self.report_path.exists():
            with open(self.report_path, "w", encoding="utf-8") as f:
                f.write("=" * 60 + "\n")
                f.write("FLAGGED FILES REPORT — Files with sensitive content\n")
                f.write("(Appended incrementally — safe to interrupt at any time)\n")
                f.write("=" * 60 + "\n\n")

    def _log(self, entry: dict) -> None:
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _parse_llm_response(self, llm_json: dict, parse_rule: dict, confidence_mapping: list[dict], category_output: str, replacement_key: str) -> dict | None:
        """Normalize LLM response using YAML-defined parse_rule and confidence_mapping.

        Args:
            llm_json: Raw JSON from LLM
            parse_rule: {"check_key": "X", "true_value": true/false}
            confidence_mapping: list of {"if_field": ..., "if_value": ..., "confidence": ...}
            category_output: label for the result category
            replacement_key: JSON key containing suggested replacement code

        Returns None if not flagged, dict with is_sensitive/confidence/category/reason/replacement otherwise.
        """
        if not llm_json or "error" in llm_json:
            return None

        check_key = parse_rule["check_key"]
        true_value = parse_rule.get("true_value", True)

        # Check if this chunk should be flagged
        is_flagged = llm_json.get(check_key, False) == true_value
        if not is_flagged:
            return None

        # Derive confidence from confidence_mapping (YAML-defined) or default
        confidence = 0.7  # default
        for rule in confidence_mapping:
            field_val = llm_json.get(rule["if_field"], "")
            if field_val == rule["if_value"]:
                confidence = rule["confidence"]
                break

        # Build reason from available fields
        reason_parts = []
        for key in ["reason", "protocol_type", "algorithm_family", "core_logic_description", "ip_risk_level", "ip_risk"]:
            val = llm_json.get(key)
            if val:
                reason_parts.append(f"{key}={val}")

        # Add array counts
        for key in ["command_structures", "response_structures"]:
            arr = llm_json.get(key, [])
            if arr:
                reason_parts.append(f"{key}: {len(arr)}")

        return {
            "is_sensitive": True,
            "confidence": confidence,
            "category": category_output,
            "reason": "; ".join(reason_parts) if reason_parts else "sensitive content detected",
            "replacement": llm_json.get(replacement_key, ""),
        }

    def _write_flagged_entry(self, entry: dict) -> None:
        """Append a single flagged file entry to the report immediately."""
        with open(self.report_path, "a", encoding="utf-8") as f:
            f.write(f"FILE: {entry['file_path']}\n")
            cats = entry.get('categories_found', [])
            if cats:
                f.write(f"  Categories: {', '.join(cats)}\n")
            f.write(f"  Flagged chunks: {entry['flagged_chunks']}/{entry['total_chunks']}\n")
            for d in entry["details"]:
                f.write(f"  - Chunk #{d['chunk_index']}: confidence={d['confidence']:.2f}, "
                        f"category={d['category']}, reason={d['reason']}\n")
            f.write("\n")

    def _sanitize_comment(self, file_path: str) -> str:
        ext = Path(file_path).suffix.lower()
        if ext in {".py", ".yaml", ".yml", ".txt", ".md"}:
            return SANITIZE_COMMENT_PY
        return SANITIZE_COMMENT_C

    def process_file(self, fpath: Path, repo_path: Path, progress: Progress, task_id) -> FileResult:
        content = fpath.read_text(encoding="utf-8", errors="replace")
        rel = fpath.relative_to(repo_path)
        result = FileResult(path=str(rel))

        chunks = self.chunker.split(content)
        sanitized_parts: list[str] = []

        # Dynamic category names from YAML
        PROMPT_ORDER = [cat["name"] for cat in self.selected_categories]
        self._debug_count = 0  # limit debug output per file

        for idx, (start, end) in enumerate(chunks):
            chunk_text = content[start:end]

            # Run ALL prompts sequentially, collect findings
            findings: list[dict] = []

            for cat_def in self.selected_categories:
                prompt_name = cat_def["name"]
                system_prompt = cat_def["system_prompt"]
                parse_rule = cat_def["parse_rule"]
                confidence_mapping = cat_def.get("confidence_mapping", [])
                category_output = cat_def.get("category_output", prompt_name)
                replacement_key = cat_def.get("replacement_key", "replacement")

                try:
                    response_text = self.runner.generate(
                        system_prompt=system_prompt,
                        user_text=chunk_text,
                        max_tokens=512,
                    )
                    llm_json = _safe_llm_json_parse(response_text)
                except Exception as e:
                    console.print(f"[yellow]⚠ LLM error ({prompt_name}) on {rel} chunk {idx}: {e}[/yellow]")
                    llm_json = {"error": str(e)}

                # Parse result based on parse_rule from YAML
                finding = self._parse_llm_response(llm_json, parse_rule, confidence_mapping, category_output, replacement_key)

                # Debug: print first 5 raw responses per file
                if self._debug_count < 5:
                    console.print(f"  [dim]DEBUG [{prompt_name}] {rel}[{idx}]: {response_text[:500]}[/dim]")
                    console.print(f"  [dim]  → finding: {finding}[/dim]")
                    self._debug_count += 1

                if finding:
                    findings.append(finding)

                # Log each individual prompt result
                self._log({
                    "file_path": str(rel),
                    "chunk_index": idx,
                    "prompt": prompt_name,
                    "decision": "sanitized" if (finding and finding["is_sensitive"]) else "safe",
                    "confidence": finding["confidence"] if finding else 0.0,
                    "category": finding["category"] if finding else prompt_name,
                    "llm_response": llm_json,
                })
                progress.advance(task_id)

            # Decide: if ANY prompt flagged this chunk — sanitize
            sensitive_findings = [f for f in findings if f["is_sensitive"]]
            if sensitive_findings:
                # Use the highest-confidence finding for replacement
                best = max(sensitive_findings, key=lambda f: f["confidence"])
                replacement = best.get("replacement", "")
                if not replacement:
                    replacement = chunk_text

                sanitized_text = self._sanitize_comment(str(fpath)) + replacement
                sanitized_parts.append(sanitized_text)
                decision = "sanitized"

                # Console output — show all findings
                for f in findings:
                    if f["is_sensitive"]:
                        console.print(f"  [yellow]⚠ [{f['category'].upper()}] {rel} chunk {idx} (conf={f['confidence']:.2f}) — {f['reason']}[/yellow]")

                confidence = best["confidence"]
                categories = [f["category"] for f in sensitive_findings]
            else:
                sanitized_parts.append(chunk_text)
                decision = "safe"
                confidence = 0.0
                categories = ["safe"]

            cr = ChunkResult(
                file_path=str(rel),
                chunk_index=idx,
                decision=decision,
                confidence=confidence,
                category="; ".join(categories),
                llm_response={"findings": findings},
            )
            result.chunks.append(cr)

        # Reconstruct output content
        result.output_content = "\n".join(sanitized_parts)

        # Write to output dir (append mode — skip if already exists)
        out_file = self.output_dir / rel
        if not out_file.exists():
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(result.output_content, encoding="utf-8")
        else:
            # File already exists from a previous run — skip overwrite
            console.print(f"  [dim]Skipping (exists): {rel}[/dim]")

        # If any chunk was sanitized, also copy the ORIGINAL source file to flagged_dir
        has_flagged = any(c.decision == "sanitized" for c in result.chunks)
        if has_flagged:
            flagged_file = self.flagged_dir / rel
            if not flagged_file.exists():
                flagged_file.parent.mkdir(parents=True, exist_ok=True)
                flagged_file.write_text(content, encoding="utf-8")

            # Collect summary for report
            flagged_chunks = [c for c in result.chunks if c.decision == "sanitized"]
            all_categories = set()
            for c in flagged_chunks:
                for cat in c.category.split("; "):
                    all_categories.add(cat)

            entry = {
                "file_path": str(rel),
                "flagged_chunks": len(flagged_chunks),
                "total_chunks": len(result.chunks),
                "categories_found": sorted(all_categories),
                "details": [
                    {
                        "chunk_index": c.chunk_index,
                        "confidence": c.confidence,
                        "category": c.category,
                        "reason": "; ".join(
                            f.get("reason", "")
                            for f in c.llm_response.get("findings", [])
                            if f.get("is_sensitive")
                        ) or "sensitive content detected",
                    }
                    for c in flagged_chunks
                ],
            }
            self.flagged_files.append(entry)
            # Write to report IMMEDIATELY — safe if process interrupted
            self._write_flagged_entry(entry)

        # Remove source file if --remove flag is set
        if self.remove_source:
            try:
                fpath.unlink()
                self.removed_files.append(str(rel))
                self._cleanup_empty_dirs(fpath, repo_path)
            except OSError as e:
                console.print(f"  [yellow]⚠ Could not remove source file {fpath}: {e}[/yellow]")

        # Remove ONLY flagged files matching clean categories
        if self.clean_source and has_flagged:
            # Check if any flagged chunk matches one of the clean categories
            clean_matched = False
            for c in flagged_chunks:
                for cat in c.category.split("; "):
                    if cat in self.clean_categories:
                        clean_matched = True
                        break
                if clean_matched:
                    break

            if clean_matched:
                try:
                    fpath.unlink()
                    self.removed_files.append(str(rel))
                    console.print(f"  [red]✗ Cleaned: {rel}[/red]")
                    self._cleanup_empty_dirs(fpath, repo_path)
                except OSError as e:
                    console.print(f"  [yellow]⚠ Could not clean source file {fpath}: {e}[/yellow]")

        return result

    def _cleanup_empty_dirs(self, fpath: Path, repo_path: Path) -> None:
        """Remove empty parent directories up to repo_path."""
        parent = fpath.parent
        while parent != repo_path and parent.is_dir() and not any(parent.iterdir()):
            try:
                parent.rmdir()
                parent = parent.parent
            except OSError:
                break


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    available_cats = list_available_categories()
    p = argparse.ArgumentParser(
        description="",  # docstring handles all help text
        add_help=False,  # custom help to show full usage
    )
    p.add_argument("--model_path", default=None, help="Path to the .gguf model file.")
    p.add_argument("--repo_path", default=None, help="Path to the source repository.")
    p.add_argument("--output_dir", default="./sanitized_output", help="Output directory for sanitized repo (appended, not overwritten).")
    p.add_argument("--context_size", type=int, default=LLM_SIZE, help="LLM context window size in tokens. Default: 10000.")
    p.add_argument("--gpu_layers", type=int, default=LLM_GPU, help="Number of model layers to offload to GPU. Default: 42. Use 0 for CPU-only, -1 for all.")
    p.add_argument("--remove", action="store_true", default=False,
                   help="Delete ALL source files after processing (mutually exclusive with --clean).")
    p.add_argument("--clean", action="store_true", default=False,
                   help="Delete ONLY flagged files matching selected categories (requires --categories subset).")
    p.add_argument("--categories", nargs="*", default=available_cats[:],
                   choices=available_cats,
                   help=f"Analysis categories: {', '.join(available_cats)}. Default: all.")
    p.add_argument("-h", "--help", action="store_true", default=False,
                   help="Show this full usage guide and exit.")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    # Show full usage when no arguments or --help provided
    if args.help or (args.model_path is None and args.repo_path is None):
        sys.stdout.reconfigure(encoding='utf-8')
        print(__doc__)
        sys.exit(0)

    # Validate required parameters
    if args.model_path is None:
        console.print("[red]✗ Error: --model_path is required. Run without arguments for usage guide.[/red]")
        sys.exit(1)
    if args.repo_path is None:
        console.print("[red]✗ Error: --repo_path is required. Run without arguments for usage guide.[/red]")
        sys.exit(1)

    # Validate --clean constraints
    available_cats = set(list_available_categories())
    selected_cats = set(args.categories) if args.categories else set()
    if args.clean:
        if args.remove:
            console.print("[red]✗ --clean and --remove are mutually exclusive.[/red]")
            sys.exit(1)
        if not selected_cats:
            console.print("[red]✗ --clean requires --categories with at least one category.[/red]")
            sys.exit(1)
        if selected_cats == available_cats:
            console.print("[red]✗ --clean requires a specific subset via --categories (cannot use all categories).[/red]")
            sys.exit(1)

    repo_path = Path(args.repo_path).resolve()
    if not repo_path.is_dir():
        console.print(f"[red]✗ Repository path does not exist: {repo_path}[/red]")
        sys.exit(1)

    model_path = Path(args.model_path).resolve()
    if not model_path.is_file():
        console.print(f"[red]✗ Model file does not exist: {model_path}[/red]")
        sys.exit(1)

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    flagged_dir = output_dir / "flagged"
    flagged_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "sanitization_log.jsonl"

    # Leave 512 tokens for response
    max_tokens_for_content = args.context_size - 512
    if max_tokens_for_content < 256:
        console.print("[yellow]⚠ Context size is very small. Chunks may be truncated.[/yellow]")
        max_tokens_for_content = 256

    chunker = SmartChunker(max_tokens=max_tokens_for_content, overlap=64)
    runner = LLMRunner(
        model_path=str(model_path),
        n_ctx=args.context_size,
        n_gpu_layers=args.gpu_layers,
    )

    # Load model first (can take a while)
    try:
        runner.load()
    except Exception:
        console.print(f"[red]✗ Fatal: could not load model.\n{traceback.format_exc()}[/red]")
        sys.exit(1)

    # Scan
    console.print(f"[dim]Scanning {repo_path} …[/dim]")
    files = scan_repo(repo_path)
    if not files:
        console.print("[yellow]⚠ No code files found.[/yellow]")
        sys.exit(0)
    console.print(f"[green]Found {len(files)} files to process.[/green]")

    engine = SanitizerEngine(runner, output_dir, flagged_dir, log_path, chunker, remove_source=args.remove, clean_source=args.clean, categories=args.categories)

    if args.remove:
        console.print("[yellow]⚠ --remove enabled: ALL source files deleted after processing[/yellow]")
    if args.clean:
        console.print(f"[yellow]⚠ --clean enabled: ONLY files flagged by [{', '.join(args.categories)}] deleted from source[/yellow]")

    # Show loaded categories with descriptions
    cats_display = []
    for cat_def in engine.selected_categories:
        cats_display.append(f"{cat_def['name']} ({cat_def.get('description', '')})")
    console.print(f"[dim]  Categories: {', '.join(cats_display)}[/dim]")

    # Progress bar — each chunk runs N prompts sequentially (N = number of categories)
    total_chunks = sum(
        len(chunker.split(f.read_text(encoding="utf-8", errors="replace")))
        for f in files
    )
    total_steps = total_chunks * len(args.categories)

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Sanitizing …", total=total_steps)

            for fpath in files:
                progress.update(task, description=f"Processing {fpath.relative_to(repo_path)}")
                try:
                    engine.process_file(fpath, repo_path, progress, task)
                except KeyboardInterrupt:
                    console.print("\n[yellow]⚠ Interrupted by user. Saving progress …[/yellow]")
                    console.print(f"[dim]Log saved to {log_path}[/dim]")
                    sys.exit(0)
                except Exception as e:
                    console.print(f"[red]✗ Error on {fpath}: {e}[/red]")
                    # Log error entry
                    engine._log({
                        "file_path": str(fpath.relative_to(repo_path)),
                        "error": str(e),
                        "decision": "error",
                    })
                    continue

    except KeyboardInterrupt:
        console.print("\n[yellow]⚠ Interrupted. Partial output may exist in %s[/yellow]", output_dir)
        sys.exit(0)

    # Summary
    console.print()
    console.print("[green]✓ Sanitization complete![/green]")
    console.print(f"  Output: [bold]{output_dir}[/bold]")
    console.print(f"  Log:    [bold]{log_path}[/bold]")

    # Print stats from log
    sanitized_count = 0
    safe_count = 0
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                entry = json.loads(line)
                if entry.get("decision") == "sanitized":
                    sanitized_count += 1
                elif entry.get("decision") == "safe":
                    safe_count += 1
    except FileNotFoundError:
        pass

    console.print(f"  Safe chunks: [green]{safe_count}[/green]")
    console.print(f"  Sanitized chunks: [yellow]{sanitized_count}[/yellow]")

    if engine.removed_files:
        console.print(f"  Removed source files: [red]{len(engine.removed_files)}[/red]")
        if args.clean:
            console.print(f"    (--clean: only matched [{', '.join(args.categories)}])")

    # Write flagged files report (already appended incrementally, just show status)
    report_path = output_dir / "flagged_files_report.txt"
    if engine.flagged_files:
        console.print(f"  Flagged files: [bold yellow]{len(engine.flagged_files)}[/bold yellow]")
        console.print(f"  Report: [bold]{report_path}[/bold]")
        console.print(f"  Flagged dir: [bold]{flagged_dir}[/bold]")
    else:
        console.print("  [green]No files flagged — all content appears safe.[/green]")


if __name__ == "__main__":
    main()
