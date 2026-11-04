# Hammerspoon：⌥S 截图后复制文字

先完成仓库根目录 README 的安装和图片识别步骤。这个模块调用 screen-ocr CLI，截图只放在临时目录，结束后清理；不会保存 OCR 历史或输出全文日志。

1. 安装并启动 [Hammerspoon](https://www.hammerspoon.org/)。按 macOS 提示授予 Hammerspoon「辅助功能」与「屏幕录制」权限；更改权限后可能需要退出并重新打开 Hammerspoon。
2. 在 screen-ocr 仓库目录执行：

   ```sh
   mkdir -p "$HOME/.hammerspoon"
   cp -i integrations/hammerspoon/screen_ocr.lua "$HOME/.hammerspoon/screen_ocr.lua"
   pwd
   command -v uv
   ```

   `cp -i` 会在同名文件存在时询问是否覆盖。记下最后两条命令输出的绝对路径。模块不使用交互式 shell 的 PATH，也不自动展开 `~`。

3. 通过 Hammerspoon 菜单的 **Open Config** 打开 `~/.hammerspoon/init.lua`，添加以下配置，并替换两个路径：

   ```lua
   if screenOcr then screenOcr:stop() end
   screenOcr = require("screen_ocr").new({
       projectDir = os.getenv("HOME") .. "/Projects/screen-ocr",
       uvPath = "/opt/homebrew/bin/uv", -- 替换为 command -v uv 的实际输出
   }):start()
   ```

   `projectDir` 必须指向已运行 `uv sync --locked`、已生成 `.runtime` 的仓库目录。路径可以包含中文或空格。若你已经绑定了 ⌥S，请先移除旧绑定，避免冲突。

4. 点击 **Reload Config**。按 **⌥S（Option+S）**，拖动选择截图区域。完成后看到「screen-ocr 已复制到剪贴板」，即可按 ⌘V 粘贴。按 Escape 可取消截图；识别为空或失败时保留原剪贴板。

识别期间再次按 ⌥S 只会提示等待，不会启动重复任务。模块使用离线模式调用已有虚拟环境，每次按键不会安装依赖或下载模型。CLI 的默认超时为 90 秒；异常会显示提示，详细错误在 **Hammerspoon Console** 中。

如遇截图无反应，检查屏幕录制权限；若启动时报路径错误，核对 `pwd`、`command -v uv` 与上述配置，并先在终端成功运行仓库 README 的测试命令。

更新仓库后重新运行 `uv sync --locked`，再复制新版 `screen_ocr.lua` 并 Reload Config。停用可在 Console 执行 `screenOcr:stop()`；卸载时删除 `init.lua` 中上述配置，Reload Config，再删除 `~/.hammerspoon/screen_ocr.lua`。模块会在正常 Reload Config / 退出时取消当前任务；进程崩溃或强制退出可能留下系统临时文件。
