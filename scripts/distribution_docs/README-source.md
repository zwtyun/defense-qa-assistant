# 答辩速查

版本：__VERSION__。面向 Windows x64 的本地资料检索与答辩辅助工具，支持 Word/PDF 等资料导入、已有答案检索、本地语音提问、按资料生成回答以及持续追问。

本仓库源码与 Windows 便携包属于同一应用、同一版本。发行整理保留现有架构、功能、接口和操作方式；源码目录供开发与构建，便携包供使用者解压后运行。

## 下载后怎么使用

普通使用者请下载 Releases 中的 `DefenseDesk-__VERSION__-Windows-x64.zip`，完整解压后双击 `答辩速查.exe` 或 `启动答辩速查.bat`。便携包已含运行依赖、本地问答模型和语音模型，无需安装 Python。

GitHub 自动生成的 `Source code (zip)` 以及 `DefenseDesk-__VERSION__-source.zip` 是源码，不是可直接运行的 Windows 便携包。

便携包默认是空资料库。每位使用者自行导入资料；如使用 GPT API，自行在连接设置中填写服务地址、模型和密钥。源码和公开发行包均不包含开发者的项目资料、问答历史、API 密钥或连接配置。

## 主要功能

- 导入 DOCX、PDF、XLSX、JSON、TXT 和 Markdown；查找已有答案或原文，并核对出处。
- 使用本地模型或使用者自行配置的 GPT API 生成分点回答，按当前问题参考相关历史问答。
- 本地语音识别，单次最长三分钟；“停顿后自动发送”默认关闭。
- 对话保存在本机；手动清空后不再作为后续上下文。导入文件支持多选和全选删除。

识别结果超过 500 字时会完整保留，提示精简或分题后再发送。语音专有名词、生成答案的事实和出处仍需核对；功能通过检查不代表逐词识别或生成事实已经全部校验。

## 源码运行

开发环境需要 Windows x64、64 位 Python；前端测试另外需要 Node.js。当前便携程序由 Windows 上的 Python 环境构建。更换 Python 或依赖版本时，应自行确认相关二进制依赖可用。

在源码目录中运行以下 PowerShell 命令：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts/download_answer_model.py --model qwen35
.\.venv\Scripts\python.exe scripts/download_speech_model.py
.\.venv\Scripts\python.exe launcher.py
```

两个下载脚本从各自声明的上游来源获取本地模型及运行组件，需要联网和足够的下载、解压空间。模型文件较大，未放入普通 Git 仓库。模型下载完成后，本地检索、语音识别和本地回答可在本机处理；选择 GPT API 时，相关问题、历史片段和检索材料会发给使用者配置的服务。

默认服务地址为 `http://127.0.0.1:8766`；被占用时启动器会选择后续可用端口。源码启动和便携程序使用相同的界面与数据结构。

## 测试与构建

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --test tests/test_generation_frontend.cjs tests/test_library_frontend.cjs tests/test_provider_frontend.cjs tests/test_voice_frontend.cjs
.\.venv\Scripts\python.exe scripts/collect_licenses.py
.\.venv\Scripts\python.exe scripts/build_windows.py
```

`build_windows.py` 使用 PyInstaller 构建，并将 Windows 程序和 `_internal` 配套目录放入项目根目录。它不把个人资料库打入可执行文件。构建后保留 `models`、`runtime` 和第三方许可目录，再制作完整发行包。

构建后的基础便携检查使用隔离临时资料库：

```powershell
.\.venv\Scripts\python.exe scripts/smoke_portable.py
```

准备源码与完整便携发行物：

```powershell
.\.venv\Scripts\python.exe scripts/prepare_distribution.py --stage-only
.\.venv\Scripts\python.exe scripts/prepare_distribution.py --archive-only
```

默认输出到 `发布包/v__VERSION__`。不加参数会顺序生成目录与压缩包；`--source-only` 仅准备源码，`--output <目录>` 可指定输出位置。具体发布方式和大 ZIP 分卷见 [PUBLISHING.md](PUBLISHING.md)。

## 目录与数据

运行时主要目录保持原结构：`_internal` 是便携运行依赖，`models` 是模型，`runtime` 是模型运行组件，`data` 保存各使用者自己的资料、对话和设置，`logs` 保存本机运行日志。源码目录中的 `static`、`scripts`、`tests` 用于界面、构建与测试。

`data`、`logs`、模型、构建输出、便携二进制以及个人配置应留在各自电脑或发行资产中，不提交到源码仓库。已有资料或对话的迁移应作为单独的数据操作处理，不通过公开源码包传播。

## 许可

本项目自身的开源许可尚待作者明确，本次发行准备未代选 MIT、Apache 或其他协议。第三方组件各自的许可不等于本项目代码已经采用相同许可。发布者应在正式公开前明确项目许可，并保留各组件附带的许可和模型条款，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

本机隔离检查只能说明相应环境下的结果；尚未在每一台接收电脑上实测。接收者的 Windows 版本、CPU 指令集、内存、显卡驱动、麦克风权限等仍可能影响运行或性能。
