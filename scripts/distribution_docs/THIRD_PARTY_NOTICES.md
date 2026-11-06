# 第三方组件与模型通知

此说明对应答辩速查 __VERSION__ 的发行准备，帮助定位组件来源与随附条款。它不是对条款的完整重述，也不是法律意见；各组件实际附带的许可文本、模型协议和通知应一并保留。

本项目自身的许可尚待作者明确。下表中的第三方许可不代表本项目代码自动采用同一许可。

| 组件 | 来源与用途 | 随附许可或通知位置 |
| --- | --- | --- |
| Qwen3.5-4B 的 Q4_K_M GGUF 量化模型 | Qwen 原始模型；LM Studio Community 发布的 GGUF，用于本地回答 | `models/answer-model/LICENSE` 为 Apache License 2.0；同目录保留 `MODEL_CARD.md`、`ORIGINAL_MODEL_CARD.md` 和 `来源说明.txt` |
| SenseVoiceSmall INT8 | FunAudioLLM / iic 模型，经 sherpa-onnx 发行，用于本地语音识别 | `models/sensevoice/FUNASR_MODEL_LICENSE.txt` 是单独的 FunASR 模型协议；同时保留 `LICENSE`、`README.md` 与 `来源说明.txt`。不要把软件代码许可直接当作模型权重许可 |
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

源码仓库通常不包含模型和运行二进制；下载或构建时，应同时取得并保留对应版本的许可与模型卡。便携包包含这些组件时，应携带同版通知，而不是只保留本汇总表。

依赖数量和各自许可并不完全相同。`collect_licenses.py` 会从已安装环境收集许可记录，但发行者仍应核对实际打入包中的组件及其通知是否齐全。替换模型、量化发行、依赖版本或运行后端后，应重新核对相应文件。
