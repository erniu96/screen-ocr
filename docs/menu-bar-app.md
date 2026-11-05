# 菜单栏 App：Screen OCR

Screen OCR 是一个原生 Swift / AppKit 菜单栏应用。启动后只显示菜单栏图标，不显示 Dock 图标；按 **⌥S（Option + S）** 框选区域，识别完成后直接复制文字。

App 管理快捷键、截图、任务状态和剪贴板。Swift 工作进程负责 OCR 协议通信、结果解析和阅读顺序整理，少量 C 代码连接原生引擎。运行文件随本地构建的 App 打包，启动和识别不依赖 Python、uv、`.venv`、项目目录、Hammerspoon 或 HTTP 服务。

## 安装

先准备[项目 README](../README.md)指定版本的本地运行文件。首次下载、提取的辅助脚本仍使用 Python；如果已经有 `.runtime`，无需创建 Python 环境或再下载。下面的构建、安装与 App 运行均不调用 Python。

以下命令均在项目根目录执行。构建需要 Swift 6 或更新版本及 Xcode Command Line Tools；目标为 Apple Silicon Mac。App 的部署目标是 macOS 13 或更新版本，OCR 运行时的实测系统范围仍以项目 README 为准。

```sh
swift scripts/build_macos_app.swift --install --open
```

构建产物位于 `dist/Screen OCR.app`，`--install` 将应用安装到当前用户的 `~/Applications/Screen OCR.app`，`--open` 在安装后启动。之后可以直接从 Finder 打开 App，无需选择项目目录。

默认从项目的 `.runtime` 读取文件，也可以显式指定包含 `Frameworks/`、`Resources/` 的本地运行文件目录：

```sh
swift scripts/build_macos_app.swift --runtime "/完整路径/运行文件目录" --install --open
```

构建脚本先校验固定基线中的每个文件，再编译原生入口、复制 Swift 工作进程与模型，并为本地 App 签名。源目录不会被修改。App 中的 `Contents/Resources/Runtime/runtime.json` 分别记录原始文件的 `source_sha256` 和打包签名后的 `sha256`；动态库的签名会改变文件字节，模型内容不变。

安装完成后，移动项目或移除其 `.venv` 不影响 App；再次构建时仍需要源代码和本地运行文件。仓库不包含第三方动态库、模型或完整 App 下载，构建产物只保存在本机忽略目录中。当前使用 ad-hoc 签名，尚未完成 App Store 沙盒适配、正式分发签名或公证；本地打包不代表获得第三方文件的再分发授权。

## 第一次使用

1. 点击菜单栏中的 Screen OCR 图标，或直接按 **⌥S**。
2. 首次截图时，按系统提示为 **Screen OCR** 授予屏幕录制权限。此前授予其他应用的权限不会自动转移到这个 App。
3. 如果已拒绝授权，从 App 菜单打开系统设置，在「隐私与安全性」中的屏幕录制项目启用 Screen OCR。按系统提示退出并重新打开应用，然后再次截图。
4. 拖动框选文字区域。识别完成后自动复制，并弹出包含完整识别文字的预览窗口。窗口不会自动抢走原应用的键盘焦点，可以直接按 **⌘V** 粘贴。

预览保留原始换行，长内容可滚动查看、选中复制，也可拖动窗口或调整大小。它不会定时消失；点击关闭按钮或开始下一次截图时收起。

截图时按 **Esc** 可取消。识别期间可从菜单取消当前任务；取消、识别失败或结果为空时，原剪贴板保持不变。同一时间只处理一张图片，再次触发不会积累等待任务。

截图和文本结果使用临时文件，任务结束后清理；App 不保存识别历史。正常退出会先取消当前任务并清理。系统强制终止或崩溃时，临时目录可能留待系统清理。

快捷键通过 macOS 的全局快捷键注册接口实现，App 不使用键盘事件监听，不主动申请辅助功能权限。屏幕录制授权由系统管理，App 不能代替用户授予。

## 从旧快捷键配置迁移

如果已有其他应用绑定 ⌥S，先停用它的 OCR 快捷键，再使用 Screen OCR 菜单重新注册快捷键或重新启动 App。

已有 Hammerspoon 配置时，只需删除或注释该 OCR 模块的初始化调用与对应快捷键绑定，然后 **Reload Config**；其他自动化配置可以保留。旧 OCR 模块本身和历史文件无需删除。如何恢复旧集成见 [Hammerspoon 集成说明](../integrations/hammerspoon/README.md)。

快捷键冲突时，仍可通过菜单开始截图。不要同时启用两个应用的同一 OCR 快捷键。

## 更新与卸载

更新源代码后，退出菜单栏 App，再执行：

```sh
git pull --ff-only
swift scripts/build_macos_app.swift --install --open
```

脚本拒绝覆盖仍在运行的 App、符号链接或占用同一路径的其他应用，并在复制和签名校验完成后替换旧版本。更新运行文件后也需要重新构建，已安装 App 使用自己包内的副本。

App 不会自动添加系统登录项。需要登录后启动时，可在 macOS 的「登录项」设置中自行添加已安装的 `Screen OCR.app`。

卸载时从菜单退出 App，删除 `~/Applications/Screen OCR.app`。删除 App 不会删除项目、运行时或其他自动化配置；如果不再需要命令行工具，可以另外删除项目目录。

## 排错

| 现象 | 检查方式 |
| --- | --- |
| ⌥S 没有反应 | 检查菜单中的快捷键状态，停用冲突绑定后重新注册。也可先从菜单触发截图。 |
| 截图失败或屏幕内容不可见 | 检查系统对 Screen OCR 的屏幕录制授权，授权变更后按系统要求重新打开 App。 |
| 构建时提示文件缺失或 checksum mismatch | 检查 `--runtime` 指定目录中的固定版本文件，按 README 重新准备；不要跳过校验或混用版本。 |
| App 提示内置运行文件缺失 | 使用完整的 `Screen OCR.app`，从正确运行文件目录重新构建安装，不要单独复制内部可执行文件。 |
| 取消后没有文字复制 | 这是预期行为，取消保留原剪贴板；等待当前任务清理结束后再截图。 |

原生界面和 OCR 运行时的文件布局见[架构说明](architecture.md)。屏幕授权接口行为可参阅 Apple 的 [CGRequestScreenCaptureAccess 文档](https://developer.apple.com/documentation/coregraphics/cgrequestscreencaptureaccess())。
