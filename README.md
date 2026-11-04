# screen-ocr

在 Apple Silicon Mac 上原生运行 OCR，识别截图中的中文、英文和数字。轻量的 **Screen OCR 菜单栏 App** 提供 **⌥S 框选截图 → 识别 → 复制到剪贴板**，同时保留命令行和本机 HTTP API。

菜单栏界面使用 Swift / AppKit，不需要 Hammerspoon。空闲时只有 App 常驻，Python 与 OCR 引擎在识别时按需启动。

识别在本机完成，无需运行 Linux 容器。项目调用第三方 macOS ARM64 动态库和模型；仓库提供调用代码，不包含第三方动态库、模型或安装包。首次安装时，准备脚本从固定版本的上游官方安装包提取所需文件，具体来源与权属见[第三方来源说明](docs/THIRD_PARTY_NOTICES.md)。

## 使用条件

- **Apple Silicon Mac**（M 系列芯片）。Intel Mac 和通过 Rosetta 运行的 x86_64 Python 不受支持。
- Xcode Command Line Tools，用于编译一个很小的原生入口程序。
- [uv](https://docs.astral.sh/uv/getting-started/installation/)，用于管理 Python 和项目环境。
- 首次安装可联网，预留约 2 GB 临时空间。源安装包约 519 MiB，提取后的运行时约 85 MiB。
- 构建菜单栏 App 需要 Swift 6 或更新版本。仅识别已有图片或使用 API 时，无需构建 App。

当前实测环境为 macOS 27.0.1、ARM64、Python 3.12。其他 macOS 版本尚未完整验证；项目目前只接受 **源应用 4.2.0，build 270352** 的运行时。

## 一步一步安装

以下命令都在 macOS 的「终端」中运行。

### 1. 安装命令行工具

```sh
xcode-select --install
```

按系统弹窗完成安装。已经安装过时，系统会提示无需重复安装。完成后检查：

```sh
xcrun clang --version
uname -m
```

第二条应输出 `arm64`。如果输出 `x86_64`，请先退出以 Rosetta 运行的终端。命令行工具的安装说明见 [Apple 官方文档](https://developer.apple.com/documentation/xcode/installing-the-command-line-tools/)。

需要菜单栏 App 时，再运行 `xcrun swift --version`，确认 Swift 版本至少为 6。

### 2. 安装 uv

尚未安装 uv 时，运行其[官方安装命令](https://docs.astral.sh/uv/getting-started/installation/)：

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

安装完成后重新打开终端，检查：

```sh
uv --version
```

如果已经使用 Homebrew，也可以用 `brew install uv` 安装；两种方式选一种即可。

### 3. 下载项目并创建 Python 环境

```sh
mkdir -p "$HOME/Projects"
cd "$HOME/Projects"
git clone https://github.com/erniu96/screen-ocr.git
cd screen-ocr
uv python install 3.12
uv sync --locked
```

后续命令默认在 `screen-ocr` 项目目录中执行。路径可以包含空格，引用路径时请保留双引号。

### 4. 准备本机 OCR 运行时

```sh
uv run scripts/prepare_runtime.py --download
```

这一步会下载固定版本的上游官方 DMG，校验 SHA-256，临时只读挂载，提取 OCR 库和模型，并编译 ARM64 入口程序。完成后会看到 `Prepared native runtime: .../.runtime`。下载和挂载的临时文件会清理，`.runtime` 留在项目目录中。

**不需要安装、启动或登录源应用。** 此后直接识别已有图片，无需每次下载。

如果已有[第三方来源说明](docs/THIRD_PARTY_NOTICES.md)中列出的同一版本官方安装包，可以改用以下命令，并替换为文件的实际绝对路径：

```sh
uv run scripts/prepare_runtime.py --dmg "/完整路径/安装包.dmg"
```

也可以从已经安装的匹配版本提取，脚本不会修改该应用。将路径替换为对应的应用包：

```sh
uv run scripts/prepare_runtime.py --app "/完整路径/源应用.app"
```

版本、build 或动态库校验不匹配时脚本会拒绝继续；不要用其他版本文件混合替换 `.runtime`。

### 5. 识别仓库里的示例图片

```sh
uv run screen-ocr recognize tests/fixtures/english.png --text
```

终端应输出两行英文示例文字，与 `tests/fixtures/english.png` 中的内容一致。

再试中文：

```sh
uv run screen-ocr recognize tests/fixtures/chinese.png --text
```

能得到文字就说明安装成功。示例图片是生成的测试样本。

## 日常使用

### ⌥S 截图识别并自动复制

完成上面的命令行示例后，在项目目录运行：

```sh
uv run scripts/build_macos_app.py --install --open
```

这会构建原生 App，安装到 `~/Applications/Screen OCR.app`，并记住当前 OCR 项目目录。之后可以直接从 Finder 打开 App。

1. 点击菜单栏 Screen OCR 图标，或按 **⌥S（Option + S）**。
2. 首次使用时，为 **Screen OCR** 授予屏幕录制权限。此前授予其他应用的权限不会自动转移；如果系统要求，请退出并重新打开 App。
3. 拖动框选文字区域。出现复制成功提示后，按 **⌘V** 粘贴。

截图时按 **Esc** 取消；识别期间也可从菜单取消本次任务。一次只执行一个任务，不积累等待队列。识别失败、取消或没有文字时保留原剪贴板；临时截图与文本结果在任务结束后清理。

App 不使用键盘事件监听，不主动申请辅助功能权限。空闲时 Python 和模型不常驻，也无需启动 HTTP 服务。如果已有其他应用绑定 ⌥S，先停用旧绑定，再从菜单重新注册快捷键。安装、授权、目录选择和迁移步骤见[菜单栏 App 说明](docs/menu-bar-app.md)。

如需继续使用已有自动化配置，可选的 [Hammerspoon 集成](integrations/hammerspoon/README.md)仍保留。

### 识别图片或复制到剪贴板

把图片路径替换成你自己的文件：

```sh
uv run screen-ocr recognize "$HOME/Desktop/screenshot.png" --text
```

确认识别成功后，把结果复制到剪贴板：

```sh
text=$(uv run screen-ocr recognize "$HOME/Desktop/screenshot.png" --text) && printf '%s' "$text" | pbcopy
```

`--text` 按坐标恢复从上到下、同行从左到右的顺序，并拼接相邻片段。它保留引擎识别出的字符，不纠正错别字；复杂多栏、表格、旋转文字及精确空格仍可能需要人工检查。

不加 `--text` 时输出 JSON，保留原始片段顺序、坐标和置信度：

```sh
uv run screen-ocr recognize tests/fixtures/english.png
```

需要保存纯文本时可用 `--text-file output.txt`，输出 UTF-8 文件而不写入标准输出。它与 `--text` 二选一，并拒绝覆盖已有文件。截图模块也使用这一方式传递长文本。

从项目目录以外调用时，同时指定项目和运行时的绝对路径：

```sh
uv run --project "$HOME/Projects/screen-ocr" screen-ocr recognize \
  "$HOME/Desktop/screenshot.png" \
  --runtime "$HOME/Projects/screen-ocr/.runtime" --text
```

### 本机 HTTP API

在项目目录启动服务，保持这个终端运行：

```sh
uv run screen-ocr serve --port 5000
```

另开终端，进入项目目录并发送一张图片：

```sh
cd "$HOME/Projects/screen-ocr"
uv run python - <<'PY'
import base64
import json
from pathlib import Path
from urllib.request import Request, urlopen

image = Path("tests/fixtures/english.png")
body = json.dumps({"image": base64.b64encode(image.read_bytes()).decode("ascii")}).encode()
request = Request(
    "http://127.0.0.1:5000/ocr",
    data=body,
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urlopen(request, timeout=100) as response:
    print(json.dumps(json.load(response), ensure_ascii=False, indent=2))
PY
```

接口约定：

| 项目 | 内容 |
| --- | --- |
| 请求 | `POST /ocr`，`Content-Type: application/json` |
| 请求体 | `{"image": "图片的 base64"}`，不带 `data:image/...;base64,` 前缀 |
| 成功响应 | `{"result": {"imgpath": "...", "errcode": 0, "width": 1000, "height": 260, "ocr_response": [...]}}` |
| 片段字段 | `text`、`left`、`top`、`right`、`bottom`、`rate` |
| 大小限制 | 整个 JSON 请求体最多 20 MiB，包含 base64 的膨胀开销 |
| 并发 | 默认最多 2 个识别任务，超出返回 HTTP 503；可设 `--max-concurrent 1` |
| 超时 | 原生识别任务默认 90 秒，可设 `--timeout 30`；超时返回 HTTP 504 |

HTTP 200 代表返回了引擎结果，调用方还应检查 `result.errcode`。`errcode == 0` 且 `ocr_response == []` 表示成功但没有文字。HTTP 返回的 `imgpath` 是已经清理的临时路径，不是可下载地址。

API 的 `ocr_response` 保留原始顺序。需要和 `--text` 相同的拼接结果时，可在 Python 中调用 `screen_ocr.layout.reconstruct_text(result)["text"]`，见[架构与接口说明](docs/architecture.md)。

按 **Ctrl+C** 停止服务。服务默认只监听 `127.0.0.1`，没有认证和 TLS，适用于本机调用。

## 文件校验与模型信息

运行时包含 12 个第三方文件：2 个动态库、9 个 `.xnet` 模型文件和 1 个字符表，合计约 85.10 MiB。完整文件名、大小与 SHA-256 见[文件清单](docs/runtime-files.md)，机器可读基线保存在 [runtime-baseline.json](src/screen_ocr/data/runtime-baseline.json)。安装器会校验全部 12 个文件。

重新检查本机文件，或只读比较候选版本：

```sh
uv run scripts/inspect_runtime.py --runtime .runtime
uv run scripts/inspect_runtime.py --dmg "/完整路径/候选版本.dmg" > candidate.json
```

比较结果逐项区分相同、变化、缺失；检查候选版本不会替换正在使用的运行时。哈希相同表示文件字节相同，哈希变化不代表必须更新或已经兼容。

查看模型头部与字符表信息：

```sh
uv run scripts/inspect_models.py .runtime/Resources
```

当前 9 个模型均使用 XNET 格式版本 26，`metaData` 字段均为空。工具可读取已验证的头部、算子数量及首个算子名称，不是完整计算图或权重解析器。细节与证据见[模型格式说明](docs/model-format.md)。

## 常见问题

**需要预热或一直开着服务吗？**

不需要先执行预热命令。CLI 和截图快捷键按需启动引擎，每次请求结束后退出。HTTP 服务也为每次识别创建独立工作进程，没有常驻预热模型。耗时包含启动、识别与清理，会随图片和机器状态变化。

**提示 `Missing runtime file`？**

先运行 `uv run scripts/prepare_runtime.py --download`。如果 `.runtime` 已经准备好，检查当前目录，或通过 `--runtime` 指向正确的绝对路径。

**提示 `Runtime already exists`？**

准备脚本不会覆盖现有运行时。已有运行时正常时不必重建；需要重新准备时，先将它改名备份，例如 `mv .runtime .runtime.backup`，再运行准备命令。确认新运行时可用后再删除备份。

**提示版本不匹配或 checksum 不一致？**

目前只验证了源应用 4.2.0 build 270352。优先使用 `--download` 获取固定版本；下载中断时重试。若上游替换或移除了该安装包，请提交 issue，不要跳过校验或手动换入未知库。

**⌥S 没反应、截图失败或屏幕全黑？**

确认菜单栏 App 正在运行，查看菜单中的快捷键状态，并检查 Screen OCR 的屏幕录制权限。可以先从菜单点击「截图并复制」，区分快捷键冲突与截图问题。若提示 OCR 目录未准备好，重新选择项目目录，并在终端运行示例识别。

**路径里有空格可以用吗？**

可以。终端中的路径加双引号；App 通过目录选择器保存完整路径，启动子进程时直接传递参数，不拼接 shell 命令。

## 更新与卸载

从旧名称版本升级时，在原项目目录执行 `git remote set-url origin https://github.com/erniu96/screen-ocr.git`，然后更新代码和环境。命令现为 `screen-ocr`。项目目录可以保留原路径，在菜单栏 App 中选择其实际位置即可；使用 App 前请停用旧 OCR 快捷键绑定。

原生入口程序也已改名。可以复用已有运行时中的第三方文件，逐个校验后重新编译入口，无需重新下载安装包：

```sh
uv run scripts/prepare_runtime.py --runtime .runtime --output .runtime-next
```

确认命令成功且 `.runtime.backup` 不存在后，退出菜单栏 App 并停止正在运行的识别任务，将旧运行时备份并替换为新版本：

```sh
mv .runtime .runtime.backup
mv .runtime-next .runtime
```

如果校验失败，保留旧运行时，按安装第 4 步准备固定版本的新运行时；不要跳过校验。

更新项目代码前，先退出菜单栏 App：

```sh
cd "$HOME/Projects/screen-ocr"
git pull --ff-only
uv sync --locked
```

使用菜单栏 App 时，继续运行以下命令重新构建并打开：

```sh
uv run scripts/build_macos_app.py --install --open
```

代码更新不会自动替换 `.runtime`；只有发布说明明确要求时才重新准备运行时。移动项目目录后，应在新目录运行 `uv sync --locked --reinstall`，再在 App 中重新选择项目目录。

停用快捷键：从菜单退出 App。卸载 App 时删除 `~/Applications/Screen OCR.app`；如不再使用命令行或 HTTP 服务，停止服务后可另外删除项目目录，其中的 `.venv` 和 `.runtime` 会一起删除。其他自动化配置无需删除。

## 开发与测试

原生菜单栏 App 的进程管理与取消测试：

```sh
swift test --package-path apps/macos
```

Python 核心与接口测试：

```sh
uv run python -m unittest discover -s tests -v
```

加入可选的 Lua 测试运行器，覆盖 Hammerspoon 的截图、取消与剪贴板生命周期：

```sh
uv run --locked --with lupa==2.8 python -m unittest discover -s tests -v
```

未安装 `lupa` 时，普通测试命令会跳过 Hammerspoon 测试，不影响 CLI 和 HTTP 测试。

准备好运行时后，运行真实引擎集成测试：

```sh
SCREEN_OCR_RUN_NATIVE=1 uv run python -m unittest discover -s tests -v
```

实现原理、输出字段和阅读顺序限制见 [架构说明](docs/architecture.md)。提交问题时请附上 macOS 版本、`uname -m`、命令及错误信息；图片请使用能够公开的最小复现样本。

## 许可与致谢

本项目自行实现的代码见 [LICENSE](LICENSE)。第三方程序、动态库和模型不属于本项目的开源许可范围，需遵守其各自适用条款。本项目与上游厂商无隶属关系，不承诺兼容未验证的运行时版本。

协议研究参考、接口行为来源及相关权属边界见[第三方来源说明](docs/THIRD_PARTY_NOTICES.md)。
