# 发布指南

源码在 [main 分支](https://github.com/zwtyun/defense-qa-assistant)维护，Windows 便携版通过 [Releases](https://github.com/zwtyun/defense-qa-assistant/releases)分发。当前版本为 __VERSION__。

## 发布内容

源码包含应用代码、界面资源、测试、构建脚本、项目 `LICENSE` 和第三方许可通知。模型、运行二进制、资料库、对话、API 设置、日志和构建输出不提交到 Git。`prepare_distribution.py` 按文件清单导出源码，制作便携包时不复制 `data` 和 `logs`。

便携版需保留本地问答模型、语音模型、`_internal`、`runtime`、项目 `LICENSE` 和第三方许可文件。打包脚本会将根目录的 `LICENSE` 复制到导出的源码和便携程序目录。GitHub 自动生成的 `Source code (zip)` 只包含源码，使用者应下载 Release 附件。

本项目自行编写的代码采用 [MIT 许可证](LICENSE)。第三方组件和模型继续适用各自条款，来源与通知位置见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 生成发行文件

在 Windows x64 开发环境中操作。先按 [README](README.md#从源码运行)安装依赖并下载模型及运行组件，再构建程序、收集许可和准备使用说明：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts/collect_licenses.py
.\.venv\Scripts\python.exe scripts/build_windows.py
(Get-Content .\scripts\distribution_docs\PORTABLE-START.txt -Raw -Encoding utf8).Replace(('__' + 'VERSION' + '__'), '__VERSION__') |
    Set-Content .\使用说明.txt -Encoding utf8
```

使用说明由便携版说明模板生成，并填入当前发行版本。打包脚本从项目根目录读取 `答辩速查.exe`、`_internal`、`models`、`runtime`、`启动答辩速查.bat`、`使用说明.txt` 和 `LICENSE`。

先生成发行目录，再压缩：

```powershell
.\.venv\Scripts\python.exe scripts/prepare_distribution.py --stage-only
.\.venv\Scripts\python.exe scripts/prepare_distribution.py --archive-only
```

默认输出到 `发布包/v__VERSION__`：

- `GitHub源码/`：导出的源码目录。
- `DefenseDesk-__VERSION__-Windows-x64/`：便携程序目录。
- `DefenseDesk-__VERSION__-source.zip`：源码压缩包。
- `DefenseDesk-__VERSION__-Windows-x64.zip`：完整便携压缩包。

不加参数时会依次生成目录和 ZIP。`--source-only` 仅生成源码，`--output <目录>` 指定输出位置；这两个参数可与 `--stage-only` 或 `--archive-only` 配合使用。输出目录中的同名发行目录或 ZIP 已存在时，脚本会停止；重新制作时使用新的输出位置。

制作便携包还需要四个 x64 Microsoft VC++ 运行库 DLL：`msvcp140.dll`、`concrt140.dll`、`vcruntime140.dll` 和 `vcruntime140_1.dll`。脚本默认从制作电脑的 `System32` 读取，可通过 `--vc-runtime-dir <目录>` 指定具有相应再分发授权的来源。运行库复制到应用依赖目录，不修改 Windows 系统目录；授权要求见 [Microsoft-VC-Runtime-NOTICE.txt](third_party_licenses/Microsoft-VC-Runtime-NOTICE.txt)。仅生成源码时不复制这些 DLL。

## 为 GitHub 生成分卷

GitHub Releases 的每个附件必须小于 2 GiB，限制见 [GitHub 官方说明](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases#storage-and-bandwidth-quotas)。__VERSION__ 便携 ZIP 约 2.74 GiB，按每份 1536 MiB 分卷：

```powershell
.\.venv\Scripts\python.exe scripts/prepare_distribution.py --split-zip ".\发布包\v__VERSION__\DefenseDesk-__VERSION__-Windows-x64.zip" --part-size-mib 1536
```

脚本在 ZIP 旁创建 `DefenseDesk-__VERSION__-Windows-x64-github-parts/`，本版本共五个附件：

- `DefenseDesk-__VERSION__-Windows-x64.zip.part001`
- `DefenseDesk-__VERSION__-Windows-x64.zip.part002`
- `Merge-Portable.bat`
- `Merge-Portable.ps1`
- `SPLIT-README.txt`

辅助文件使用英文名，便于 GitHub 保留文件名并与脚本引用保持一致。分卷是完整 ZIP 的字节切片，不能单独解压。下载者需将五个文件放在同一目录，双击 `Merge-Portable.bat` 合并 ZIP，再完整解压运行。分卷、合并后的 ZIP 和解压目录会同时占用磁盘空间。

## 上传发布

1. 将本次源码修改提交到 `main` 并推送到 `https://github.com/zwtyun/defense-qa-assistant.git`。提交前查看文件清单，排除模型、个人数据、密钥、日志和发行包。
2. 在 [Releases](https://github.com/zwtyun/defense-qa-assistant/releases) 新建草稿，使用与程序一致的版本标签，例如 `v__VERSION__`，目标选择本次源码提交。已有标签不重复创建。
3. 上传分卷目录中的全部五个附件。发布说明注明 Windows x64、无需安装 Python、默认空资料库，以及合并、解压和启动步骤；API 由使用者自行配置。
4. 核对附件名称、大小和上传状态，并确认 BAT 引用的 PS1 与发布文件名一致，再发布。

更新版本时同步调整程序版本、文档和 Release 标签。`scripts/distribution_docs/` 中的模板使用版本占位符；导出源码和便携包时由打包脚本替换。
