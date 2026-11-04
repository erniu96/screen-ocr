-- All UI, process and file effects are isolated behind fake Hammerspoon APIs.
local modulePath = assert(TEST_MODULE_PATH)
local passed = 0
local project = "/Projects/中文 OCR"
local uv = "/Applications/Local Tools/uv"

local function fresh(start)
    local state = {tasks = {}, copied = {}, removed = {}, alerts = {}, bindings = {}, logs = {},
        files = {}, contents = {}, uuid = 0, copyOK = true, previousCalls = 0}
    state.files[project] = "directory"
    state.files[uv] = "file"
    state.files[project .. "/.venv/bin/wxocr"] = "file"
    state.files[project .. "/.runtime/runtime.json"] = "file"
    local fake = {
        shutdownCallback = function() state.previousCalls = state.previousCalls + 1 end,
        host = {uuid = function() state.uuid = state.uuid + 1; return tostring(state.uuid) end},
        fs = {
            temporaryDirectory = function() return "/tmp/" end,
            attributes = function(path, field)
                assert(field == "mode")
                return state.files[path]
            end,
        },
        alert = {
            show = function(message) state.alerts[#state.alerts + 1] = message; return #state.alerts end,
            closeSpecific = function() end,
        },
        pasteboard = {setContents = function(text)
            if state.copyOK then state.copied[#state.copied + 1] = text end
            return state.copyOK
        end},
        hotkey = {bind = function(mods, key, callback)
            local binding = {mods = mods, key = key, callback = callback}
            function binding:delete() self.deleted = true end
            state.bindings[#state.bindings + 1] = binding
            return binding
        end},
        task = {new = function(path, callback, args)
            if state.failNew then state.failNew = false; return nil end
            local task = {path = path, callback = callback, args = args, running = false,
                interrupts = 0, terminations = 0}
            function task:start()
                if state.failStart then state.failStart = false; return nil end
                self.running = true
                return self
            end
            function task:isRunning() return self.running end
            function task:setWorkingDirectory(path) self.directory = path; return self end
            function task:closeInput() self.inputClosed = true end
            function task:interrupt() self.interrupts = self.interrupts + 1 end
            function task:terminate() self.terminations = self.terminations + 1 end
            state.tasks[#state.tasks + 1] = task
            return task
        end},
    }
    state.hs = fake
    local environment = setmetatable({hs = fake,
        print = function(text) state.logs[#state.logs + 1] = text end,
        os = setmetatable({remove = function(path)
            state.removed[#state.removed + 1] = path
            state.files[path] = nil
            state.contents[path] = nil
            return true
        end}, {__index = os}),
        io = {open = function(path, mode)
            assert(mode == "rb")
            if state.contents[path] == nil then return nil, "missing result file" end
            return {
                read = function(_, format)
                    assert(format == "*a")
                    return state.contents[path]
                end,
                close = function() end,
            }
        end},
    }, {__index = _G})
    state.module = assert(loadfile(modulePath, "t", environment))()
    state.ocr = state.module.new({projectDir = project, uvPath = uv})
    if start ~= false then state.ocr:start() end
    function state:exit(task, code, stdout, stderr)
        task.running = false
        if task.path == uv and stdout ~= nil then
            self.contents[task.args[#task.args]] = stdout
        end
        task.callback(code, stdout or "", stderr or "")
    end
    function state:capture()
        assert(self.ocr:capture())
        return self.tasks[#self.tasks]
    end
    function state:recognize()
        local capture = self:capture()
        self.files[capture.args[3]] = "file"
        self:exit(capture, 0)
        return self.tasks[#self.tasks], capture.args[3]
    end
    return state
end

local function run(name, callback)
    local ok, detail = pcall(callback)
    if not ok then error(name .. ": " .. tostring(detail), 0) end
    passed = passed + 1
end

run("Option+S uses the explicit project and uv without shell parsing", function()
    local s = fresh()
    assert(s.bindings[1].mods[1] == "alt" and s.bindings[1].key == "s")
    s.bindings[1].callback()
    local capture = s.tasks[1]
    assert(capture.path == "/usr/sbin/screencapture" and capture.args[1] == "-i" and capture.args[2] == "-x")
    s.files[capture.args[3]] = "file"
    s:exit(capture, 0)
    local task = s.tasks[2]
    assert(task.path == uv and task.directory == project)
    assert(table.concat(task.args, "|") == table.concat({"run", "--offline", "--locked", "--no-sync",
        "--project", project, "wxocr", "recognize", capture.args[3], "--runtime", project .. "/.runtime",
        "--text-file", capture.args[3] .. ".txt"}, "|"))
end)

run("Success preserves OCR whitespace, copies once and removes image", function()
    local s = fresh()
    local task, path = s:recognize()
    local text = "  中文 first\nsecond line  "
    s:exit(task, 0, text)
    s:exit(task, 0, "duplicate\n")
    assert(#s.copied == 1 and s.copied[1] == text)
    assert(#s.removed == 2 and s.removed[1] == path and s.removed[2] == path .. ".txt" and s.ocr._active == nil)
    assert(#s.logs == 0, "OCR text must not be logged")
end)

run("No duplicate capture while capture or OCR is active", function()
    local s = fresh()
    local capture = s:capture()
    assert(not s.ocr:capture() and #s.tasks == 1)
    s.files[capture.args[3]] = "file"
    s:exit(capture, 0)
    assert(not s.ocr:capture() and #s.tasks == 2)
    s:exit(s.tasks[2], 0, "done\n")
    assert(s.ocr:capture() and #s.tasks == 3)
end)

run("Escape without a file silently releases capture", function()
    for _, code in ipairs({0, 1}) do
        local s = fresh()
        s:exit(s:capture(), code)
        assert(#s.tasks == 1 and #s.copied == 0 and #s.alerts == 0 and s.ocr._active == nil)
    end
end)

run("Screenshot error reports failure and leaves clipboard intact", function()
    local s = fresh()
    s:exit(s:capture(), 1, "", "could not create image from display")
    assert(#s.copied == 0 and #s.logs == 1 and #s.alerts == 1 and s.ocr._active == nil)
end)

run("Empty OCR result leaves clipboard intact", function()
    local s = fresh()
    s:exit(s:recognize(), 0, "\n")
    assert(#s.copied == 0 and s.ocr._active == nil and #s.removed == 2)
end)

run("Failed OCR never copies partial stdout", function()
    local s = fresh()
    s:exit(s:recognize(), 1, "partial\n", "engine failure")
    assert(#s.copied == 0 and #s.logs == 1 and s.ocr._active == nil and #s.removed == 2)
end)

run("Clipboard failure releases request without logging result", function()
    local s = fresh()
    s.copyOK = false
    s:exit(s:recognize(), 0, "private text\n")
    assert(#s.copied == 0 and #s.logs == 1 and not s.logs[1]:find("private text", 1, true))
    assert(s.ocr._active == nil)
end)

run("Capture launch failures release request", function()
    for _, failure in ipairs({"failNew", "failStart"}) do
        local s = fresh()
        s[failure] = true
        assert(not s.ocr:capture())
        assert(#s.copied == 0 and s.ocr._active == nil and #s.removed == 1)
    end
end)

run("OCR launch failures release request", function()
    for _, failure in ipairs({"failNew", "failStart"}) do
        local s = fresh()
        local capture = s:capture()
        s.files[capture.args[3]] = "file"
        s[failure] = true
        s:exit(capture, 0)
        assert(#s.copied == 0 and s.ocr._active == nil and #s.removed == 2)
    end
end)

run("Stop during capture suppresses late OCR and cleans late screenshot", function()
    local s = fresh()
    local capture = s:capture()
    s.ocr:stop()
    assert(capture.terminations == 1 and s.bindings[1].deleted and not s.ocr:capture())
    s.files[capture.args[3]] = "file"
    s:exit(capture, 0)
    assert(s.files[capture.args[3]] == nil and #s.tasks == 1 and #s.copied == 0)
end)

run("Stop during OCR sends SIGINT and ignores late clipboard result", function()
    local s = fresh()
    local task = s:recognize()
    s.ocr:stop()
    assert(task.interrupts == 1 and task.terminations == 0 and s.ocr._active == nil)
    s:exit(task, 0, "too late\n")
    assert(#s.copied == 0)
end)

run("Restart permits a new request without stale completion interference", function()
    local s = fresh()
    local old = s:recognize()
    s.ocr:stop():start()
    local new = s:recognize()
    s:exit(old, 0, "stale\n")
    assert(s.ocr._active ~= nil and #s.copied == 0)
    s:exit(new, 0, "current")
    assert(s.ocr._active == nil and s.copied[1] == "current")
end)

run("Start is idempotent and shutdown preserves prior callback", function()
    local s = fresh()
    s.ocr:start()
    assert(#s.bindings == 1)
    local task = s:recognize()
    s.hs.shutdownCallback()
    assert(s.previousCalls == 1 and task.interrupts == 1 and s.bindings[1].deleted)
    s.ocr:stop()
    assert(task.interrupts == 1)
end)

run("Invalid or missing paths fail before binding", function()
    local s = fresh(false)
    assert(not pcall(s.module.new, {projectDir = "~/Projects/wxocr", uvPath = uv}))
    assert(not pcall(s.module.new, {projectDir = project, uvPath = "uv"}))
    s.files[project .. "/.runtime/runtime.json"] = nil
    assert(not pcall(function() s.ocr:start() end) and #s.bindings == 0)
end)

run("Stop does not replace a shutdown callback installed later", function()
    local s = fresh()
    local later = function() end
    s.hs.shutdownCallback = later
    s.ocr:stop()
    assert(s.hs.shutdownCallback == later)
end)

run("Large Unicode results avoid stdout and are copied completely", function()
    local s = fresh()
    local task = s:recognize()
    local text = string.rep("中文识别结果", 32768)
    s.contents[task.args[#task.args]] = text
    task.running = false
    task.callback(0, "", "")
    assert(s.copied[1] == text and #s.removed == 2)
end)

run("Missing result file fails without replacing clipboard", function()
    local s = fresh()
    s:exit(s:recognize(), 0)
    assert(#s.copied == 0 and #s.logs == 1 and s.ocr._active == nil and #s.removed == 2)
end)

return {passed = passed}
