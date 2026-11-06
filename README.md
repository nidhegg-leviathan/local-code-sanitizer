# Local Code Sanitizer

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Инструмент для локальной очистки исходного кода от конфиденциальной информации с использованием LLM (GGUF формат).

## Требования

- Python 3.11+
- GGUF модель (например, `gemma-4-E2B-it-Q4_K_M.gguf`, ~3,4 ГБ — [как скачать](#скачивание-модели))

---

## Скачивание модели

В репозитории весов нет: файл на 3,4 ГБ лежит отдельно на Hugging Face. Скачайте его в папку `model/`.

**Страница модели:** [dahus/gemma-4-e2b-it-Q4_K_M-GGUF](https://huggingface.co/dahus/gemma-4-e2b-it-Q4_K_M-GGUF)

**Прямая ссылка на файл:** [gemma-4-e2b-Q4_K_M.gguf (3,43 ГБ)](https://huggingface.co/dahus/gemma-4-e2b-it-Q4_K_M-GGUF/resolve/main/gemma-4-e2b-Q4_K_M.gguf?download=true)

### Вариант 1: браузер

Открыть страницу модели → вкладка **Files and versions** → `gemma-4-e2b-Q4_K_M.gguf` → **Download**, затем положить файл в `model\gemma-4-E2B-it-Q4_K_M.gguf` (имя из примеров ниже).

### Вариант 2: PowerShell

```powershell
New-Item -ItemType Directory -Force model | Out-Null
Invoke-WebRequest `
  -Uri "https://huggingface.co/dahus/gemma-4-e2b-it-Q4_K_M-GGUF/resolve/main/gemma-4-e2b-Q4_K_M.gguf?download=true" `
  -OutFile "model\gemma-4-E2B-it-Q4_K_M.gguf"
```

### Вариант 3: huggingface-cli (с докачкой при обрыве)

```powershell
pip install -U "huggingface_hub[cli]"
hf download dahus/gemma-4-e2b-it-Q4_K_M-GGUF gemma-4-e2b-Q4_K_M.gguf --local-dir model
Rename-Item model\gemma-4-e2b-Q4_K_M.gguf gemma-4-E2B-it-Q4_K_M.gguf
```

### Другие квантизации

Если 3,4 ГБ многовато — в [model/README.md](model/README.md) есть таблица Q2_K … Q8 с размерами, скоростью и качеством, и ссылки на все варианты. Меньшие квантизации слабее держат формат ответа, поэтому для замены кода на заглушки лучше Q4_K_M и выше.

> Веса распространяются по [Gemma Terms of Use](https://ai.google.dev/gemma/terms), а не по лицензии MIT, под которой выпущен код этого репозитория.

---

## Установка

### Вариант 1: Запуск на CPU

```powershell
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Вариант 2: Запуск на AMD GPU (Vulkan)

```powershell
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
$env:CMAKE_ARGS="-DGGML_VULKAN=ON"
$env:FORCE_CMAKE="1"
pip install llama-cpp-python --no-cache-dir
pip install gitpython rich pydantic pyyaml
```

---

## Запуск

### Полный usage guide (без параметров)

```powershell
.\venv\Scripts\Activate.ps1
python local_sanitizer.py
```

Выведёт полное описание всех параметров и примеры использования.

### Базовый скан — все категории

```powershell
python local_sanitizer.py --model_path .\model\gemma-4-E2B-it-Q4_K_M.gguf --repo_path .\src
```

### Сканирование только секретов (scan)

```powershell
python local_sanitizer.py --model_path .\model\gemma-4-E2B-it-Q4_K_M.gguf --repo_path .\src --categories scan
```

### Сканирование только аппаратных протоколов (protocol)

```powershell
python local_sanitizer.py --model_path .\model\gemma-4-E2B-it-Q4_K_M.gguf --repo_path .\src --categories protocol
```

### Сканирование только алгоритмов (algorithm)

```powershell
python local_sanitizer.py --model_path .\model\gemma-4-E2B-it-Q4_K_M.gguf --repo_path .\src --categories algorithm
```

### Несколько категорий

```powershell
python local_sanitizer.py --model_path .\model\gemma-4-E2B-it-Q4_K_M.gguf --repo_path .\src --categories scan algorithm
```

### Пользовательская директория вывода и размер контекста

```powershell
python local_sanitizer.py --model_path .\model\gemma-4-E2B-it-Q4_K_M.gguf --repo_path .\src --output_dir .\clean_code --context_size 32768
```

### CPU-only запуск (без GPU)

```powershell
python local_sanitizer.py --model_path .\model\gemma-4-E2B-it-Q4_K_M.gguf --repo_path .\src --gpu_layers 0
```

### Полная загрузка модели на GPU (-1 = все слои)

```powershell
python local_sanitizer.py --model_path .\model\gemma-4-E2B-it-Q4_K_M.gguf --repo_path .\src --gpu_layers -1
```

### Удаление ВСЕХ исходных файлов после обработки (--remove)

```powershell
python local_sanitizer.py --model_path .\model\gemma-4-E2B-it-Q4_K_M.gguf --repo_path .\src --remove
```

### Удаление только flagged файлов по категориям (--clean)

```powershell
python local_sanitizer.py --model_path .\model\gemma-4-E2B-it-Q4_K_M.gguf --repo_path .\src --clean --categories scan
```

---

## Параметры

| Параметр         | Описание                                              | По умолчанию         |
| ---------------- | ----------------------------------------------------- | -------------------- |
| `--model_path`   | Путь к GGUF модели (или не указывать для вывода help) | —                    |
| `--repo_path`    | Путь к репозиторию (или не указывать для вывода help) | —                    |
| `--output_dir`   | Директория вывода                                     | `./sanitized_output` |
| `--context_size` | Размер контекстного окна в токенах (>512)             | 10000                |
| `--gpu_layers`   | Слоёв на GPU (0 = CPU, -1 = все)                      | 42                   |
| `--categories`   | Категории анализа: scan protocol algorithm            | все                  |
| `--remove`       | Удалить ВСЕ файлы после обработки                     | false                |
| `--clean`        | Удалить только flagged файлы по категориям            | false                |

---

## Категории анализа (prompts.yaml)

| Категория     | Что ищет                                                                                 |
| ------------- | ---------------------------------------------------------------------------------------- |
| **scan**      | Hardcoded ключи шифрования, API keys, пароли, токены, connection strings, Base64-секреты |
| **protocol**  | Аппаратные протоколы (SPI/I2C/UART), структуры пакетов, команды, device addresses        |
| **algorithm** | Проприетарные алгоритмы: FFT, DSP, PID, CRC, калибровка, state machines                  |

Для добавления новой категории — добавьте блок в `prompts.yaml`.

---

## Результат

```
sanitized_output/
├── [очищенный исходный код]     — чувствительные фрагменты заменены на заглушки
├── flagged/                     — оригиналы файлов с проблемами
├── sanitization_log.jsonl       — лог анализа (JSONL)
└── flagged_files_report.txt     — отчёт по проблемным файлам
```

---

## Лицензия

Проект распространяется по лицензии [MIT](LICENSE): код можно свободно использовать, изменять и распространять, в том числе в коммерческих целях, при сохранении текста лицензии и указания авторства. Программа поставляется «как есть», без гарантий.

Веса модели Gemma в репозитории не распространяются — они скачиваются отдельно и подчиняются условиям правообладателя ([Gemma Terms of Use](https://ai.google.dev/gemma/terms)).
