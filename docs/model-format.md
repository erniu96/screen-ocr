# 模型格式与可读取的信息

当前运行时的 9 个 `.xnet` 文件可以读取头部和部分结构信息，但本项目尚未实现完整计算图、权重与算子参数的独立解析器。模型格式包含 `metaData` 字段；这 9 个样本的该字段都为空。

以下结论来自对基线版本 **4.2.0 / build 270352** 文件的实际读取，以及对应 ARM64 原生库的只读符号与反汇编检查。文件大小和完整 SHA-256 见[运行时文件清单](runtime-files.md)。它们是针对已验证版本的事实，不是根据扩展名或相似字段猜测的兼容规则。

运行以下命令可输出每个模型的 SHA-256、已确认的头部字段，以及字符表统计，不执行原生引擎：

```sh
uv run scripts/inspect_models.py .runtime/Resources
```

解析器仅处理确认过的格式版本 `26`。其他版本报告 `unsupported_version`，截断或不合法的前缀报告错误，并返回非零退出码。原始 `metaData` 以十六进制表示，不猜测其编码或业务字段；字符串超过 4,096 字节时仅报告长度，内容为 `null`，以限制报告大小。算子名称按 UTF-8 严格解码（当前样本均为 ASCII），非法字节不会被替换成占位字符。

## 已确认的头部契约

| 顺序 | 类型 | 内容 |
| --- | --- | --- |
| 1 | 4 字节 | ASCII `XNET`，即 `58 4e 45 54` |
| 2 | little-endian uint32 | 模型格式版本，当前为 `26` |
| 3 | little-endian uint32 + 指定长度字节 | `metaData` 字符串的字节长度和内容 |
| 4 | little-endian uint32 | `operators` 列表元素数量 |
| 5 | little-endian uint32 + 指定长度字节 | 第一个算子的名称，随后是该算子的其他字段 |

当前所有样本的 `metaData` 字节长度为 `0`，所以算子数量恰好位于文件偏移 `12`，第一个算子的名称长度位于偏移 `16`。不能把这两个偏移视为所有未来文件的固定位置：有非空 `metaData` 时，它们会后移。

格式版本 `26` 不是上游应用版本，也不是文件名中的 `V1.7`、`v2` 或模型训练版本。不同层面的版本不能互相替代。

| 文件 | 格式版本 | metaData 字节数 | 算子数 | 首个算子名称 |
| --- | ---: | ---: | ---: | --- |
| `text_det_fp16_v2.xnet` | 26 | 0 | 158 | `Conv_0_(+)_Relu_1` |
| `text_rec_zh13562_fp16_v2.xnet` | 26 | 0 | 94 | `/encoder/features.0/features.0.0/Conv_(+)_/encoder/features.0/features.0.1/Relu` |
| `paragraph_det_fp16_v2.xnet` | 26 | 0 | 97 | `/backbone/layer0/conv/Conv_(+)_HardSwish_0` |
| `QBarDetV1.7.0.26.xnet` | 26 | 0 | 177 | `Conv_0_(+)_LeakyRelu_1` |
| `QBarSegV1.1.0.26.xnet` | 26 | 0 | 78 | `Conv_0_(+)_Relu_1` |
| `DeepSR_REPSR_R_M4C16_Flip_ep24.xnet` | 26 | 0 | 11 | `Div_0` |
| `FPMMFG1V1.2.0.26.xnet` | 26 | 0 | 69 | `Conv_0_(+)_Relu_1` |
| `FPMMFG2V1.3.1.26.xnet` | 26 | 0 | 105 | `Conv_8_(+)_Relu6_0` |
| `PrivacyDetectV8.0.26.xnet` | 26 | 0 | 322 | `/model.0/conv/Conv` |

文件中还存在明文的算子与参数名称，如 `Conv`、`Relu`、`encoder/features`、`backbone.backbone.0.conv3x3.weight`。因此不能把这些文件描述为“整体加密、完全不可读”；同时，看到部分明文也不能证明所有权重区都没有压缩、量化或专有编码。

## 字符表是可以完整读取的

`charset_zh13562.txt` 是普通 UTF-8 文本，无 BOM：

- 文件大小为 **54,258 字节**。
- `splitlines()` 得到 **13,563 个条目**，每个条目恰好一个 Unicode 码点，条目互不重复。
- 最后一个条目是一个普通空格。读取时不能对每行执行 `strip()`，否则会丢失这个有效条目。

文件名中的 `13562` 不等于实际条目数。字符表与识别模型的索引对应方式仍由引擎决定，不能仅从文件名推导类别数或 CTC blank 位置。

可以在项目目录执行以下只读检查：

```sh
uv run python - <<'PY'
from pathlib import Path

data = Path(".runtime/Resources/charset_zh13562.txt").read_bytes()
entries = data.decode("utf-8").splitlines()
print("bytes:", len(data))
print("entries:", len(entries))
print("unique entries:", len(set(entries)))
print("last entry:", repr(entries[-1]))
PY
```

## 解析与转换的实际边界

对应原生库包含 `xnet::dnn::DnnModel::LoadBin`、`SaveBin`、`SaveDOTLang` 和 `OnnxToXNet` 符号。这证明引擎有自己的 XNet 模型加载/保存路径，并含 ONNX 转入相关代码；不代表存在可直接使用的 XNet → ONNX 转换器。

当前已确认的字段不足以完成独立推理或无损模型转换。完整解析还需要验证每类算子的编号、参数布局、张量形状、权重存储类型和连接关系，以及输入预处理与输出后处理。文件名中的 `fp16` 是命名信息，不能据此宣布所有张量都使用 FP16。

本次没有获得可用于完整解码这一格式的公开上游规范，也没有从这些样本的 `metaData` 字段读出作者、训练日期、训练数据来源或许可证信息。保留原始字节和逐文件 SHA-256，足以判断文件是否变化，但哈希不同不能说明准确率更高或可与旧引擎混用。

## 证据与复核方式

检查对象为 `Resources/libwxocr.dylib` 的 ARM64 slice，整个原文件 SHA-256 为：

```text
a0056c2a1cbf7c8017301d60e3bb35f07611073fe99a41ca6d62ea15657cc204
```

基线二进制中的关键位置（静态虚拟地址）：

- `DnnModel::LoadBin(SequentialInputStream&, bool)`，`0x62e334`，进入内部读取路径。
- 内部读取函数 `0x61e17c`：读取头部，再读取标注为 `metaData` 的字符串和标注为 `operators` 的列表。
- `DnnParamsValidator<DnnModelHeader>::Validate`，`0x62cd10`：检查 `XNET` magic 和格式版本 `26`。
- 字符串读取路径 `0x405ab0` → `0x404d98`：读取 uint32 长度，随后读取对应字节。
- 算子列表读取路径 `0x61e32c`：读取元素数量，再逐项读取名称及算子字段。

这些地址只适用于上面的确切文件。调试器对没有符号的内部地址可能显示“前一个符号 + 偏移”，不能把这种邻近符号标签当成函数的真实名称。

可用系统工具复核，无需运行模型或加载执行第三方库：

```sh
xxd -l 32 .runtime/Resources/text_det_fp16_v2.xnet
nm -C .runtime/Resources/libwxocr.dylib | rg 'DnnModel.*(LoadBin|SaveBin|SaveDOTLang)'
xcrun lldb -b \
  -o 'target create --arch arm64 .runtime/Resources/libwxocr.dylib' \
  -o 'disassemble --start-address 0x62cd10 --count 24' \
  -o quit
```

`file` 命令并不能可靠识别这里的专有模型格式；它曾将其中一个文件误报为热成像原始数据。判断依据应是实际头部与经过核对的读取代码。
