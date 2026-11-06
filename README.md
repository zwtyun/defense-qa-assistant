# 答辩速查

答辩速查是一款 Windows 本地资料检索与问答工具。导入项目材料和预先整理的答案后，可以输入关键词查找回答，也可以用语音提问，由本地模型或 API 结合资料生成回答。

[下载 Windows 便携版](https://github.com/zwtyun/defense-qa-assistant/releases/latest) · [反馈问题](https://github.com/zwtyun/defense-qa-assistant/issues) · [构建与发布](PUBLISHING.md)

当前版本：2.2.2。

## 主要功能

- **资料检索**：导入 Word、PDF、Excel、JSON、TXT 和 Markdown，搜索预设答案与原文片段，并查看出处。
- **答辩回答**：先展示从本地资料提炼的开场回答，再逐步显示按约三分钟准备的分点口述稿与依据；支持追问、停止生成和复制。
- **语音提问**：在本机识别语音，单次最长三分钟；“停顿后自动发送”默认关闭。
- **回答模型**：可选择本地模型，也可配置支持 Responses 或 Chat Completions 格式的 API，模型名称可自行填写。
- **资料管理**：编辑答案卡片、维护关键词和其他问法，按需导入、删除或导出答案库。

## 下载安装

便携版适用于 Windows x64，已包含 Python 运行环境、本地问答模型和语音模型。

1. 打开 [Release 下载页面](https://github.com/zwtyun/defense-qa-assistant/releases/latest)，下载以下五个附件：

   ```text
   DefenseDesk-2.2.2-Windows-x64.zip.part001
   DefenseDesk-2.2.2-Windows-x64.zip.part002
   Merge-Portable.bat
   Merge-Portable.ps1
   SPLIT-README.txt
   ```

2. 将五个文件放在同一目录，双击 `Merge-Portable.bat`，生成完整的 `DefenseDesk-2.2.2-Windows-x64.zip`。
3. 将 ZIP **完整解压**到当前用户可写的文件夹，双击 `答辩速查.exe` 或 `启动答辩速查.bat`。
4. 等待启动窗口就绪，浏览器会打开操作界面。首次使用时资料库为空，需要自行导入材料。

请保留程序目录中的 `_internal`、`models`、`runtime` 和许可文件。合并时会同时保留分卷和完整 ZIP，解压还需要额外空间。

Release 底部的 `Source code` 压缩包用于开发，不含运行环境和模型。需要直接使用程序时，请下载上述五个附件。

## 第一次使用

### 导入材料与答案库

点击“导入资料”，选择文件和导入方式：

| 导入方式 | 适合的材料 |
| --- | --- |
| 自动识别 | 同时包含正文和问答的材料，提取问答并保留原文 |
| 答案库 | 已经整理好的预设问答 |
| 原始资料 | 项目申报书、技术说明等，只建立原文索引 |

支持 `.docx`、`.pdf`、`.xlsx`、`.json`、`.txt` 和 `.md`，可以多选，每个文件不超过 60 MB。旧版 `.doc` 请先另存为 `.docx`；扫描 PDF 请先进行文字识别，带密码的 PDF 请先解除密码保护。

已有答案可以使用“资料与答案库”中的“下载答案模板”整理：一行填写一个问题、回答、关键词及出处，保存为 Excel 后导入。也可以点击“新建答案”逐条维护。

### 检索与提问

- 只查准备好的答案：取消勾选“生成回答”，输入问题关键词，查看“已有答案与原文”。
- 需要组织回答：勾选“生成回答”，输入完整问题，按 Enter 或点击“发送问题”。
- 继续追问：在同一对话中直接输入追问。回答会参考相关前文和当前检索到的资料。
- 需要核对依据：打开回答中的出处，查看对应原文。Word 使用段落或表格位置，PDF 使用文件内的页序。

每个独立问题的完整口述稿按约三分钟组织，实际朗读时长随语速和内容变化。答辩前请核对关键数字、单位、专有名词和引用出处。

### 语音输入

点击“语音提问”，允许浏览器访问麦克风，说完后手动停止。单次最长三分钟，转写后可以修改文字。

“更多选项”中的“停顿后自动发送”默认关闭，需要时自行勾选。识别文字超过 500 字会完整保留，请精简或分题后再发送。录音在本机识别，不保存录音文件。

### 本地模型与 API

本地模型无需填写密钥，首次加载可能需要等待，响应速度取决于电脑的 CPU、内存、显卡和驱动。

使用 API 时，打开“连接设置”：

1. 将“使用方式”切换为“GPT API”。
2. 填写服务提供的 **API 地址**（Base URL），不要填写具体的 `/responses` 或 `/chat/completions` 接口路径。
3. 按服务说明选择 **接口格式**，填写实际可用的 **模型名称** 和自己的 **API Key**。下拉列表只是快捷选项，也可以手动填写模型名。
4. 点击“测试连接”，通过后点击“保存并使用”。连接测试使用通用短句，也可能产生少量调用费用。

模型不支持“思考强度”参数时，选择“由模型默认决定”。需要代理时，可以在连接设置的“更多选项”中开启“使用系统代理”。

本地检索、导入和语音识别在本机处理。选择 API 生成回答时，当前问题、相关对话和资料片段会发送到配置的服务，并按该服务计费。

## 数据保存与备份

| 目录 | 内容 |
| --- | --- |
| `data/` | 导入材料、答案库、对话和连接设置 |
| `logs/` | 本机运行日志，启动问题可查看 `logs/app.log` |
| `models/` | 本地问答和语音模型 |
| `runtime/` | 本地模型运行组件 |

关闭启动窗口后，复制 `data` 目录即可备份个人资料和对话。迁移到另一台电脑时，先关闭两边程序，再恢复 `data`；保存的 API 密钥使用当前 Windows 用户加密，换电脑或 Windows 用户后需要重新填写。

“清空对话”会删除已显示的问答和后续对话上下文，导入的资料仍保留。删除导入材料请到“资料与答案库”中操作。关闭浏览器页面不会停止本地服务，关闭启动窗口才会停止服务。

## 常见问题

| 问题 | 处理方法 |
| --- | --- |
| 下载后无法解压 | 两份 `.part` 文件需要先通过 `Merge-Portable.bat` 合并；检查五个附件是否都在同一目录、下载是否完成 |
| 程序打不开或提示缺少 DLL | 重新完整解压，保留 `_internal`、`models` 和 `runtime`，查看 `logs/app.log` 中的报错 |
| 没有检索结果 | 先确认材料已导入，再尝试核心关键词；扫描 PDF 需要先识别文字，答案卡片可以补充关键词和其他问法 |
| 本地模型首次回答较慢 | 等待模型加载完成；内存或算力不足时，可切换为 API 回答，或关闭“生成回答”只检索已有答案 |
| 麦克风无法使用 | 检查 Windows 麦克风权限及浏览器对本地页面的麦克风授权，确认输入设备正确 |
| API 连接失败 | 核对 Base URL、接口格式、模型名称、密钥和账号额度；参数不支持时将思考强度设为默认 |
| 浏览器未自动打开 | 点击启动窗口的“打开检索界面”，使用窗口显示的本地地址 |

程序默认使用 `http://127.0.0.1:8766`。端口被其他程序占用时会尝试后续端口，以启动窗口显示的地址为准。当前发布面向 Windows x64，其他系统或硬件组合的兼容性尚未逐一验证。

## 从源码运行

需要 Windows x64 和 64 位 Python。在仓库根目录打开 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/download_answer_model.py --model qwen35
.\.venv\Scripts\python.exe scripts/download_speech_model.py
.\.venv\Scripts\python.exe launcher.py
```

模型和运行组件需要联网下载，未包含在 Git 仓库中。下载完成后，本地模式可以离线使用。界面资源位于 `static/`，文档导入、检索、语音与回答服务分别位于对应 Python 模块中。

开发依赖和测试命令：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
node --test tests/test_generation_frontend.cjs tests/test_library_frontend.cjs tests/test_provider_frontend.cjs tests/test_voice_frontend.cjs
```

前端测试需要 Node.js。Windows 打包、源码导出和分卷发布步骤见 [PUBLISHING.md](PUBLISHING.md)。

## 许可与反馈

本项目自行编写的代码采用 [MIT 许可证](LICENSE)，版权署名为 zwtyun。第三方模型、运行组件及依赖遵循各自许可，来源和通知见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 与 `third_party_licenses/`。

遇到问题请在 [Issues](https://github.com/zwtyun/defense-qa-assistant/issues) 中提供程序版本、Windows 版本、操作步骤和错误信息。粘贴日志或截图前，请去掉 API 密钥和不便公开的材料内容。
