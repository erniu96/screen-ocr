-- Optional screenshot-to-clipboard integration for wxocr-native.
-- Configure absolute paths explicitly; tasks never invoke a shell.
local module = {}
local OCR = {}
OCR.__index = OCR

local function absolutePath(value, name)
    assert(type(value) == "string" and value:sub(1, 1) == "/" and not value:find("%z"),
        name .. " must be an absolute path (expand ~ yourself)")
    return value:gsub("/+$", "")
end

function module.new(config)
    assert(type(config) == "table", "wxocr.new requires projectDir and uvPath")
    return setmetatable({
        projectDir = absolutePath(config.projectDir, "projectDir"),
        uvPath = absolutePath(config.uvPath, "uvPath"),
        _stopped = true,
    }, OCR)
end

function OCR:_closeAlert()
    if self._alert then hs.alert.closeSpecific(self._alert); self._alert = nil end
end

function OCR:_show(message)
    self:_closeAlert()
    self._alert = hs.alert.show(message, 2)
end

function OCR:_isCurrent(request)
    return not self._stopped and not request.cancelled and self._active == request
end

function OCR:_finish(request)
    -- Also remove on late completion after stop(): screencapture may have
    -- written the image between cancellation and its exit callback.
    os.remove(request.path)
    if request.textPath then os.remove(request.textPath) end
    request.finished = true
    if self._active == request then self._active = nil end
end

function OCR:_fail(request, message, detail)
    if self:_isCurrent(request) then
        self:_show(message)
        print("[wxocr] " .. detail)
    end
    self:_finish(request)
end

function OCR:_recognize(request)
    if not self:_isCurrent(request) then self:_finish(request); return end
    self:_show("wxocr 正在识别…")
    request.textPath = request.path .. ".txt"
    local arguments = {
        "run", "--offline", "--locked", "--no-sync", "--project", self.projectDir,
        "wxocr", "recognize", request.path, "--runtime", self.projectDir .. "/.runtime",
        "--text-file", request.textPath,
    }
    request.ocrTask = hs.task.new(self.uvPath, function(exitCode, stdout, stderr)
        request.ocrTask = nil
        if request.finished then return end
        if not self:_isCurrent(request) then self:_finish(request); return end
        if exitCode ~= 0 then
            self:_fail(request, "wxocr 识别失败；请查看 Hammerspoon Console",
                string.format("OCR exited %d: %s", exitCode, stderr))
            return
        end
        -- A result file avoids pipe-capacity deadlocks and UTF-8 chunk splits
        -- in hs.task. Preserve recognized spaces and line breaks exactly.
        local file, openError = io.open(request.textPath, "rb")
        if not file then
            self:_fail(request, "无法读取 wxocr 结果", tostring(openError))
            return
        end
        local text, readError = file:read("*a")
        file:close()
        if not text then
            self:_fail(request, "无法读取 wxocr 结果", tostring(readError))
            return
        end
        if text:match("^%s*$") then
            self:_show("未识别到文字，剪贴板保持原样")
        elseif hs.pasteboard.setContents(text) then
            self:_show("wxocr 已复制到剪贴板")
        else
            self:_show("wxocr 无法写入剪贴板")
            print("[wxocr] Failed to write OCR text to clipboard")
        end
        self:_finish(request)
    end, arguments)
    if not request.ocrTask then
        self:_fail(request, "无法启动 wxocr", "hs.task.new failed for configured uvPath")
        return
    end
    request.ocrTask:setWorkingDirectory(self.projectDir)
    if not request.ocrTask:start() then
        request.ocrTask = nil
        self:_fail(request, "无法启动 wxocr", "uv task failed to start; check uvPath and project installation")
        return
    end
end

function OCR:capture()
    if self._stopped then return false end
    if self._active then self:_show("wxocr 正在处理上一张截图"); return false end
    self:_closeAlert()
    local request = {path = hs.fs.temporaryDirectory() .. "wxocr-" .. hs.host.uuid() .. ".png"}
    self._active = request
    request.captureTask = hs.task.new("/usr/sbin/screencapture", function(exitCode, _, stderr)
        request.captureTask = nil
        if request.finished then return end
        if not self:_isCurrent(request) then self:_finish(request); return end
        -- Escape exits without an image. No clipboard write or failure alert.
        if hs.fs.attributes(request.path, "mode") ~= "file" then
            if exitCode ~= 0 and stderr ~= "" then
                self:_fail(request, "截图失败；请检查屏幕录制权限", stderr)
            else
                self:_finish(request)
            end
        elseif exitCode ~= 0 then
            self:_fail(request, "截图失败", string.format("screencapture exited %d: %s", exitCode, stderr))
        else
            self:_recognize(request)
        end
    end, {"-i", "-x", request.path})
    if not request.captureTask or not request.captureTask:start() then
        request.captureTask = nil
        self:_fail(request, "无法开始截图", "screencapture task failed to start")
        return false
    end
    return true
end

function OCR:start()
    if self._hotkey then return self end
    assert(hs.fs.attributes(self.projectDir, "mode") == "directory", "projectDir is not a directory")
    assert(hs.fs.attributes(self.uvPath, "mode") == "file", "uvPath is not a file; run command -v uv")
    assert(hs.fs.attributes(self.projectDir .. "/.venv/bin/wxocr", "mode") == "file",
        "wxocr environment missing; run uv sync --locked in projectDir")
    assert(hs.fs.attributes(self.projectDir .. "/.runtime/runtime.json", "mode") == "file",
        "wxocr runtime missing; run uv run scripts/prepare_runtime.py --download in projectDir")
    self._hotkey = assert(hs.hotkey.bind({"alt"}, "s", function() self:capture() end), "cannot bind Option+S")
    self._stopped = false
    local previous = hs.shutdownCallback
    self._previousShutdown = previous
    self._shutdown = function()
        local ok, error = pcall(function() self:stop() end)
        if not ok then print("[wxocr] Shutdown cleanup failed: " .. tostring(error)) end
        if type(previous) == "function" then previous() end
    end
    hs.shutdownCallback = self._shutdown
    return self
end

function OCR:stop()
    self._stopped = true
    if self._hotkey then self._hotkey:delete(); self._hotkey = nil end
    if self._shutdown and hs.shutdownCallback == self._shutdown then
        hs.shutdownCallback = self._previousShutdown
    end
    self._shutdown, self._previousShutdown = nil, nil
    self:_closeAlert()
    local request = self._active
    self._active = nil
    if request then
        request.cancelled = true
        if request.captureTask and request.captureTask:isRunning() then request.captureTask:terminate() end
        -- uv forwards SIGINT to the CLI, whose finally block reaps native
        -- worker process groups. Hammerspoon may additionally send SIGTERM
        -- during reload; the CLI handles both signals.
        if request.ocrTask and request.ocrTask:isRunning() then request.ocrTask:interrupt() end
        os.remove(request.path)
        if request.textPath then os.remove(request.textPath) end
    end
    return self
end

return module
