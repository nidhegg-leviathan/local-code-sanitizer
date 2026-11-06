---
language:
- en
base_model: google/gemma-4-e2b-it
pipeline_tag: text-generation
library_name: gguf
tags:
  - gemma
  - gemma4
  - gemma-4
  - gguf
  - llama-cpp
  - q4_k_m
  - quantized
license: apache-2.0
---

# Gemma 4 E2B it — Q4_K_M GGUF

4-bit medium quantized GGUF version of [google/gemma-4-e2b-it](https://huggingface.co/google/gemma-4-e2b-it).  
**Recommended default** — best balance of quality, speed, and size in the series.

> **Other quantizations in this series:**  
> [Q2_K](https://huggingface.co/dahus/gemma-4-e2b-it-Q2_K-GGUF) · [Q3_K_S](https://huggingface.co/dahus/gemma-4-e2b-it-Q3_K_S-GGUF) · [Q3_K_M](https://huggingface.co/dahus/gemma-4-e2b-it-Q3_K_M-GGUF) · [Q4_K_S](https://huggingface.co/dahus/gemma-4-e2b-it-Q4_K_S-GGUF) · [Q5_K_S](https://huggingface.co/dahus/gemma-4-e2b-it-Q5_K_S-GGUF) · [Q5_K_M](https://huggingface.co/dahus/gemma-4-e2b-it-Q5_K_M-GGUF) · [Q6_K](https://huggingface.co/dahus/gemma-4-e2b-it-Q6_K-GGUF) · [Q8](https://huggingface.co/dahus/gemma-4-e2b-it-Q8_0-GGUF)

---

## File Info

| Property | Value |
|---|---|
| Format | GGUF Q4_K_M |
| File size | **3.43 GB** |
| Bits per weight | ~4 |
| Size vs F16 | 2.7× smaller |

---

## Benchmark Results

Tested across 4 categories (Math, Logic, Code, Science), 3 prompts each.  
Greedy decoding, 200 max new tokens. Metrics compare logit distributions vs F16 baseline.

### Results by Category

| Category | Speed (tok/s) | SQNR | Top-1 Agreement | KL Divergence |
|---|---|---|---|---|
| 🔢 Math | 24.3 | 19.9 dB | 84.5% | 0.2786 |
| 🧠 Logic | 23.9 | 20.3 dB | 81.5% | 0.4190 |
| 💻 Code | 23.3 | 20.6 dB | 80.5% | 0.3618 |
| 🔬 Science | 24.7 | 20.6 dB | 83.3% | 0.2830 |
| **Overall** | **24.0** | **20.33 dB** | **82.4%** | **0.3356** |

---

## Quantization Comparison

| Model | Size | Speed (tok/s) | vs F16 speed | SQNR | Top-1 Agree | KL Div |
|---|---|---|---|---|---|---|
| F16 (baseline) | 8.67 GB | 5.7 | 1.0× | baseline | baseline | baseline |
| Q2_K | 2.78 GB | 31.6 | 5.6× | 5.85 dB | 32.0% | 4.1149 |
| Q3_K_M | 2.98 GB | 27.4 | 4.8× | 13.93 dB | 63.2% | 1.6747 |
| Q4_K_S | 3.13 GB | 25.0 | 4.4× | 19.10 dB | 80.9% | 0.3456 |
| **Q4_K_M** (this) | 3.43 GB | 24.0 | **4.2×** | **20.33 dB** | **82.4%** | **0.3356** |
| Q5_K_S | 3.35 GB | 21.9 | 3.9× | 23.32 dB | 87.7% | 0.1547 |
| Q5_K_M | 3.38 GB | 22.0 | 3.9× | 23.25 dB | 86.9% | 0.1248 |
| Q6_K | 3.58 GB | 19.9 | 3.5× | 28.72 dB | 94.1% | 0.0743 |
| Q8 | 4.63 GB | 16.2 | 2.9× | 37.11 dB | 96.0% | 0.0171 |

---

## Key Findings

- **Quality**: 82.4% Top-1 agreement — large jump from Q3 (63%), outputs are reliable and coherent
- **Speed**: 24.0 tok/s — 4.2× faster than F16
- **Size**: 3.19 GB — fits in 4 GB RAM
- **vs Q4_K_S**: Q4_K_M is marginally better quality (20.33 vs 19.10 dB SQNR, 82.4 vs 80.9% Top-1) for only 60 MB more
- **Best for**: General-purpose use — math, code, Q&A, chat; the go-to choice when you don't have a specific reason to pick another variant

---

## Usage

```bash
# llama.cpp CLI
./llama-cli -m gemma-4-e2b-q4km.gguf -p "Write a Python function for binary search." -n 200
```

```python
# llama-cpp-python
from llama_cpp import Llama

llm = Llama(model_path="gemma-4-e2b-q4km.gguf", n_ctx=2048)
output = llm("Write a Python function for binary search.", max_tokens=200)
print(output["choices"][0]["text"])
```

---

## Hardware

Tested on: CPU inference (llama.cpp)  
Context: 2048 tokens | Greedy decoding
