# 第三方组件与模型通知

本文列出答辩速查 __VERSION__ 使用的第三方组件、模型及其许可文件位置。发行包保留各组件对应版本的许可文本、模型协议和通知，具体条款以随附文件为准。

本项目自行编写的代码采用 [MIT 许可证](LICENSE)。下表中的第三方组件和模型分别适用各自条款，不由本项目的 MIT 许可证重新授权。

| 组件 | 来源与用途 | 随附许可或通知位置 |
| --- | --- | --- |
| Qwen3.5-4B 的 Q4_K_M GGUF 量化模型 | Qwen 原始模型；LM Studio Community 发布的 GGUF，用于本地回答 | `models/answer-model/LICENSE` 为 Apache License 2.0；同目录保留 `MODEL_CARD.md`、`ORIGINAL_MODEL_CARD.md` 和 `来源说明.txt` |
| SenseVoiceSmall INT8 | FunAudioLLM / iic 模型，经 sherpa-onnx 发行，用于本地语音识别 | `models/sensevoice/FUNASR_MODEL_LICENSE.txt` 是单独的 FunASR 模型协议；同时保留 `LICENSE`、`README.md` 与 `来源说明.txt`。模型权重适用其模型协议，软件代码许可另行适用 |
| llama.cpp 与 ggml 运行组件 | 用于本地模型运行，发行包保留相应 CPU / Vulkan 组件 | `runtime/llama/LICENSE` 为 MIT；各运行子目录内如有许可和来源通知也应保留 |
| Mermaid 10.9.3 | 随界面资源保留的第三方 JavaScript 资源 | `third_party_licenses/mermaid-10.9.3-LICENSE.txt` 为 MIT |
| Python 与已打包的第三方 Python 依赖 | Python 运行时及 FastAPI、Uvicorn、sherpa-onnx、jieba、文档解析等依赖 | `third_party_licenses/INDEX.txt` 与该目录各依赖许可文件；便携运行目录中相应的许可文件也应保留 |
| Microsoft Visual C++ Runtime | 应用目录内的 MSVCP140、CONCRT140、VCRUNTIME140 和 VCRUNTIME140_1，用于补齐跨电脑运行依赖 | `third_party_licenses/Microsoft-VC-Runtime-NOTICE.txt`；微软组件适用各自的微软条款和发行者的再分发授权，不属于项目自身的开源许可 |

当前模型与运行组件的上游来源：

- [Qwen3.5-4B 原始模型](https://huggingface.co/Qwen/Qwen3.5-4B)
- [LM Studio Community GGUF 发行](https://huggingface.co/lmstudio-community/Qwen3.5-4B-GGUF)
- [sherpa-onnx 模型与运行库](https://github.com/k2-fsa/sherpa-onnx)
- [FunASR 模型许可](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE)
- [llama.cpp](https://github.com/ggml-org/llama.cpp)
- [Mermaid](https://github.com/mermaid-js/mermaid)

源码仓库不包含模型和运行二进制。下载或构建时需同时取得对应版本的许可与模型卡，并随包含这些组件的便携包一起保留。

`scripts/collect_licenses.py` 从已安装环境收集 Python 依赖的许可记录。制作发行包时需按实际打包的组件核对通知文件；更换模型、量化版本、依赖或运行后端后，同步更新对应许可和来源说明。
