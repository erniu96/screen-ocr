# 菜单栏 App：Screen OCR

Screen OCR 是一个原生 Swift / AppKit 菜单栏应用。启动后只显示菜单栏图标，不显示 Dock 图标；按 **⌥S（Option + S）** 框选区域，识别完成后直接复制文字。

App 管理快捷键、截图、任务状态和剪贴板。OCR 仍使用项目中已准备好的本地运行时；Python 与 OCR 引擎只在识别时启动，不在空闲时常驻。使用这个 App 不需要 Hammerspoon，也不需要先启动 HTTP 服务。

## 安装

先完成[项目 README](../README.md)中的环境和运行时准备，并确认以下命令能输出文字：

```sh
uv run screen-ocr recognize tests/fixtures/english.png --text
```

以下命令均在项目根目录执行。构建需要 Swift 6 或更新版本及 Xcode Command Line Tools；目标为 Apple Silicon Mac。App 的部署目标是 macOS 13 或更新版本，OCR 运行时的实测系统范围仍以项目 README 为准。

```sh
uv run scripts/build_macos_app.py --install
open "$HOME/Applications/Screen OCR.app" --args --project-directory "$PWD"
```

构建产物位于 `dist/Screen OCR.app`，`--install` 将应用安装到当前用户的 `~/Applications/Screen OCR.app`。应用使用本机 ad-hoc 签名，不包含第三方模型或运行库。

也可用 `uv run scripts/build_macos_app.py --install --open` 一次完成构建、安装并配置本次启动的项目目录。

`--project-directory` 指向已完成安装的项目目录，App 会记住这个位置。之后可以直接从 Finder 打开 `Screen OCR.app`，也可以通过菜单选择新的 OCR 项目目录。不要移动或删除这个目录中的 `.venv` 与 `.runtime`。

## 第一次使用

1. 点击菜单栏中的 Screen OCR 图标，或直接按 **⌥S**。
2. 首次截图时，按系统提示为 **Screen OCR** 授予屏幕录制权限。此前授予其他应用的权限不会自动转移到这个 App。
3. 如果已拒绝授权，从 App 菜单打开系统设置，在「隐私与安全性」中的屏幕录制项目启用 Screen OCR。按系统提示退出并重新打开应用，然后再次截图。
4. 拖动框选文字区域。出现复制成功提示后，在目标应用按 **⌘V** 粘贴。

截图时按 **Esc** 可取消。识别期间可从菜单取消当前任务；取消、识别失败或结果为空时，原剪贴板保持不变。同一时间只处理一张图片，再次触发不会积累等待任务。

截图和文本结果使用临时文件，任务结束后清理；App 不保存识别历史。正常退出会先取消当前任务并清理。系统强制终止或崩溃时，临时目录可能留待系统清理。

快捷键通过 macOS 的全局快捷键注册接口实现，App 不使用键盘事件监听，不主动申请辅助功能权限。屏幕录制授权由系统管理，App 不能代替用户授予。

## 从旧快捷键配置迁移

如果已有其他应用绑定 ⌥S，先停用它的 OCR 快捷键，再使用 Screen OCR 菜单重新注册快捷键或重新启动 App。

已有 Hammerspoon 配置时，只需删除或注释该 OCR 模块的初始化调用与对应快捷键绑定，然后 **Reload Config**；其他自动化配置可以保留。旧 OCR 模块本身和历史文件无需删除。如何恢复旧集成见 [Hammerspoon 集成说明](../integrations/hammerspoon/README.md)。

快捷键冲突时，仍可通过菜单开始截图。不要同时启用两个应用的同一 OCR 快捷键。

## 更新、移动项目与卸载

更新源代码后，退出菜单栏 App，再执行：

```sh
git pull --ff-only
uv sync --locked
uv run scripts/build_macos_app.py --install
open "$HOME/Applications/Screen OCR.app"
```

若移动了项目目录，在新目录运行 `uv sync --locked --reinstall`，再在 App 中重新选择项目目录。虚拟环境中的可执行入口和可编辑安装会记录路径，仅修改 App 的设置不足以修复旧路径。

App 不会自动添加系统登录项。需要登录后启动时，可在 macOS 的「登录项」设置中自行添加已安装的 `Screen OCR.app`。

卸载时从菜单退出 App，删除 `~/Applications/Screen OCR.app`。删除 App 不会删除项目、运行时或其他自动化配置；如果不再需要命令行工具，可以另外删除项目目录。

## 排错

| 现象 | 检查方式 |
| --- | --- |
| ⌥S 没有反应 | 检查菜单中的快捷键状态，停用冲突绑定后重新注册。也可先从菜单触发截图。 |
| 截图失败或屏幕内容不可见 | 检查系统对 Screen OCR 的屏幕录制授权，授权变更后按系统要求重新打开 App。 |
| 提示 OCR 目录未准备好 | 选择包含 `.venv` 与 `.runtime` 的项目根目录，先在终端运行 README 的图片识别示例。 |
| 移动项目后识别失败 | 在新目录执行 `uv sync --locked --reinstall`，然后重新选择项目目录。 |
| 取消后没有文字复制 | 这是预期行为，取消保留原剪贴板；等待当前任务清理结束后再截图。 |

原生界面和 OCR 运行时的文件布局见[架构说明](architecture.md)。屏幕授权接口行为可参阅 Apple 的 [CGRequestScreenCaptureAccess 文档](https://developer.apple.com/documentation/coregraphics/cgrequestscreencaptureaccess())。
