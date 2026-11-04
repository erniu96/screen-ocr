# 运行时文件与 SHA-256

当前固定版本为源应用 **4.2.0，build 270352，arm64**。从中提取 **12 个文件**：2 个动态库、9 个 `.xnet` 文件和 1 个字符表，总计 **89,229,802 字节（约 85.10 MiB）**。

本项目另外编译 `bin/screen-ocr-engine`，并生成 `runtime.json`；这两个文件不属于源应用提取文件。helper 的哈希会随编译器、编译选项和源码变化，因此不纳入下面的第三方文件基线。

以下 SHA-256 已对本机实际文件重新计算。机器可读的唯一权威基线是 [runtime-baseline.json](../src/screen_ocr/data/runtime-baseline.json)，安装脚本和比较工具共同读取它。安装时会核对全部 12 个文件，而不只检查动态库。

| 路径（相对运行时根目录） | 类别 | 字节数 | SHA-256 |
| --- | --- | ---: | --- |
| `Frameworks/libmmmojo.dylib` | 动态库 | 3,815,488 | `1fd92398807aa17ccf4e6c0f9cf0a96e0202e3b7a49104169a188e4c515c6425` |
| `Resources/libwxocr.dylib` | 动态库 | 58,027,936 | `a0056c2a1cbf7c8017301d60e3bb35f07611073fe99a41ca6d62ea15657cc204` |
| `Resources/text_det_fp16_v2.xnet` | XNET 文件 | 1,447,764 | `3d24e46ad2b7103370656043965548acfb59a7d33c4ea7689abd5f4cdfdf0279` |
| `Resources/text_rec_zh13562_fp16_v2.xnet` | XNET 文件 | 8,354,384 | `fc5c638d5e4d1eb7399e3be89a52f37a527631a41c9869364a350f6ae4cf20b8` |
| `Resources/charset_zh13562.txt` | 字符表 | 54,258 | `e61c2b740d4f09263cdd3495825c1b17d69bd073e4bf99272ac2c075e4c5bbc8` |
| `Resources/paragraph_det_fp16_v2.xnet` | XNET 文件 | 2,937,632 | `055fb665b8d728d869ff555969441175f78929cace3647a4ba01cb71a4be8555` |
| `Resources/QBarDetV1.7.0.26.xnet` | XNET 文件 | 413,819 | `4bebaeb0fa34a020522768fa649a45e28e3b768d7c9ca0458a4fe4be7d4b2296` |
| `Resources/DeepSR_REPSR_R_M4C16_Flip_ep24.xnet` | XNET 文件 | 45,388 | `529969c2d81b62cf1d8fcac2a1d67fcf08771c810e05bfb3006990746073f65d` |
| `Resources/QBarSegV1.1.0.26.xnet` | XNET 文件 | 224,449 | `30a536fa8b35d60bf39ec780259f37b64da8393de54a50fa7d6c71cea58b053d` |
| `Resources/FPMMFG1V1.2.0.26.xnet` | XNET 文件 | 1,604,697 | `a9e9a990ca68872baba7a37a9bf1cadabb9c54b84c9cb43ce19634619f744678` |
| `Resources/FPMMFG2V1.3.1.26.xnet` | XNET 文件 | 6,952,285 | `6e998da766ef2a62b9f114ead019cdff3df673f6c951c531a58c8e568d1f0c21` |
| `Resources/PrivacyDetectV8.0.26.xnet` | XNET 文件 | 5,351,702 | `104793c78465c93bcfe4165cbfa25210015d2870b14614be58fd338694224b8e` |

这些文件位于本机 `.runtime/Frameworks` 和 `.runtime/Resources`。公开仓库保存清单和检查代码，不包含第三方专有文件；来源和权属见[第三方来源说明](THIRD_PARTY_NOTICES.md)。表中分类只区分文件类型，不据文件名推断未验证模型的业务用途。

## 检查本机文件

在项目目录执行：

```sh
uv run scripts/inspect_runtime.py --runtime .runtime
```

工具重新计算哈希，输出 JSON。正常基线应得到：

```json
{"unchanged": 12, "changed": 0, "missing": 0}
```

`runtime.json` 是可选的版本标签来源；即使没有该文件，也可检查具有 `Frameworks/`、`Resources/` 布局的独立目录。比较依据是实际文件字节，不信任其中缓存的哈希。

## 比较未来版本

指定对应的源应用目录，或者指定现有 DMG：

```sh
uv run scripts/inspect_runtime.py --app "/完整路径/源应用.app" > candidate.json
uv run scripts/inspect_runtime.py --dmg "/完整路径/候选版本.dmg" > candidate.json
```

DMG 检查只在 macOS 上运行，会计算候选 DMG 的 SHA-256、只读挂载、读取已确认的应用目录布局并卸载。它不会启动源应用、加载动态库、运行模型或替换当前 `.runtime`。若候选包的应用布局变了，工具会明确报错；请自行挂载后用 `--app` 指定准确路径。

检查器允许未来应用版本，以便比较；安装器仍只接受已经验证的版本和哈希。应用版本/build 读取 `CFBundleShortVersionString`、`CFBundleVersion`，不尝试其他候选字段。

每个已知文件有 `unchanged`、`changed` 或 `missing` 状态，以及预期/实际字节数和 SHA-256。额外的顶层 `Resources/*.xnet` 文件会列在 `additional_xnet_files`，不会按相似名字映射成旧模型。此列表不宣称覆盖整个源应用的所有资源。

退出码：`0` 表示基线中的所有文件相同，`1` 表示至少一个已知文件变化或缺失，`2` 表示无法完成检查。文件有变化时仍会输出完整 JSON，退出码 1 不是工具崩溃。

如需比较另一个经过审查的基线，传入 `--baseline /完整路径/baseline.json`。该文件必须符合版本 1 的严格基线结构；比较报告不是基线，不能直接替代它。

## 如何决定是否更新

- 全部 12 个文件相同：已有运行时包含同样的文件，无需仅因源应用版本号变化而重新复制它们。
- 只有动态库变化：模型内容没有变化，但接口或加载方式仍可能变化，不能由模型哈希推断引擎兼容性。
- 模型或字符表变化：说明内容变化；需要保留配套库，验证加载、识别结果和实际耗时，再决定是否更新。
- 文件缺失或出现新名字：需要重新确认布局和加载契约，不自动寻找相似文件作为替代。

哈希只判断内容是否相同，不能证明模型更准确、质量更好、可被当前引擎加载或具有再分发许可。报告中的 `compatibility_verified` 因此始终为 `false`。可解析的 XNET 格式和元数据见[模型格式说明](model-format.md)。
