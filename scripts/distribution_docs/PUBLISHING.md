# 同一应用的源码仓库与 Windows 发行包

当前版本：__VERSION__。源码与便携 ZIP 是同一个应用的两种交付形态，不是两个独立产品。保持相同的版本标识；使用者下载便携版，开发者获取源码。

## 源码放仓库，程序放 Releases

源码仓库保存应用代码、界面资源、构建脚本、测试和许可通知。模型权重、运行二进制、个人资料库、对话、API 设置、日志、构建目录和旧报告不放进普通 Git 提交。

GitHub 普通 Git 仓库会阻止超过 100 MiB 的单文件；GitHub Releases 的单个资产文件必须小于 2 GiB。这两项限制作用于不同的上传位置，不能用“源码压缩包”名称绕过。参见 GitHub 官方的[大文件说明](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)和[Releases 说明](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)。

公开源码不包含大模型，因此 GitHub 自动生成的 `Source code (zip)` 也不能替代带模型的便携版。

## 使用本次白名单源码目录

开发工作区原有 `.git` 曾追踪个人资料、对话、设置、日志和大体积运行文件。后来修改 `.gitignore` 或删除当前文件，不会自动移除早期提交中的内容。

请使用 `prepare_distribution.py` 导出的“GitHub源码”目录建立新仓库，不要在原工作区直接添加远端并 push，也不要将原 `.git` 复制进去。本次准备工作不修改原 Git 历史，不创建远端，也不自动上传。

正式公开前，由作者明确项目自身许可；本次未代选 MIT 或 Apache 协议。第三方许可通知和模型条款应继续随相应发行物保留。

## 生成交付文件

在开发工作区运行：

```powershell
.\.venv\Scripts\python.exe scripts/prepare_distribution.py --stage-only
```

先生成可检查的源码与便携目录。默认输出位置为 `发布包/v__VERSION__`；使用 `--output <目录>` 可以指定位置，`--source-only` 仅准备源码。

目录准备完成后压缩：

```powershell
.\.venv\Scripts\python.exe scripts/prepare_distribution.py --archive-only
```

默认发行文件名为：

- `DefenseDesk-__VERSION__-source.zip`：白名单源码，不包含旧 `.git` 或个人数据。
- `DefenseDesk-__VERSION__-Windows-x64.zip`：含完整本地功能的 Windows 便携程序。

不加参数会依次生成目录与 ZIP。完整便携版主要空间来自本地问答和语音模型；保留全部功能时，不能通过删除模型把它缩成只有一个小 exe。

制作便携目录时还会补齐应用本地的四个 x64 Microsoft VC++ 运行库 DLL；可用 `--vc-runtime-dir <目录>` 指向具有适当再分发授权的微软运行库目录。默认从制作电脑的 System32 查找。它不会安装运行库或改动系统目录；发行者的授权要求见 `third_party_licenses/Microsoft-VC-Runtime-NOTICE.txt`。仅制作源码时不复制这些 DLL。

## 创建源码仓库

以下为发布者后续手动操作说明，本次整理不会执行这些上传命令。

在导出的“GitHub源码”目录内初始化新 Git 仓库，确认待提交文件只来自白名单，再创建提交和远端。示例中的仓库地址由发布者填写：

```powershell
git init
git add .
git commit -m "Prepare version __VERSION__"
git branch -M main
git remote add origin <你的GitHub仓库地址>
git push -u origin main
```

只在导出的源码目录执行，不能在包含旧历史的开发工作区执行。不要将整个“发布包”目录、便携模型或个人数据作为源码添加。

## 便携 ZIP 大于 2 GiB 时

完整 ZIP 可以直接通过移动存储设备或其他支持大文件的方式传给接收者。准备 GitHub Releases 时，先看 ZIP 的实际大小：小于 2 GiB 可作为单一资产上传；达到或超过该值时，可将 ZIP 切成每卷不超过 1.5 GiB 的资产。

```powershell
.\.venv\Scripts\python.exe scripts/prepare_distribution.py --split-zip "<便携ZIP完整路径>" --part-size-mib 1536
```

分卷输出到 ZIP 相邻的 `<ZIP文件名不含扩展>-github-parts` 目录，其中包含 `.part001`、`.part002` 等文件以及 `合并便携压缩包.bat`、`合并便携压缩包.ps1`。将所有分卷与两个合并脚本一起作为同一版本的 Release 资产上传。

分卷是原 ZIP 的字节切片，不是每卷独立可解压的压缩包。接收者下载全部文件后，用 Windows 自带 PowerShell 的配套脚本流式合并为原 ZIP，再完整解压使用。

默认只生成完整 ZIP，不同时生成分卷，避免本机重复占用空间。向接收者交付时，选择“完整 ZIP”或“全部分卷加合并脚本”其中一种，不需要两套都发。制作分卷或在接收端合并期间仍会暂时同时存在源文件和目标文件，请为该过程留足空间；上传与解压确认完成后，再按自己的保留需要处理多余副本。

## 发布页面应说明

- 标注 Windows x64、版本 __VERSION__ 和正常启动方式。
- 标注空资料库、使用者自行导入资料与自行配置 API Key。
- 提供本地模型与其他组件的来源、许可通知和已知兼容性范围。
- 将“本机隔离验证”与“其他电脑实际验证”分别描述，不承诺尚未实测的机器已经兼容。
