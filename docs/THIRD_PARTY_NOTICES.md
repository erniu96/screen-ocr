# 第三方来源与测试数据

本仓库的开源许可证适用于本仓库自行实现的代码，不授予上游软件、OCR 模型或其他第三方材料的使用或再分发权利。本项目与上游软件及其权利人没有隶属关系，也未获得其官方支持。

## 本地 OCR 运行时

`scripts/prepare_runtime.py` 从使用者提供的、或直接从原始发布服务器下载的 macOS 安装包中提取第三方专有运行时。当前固定版本为 4.2.0（build 270352，arm64），实际来源见下方原始地址。

- 官方安装包：<https://dldir1v6.qq.com/weixin/Universal/Mac/WeChatMac_4.2.0.dmg>
- DMG SHA-256：`077b99658e2d2083458a181f6de21ac9442fef8479635e367afdb0e4b1be5f22`
- 第三方专有组件包括 `libmmmojo.dylib`、`libwxocr.dylib`、`.xnet` 模型和字符集文件。它们保留原有权属和适用条款，不属于本项目的开源授权范围。

这些组件不提交到仓库，不包含在本项目源码分发包中。`.runtime/` 只在使用者本机生成；请勿将其作为本项目的开源产物再分发。本仓库的 `native/ocr_helper.c` 是自行编写的动态库入口程序，并非第三方引擎本身。

## 协议研究参考

以下项目帮助确认了接口和消息的字段契约：

- [swigger/wechat-ocr，commit e32d4af10d8045f8613078bac2df442662c76b03](https://github.com/swigger/wechat-ocr/tree/e32d4af10d8045f8613078bac2df442662c76b03)：`pb/ocr_wx4.proto`、`pb/ocr_common.proto` 以及 MMMojo 接口声明。本项目根据这些已知字段和真实引擎消息实现 Python 编解码器与 macOS 调用层，未包含该项目的 `.proto`、生成代码、C++ 源码或编译产物。所检查版本未提供明确的仓库许可证；此处的致谢不构成对其代码的重新授权。
- [zhangtaomox/wxocr，commit 118891e187229c95eeabbb9ca571cd235f67cb86](https://github.com/zhangtaomox/wxocr/tree/118891e187229c95eeabbb9ca571cd235f67cb86)：Linux HTTP 服务的行为参考。该版本 README 的 License 章节声明 MIT License 并列出附加免责条款。本项目自行实现标准库 HTTP 服务，未包含其 Flask 服务源码、页面、镜像或原生扩展。
- [golangboyme/wxocr 镜像](https://hub.docker.com/r/golangboyme/wxocr)：用于验证既有请求/响应行为的研究对象。它不是本项目的安装依赖，也没有被重新打包或发布。

协议中未确认的字段被跳过，不按数值或相似名字推断其业务含义。第三方项目的公开可读状态不等于其代码或所包含专有组件获得本仓库许可证的授权。

## 公开测试数据

- `tests/fixtures/english.png`、`chinese.png` 和 `blank.png` 是专门生成的测试图片，不包含用户截图。
- `linux-capture.jsonl` 与 `macos-capture.jsonl` 是引擎识别上述英文测试图片时记录的消息。保留原始消息字节及使用独立 protobuf 定义解析的结果，以验证编解码器。请求路径仅为临时测试路径 `/tmp/wxocr-capture/fixture.png` 和 `/private/tmp/wxocr-capture/fixture.png`，不指向用户文件；它们无需在运行测试的机器上存在。
- `synthetic-layout-fragments.json` 是手工构造的乱序片段，用于验证阅读顺序、片段拼接与原始字符保留，不来自用户截图。

私人截图、识别历史和个人实验报告不属于此公开仓库。
