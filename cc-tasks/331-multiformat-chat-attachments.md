# 工单331：聊天多格式附件与三端同步

## 目标与现状

用户无法发送 CSV。当前后端 SUPPORTED_SUFFIXES、桌面 TS/Rust/选择器、手机 Dart/Android MIME 过滤只开放 TXT/MD/DOCX。先完成本工单，再修改实现。三仓均在当前分支施工，保留其他未提交改动。

## 验收合同

- 新增 CSV/TSV、XLSX、PDF、PPTX、JSON/JSONL/YAML/YML、HTML/HTM、常用源码/日志文本；保留 TXT/MD/DOCX、现有图片/语音入口。
- CSV 支持 UTF-8 BOM、UTF-16 BOM、GB18030，保留引号内换行和空单元格；Excel 保留工作表、行列和公式原文，不执行公式；PPTX 保留页码、正文与表格；PDF 提取逐页文字，对无文字页明确提示无法识别，不宣称完成视觉识别。
- 文档内图片、扫描 PDF OCR、视频理解、旧二进制 DOC/XLS/PPT 不列为本次已完成能力；失败提示应给出可行转换方式。图片和语音沿用既有识别链。
- 保持每份文档5MB、单文档、文档图片不混传、正文50万字符上限。压缩容器检查展开体积与成员数量；不执行脚本、宏、公式、外部链接。失败用既有结构化422，禁止静默截断或空成功。
- 复用资料库、read_document、既有只读观测与归属隔离，不新增开关/权限/状态库；trusted_user_text 只包含附言，附件正文仍为不可信资料。
- 桌面同步 TS/Rust、文件选择器、拖放与提示；手机同步 Dart/Android MIME、中英文提示与 MIME 上报。发送、重试、请求关联、历史恢复和5MB限制保持。

## 施工清单

- [x] 排查实际入口、读取三仓规则和相关文档；定位多层白名单为共同根因。
- [x] 先补入口回归：`test_csv_upload_reaches_turn_and_archives_full_text[mobile/desktop]` 修复前两项均返回415（业务断言预期200），修复后通过。
- [x] 后端新增解析与资料库接线：`core/document_parsers.py`、`core/media_processor.py`；首轮相关39项通过。
- [x] 桌面同步选择器、TS/Rust校验和文案；附件提示右侧增加可关闭按钮，关闭仅清除提示不清附件。首次TS/Vite build通过。
- [x] 手机同步Dart校验、Android MIME和中英文文案；`flutter gen-l10n`通过。
- [x] 后端定向59项通过；增量解析/长文/换行31项通过；最终有界表格变更后15项解析回归通过。桌面7项Vitest、TS/Vite build、cargo check、原生附件测试1项通过。Flutter analyze零问题、附件与三组必跑发送回归84项通过。JDK21 Android `:app:compileDevDebugKotlin`通过（66 tasks，既有弃用警告）。
- [x] 隔离浏览器 `scripts/multiformat-attachments-browser.mjs` 通过：真实ChatPanel组件上的CSV粘贴/发送及附言payload、实际选择器过滤、提示右侧关闭、空框消失、保留已有附件。截图 `.tmp/331-attachment-hint.png`、`.tmp/331-csv-draft.png`（ignored）；已检查截图。
- [x] 同步三仓接口文档和known-issues；未新增设置或状态库，复用现有资料库观测，scope/request_id/角色冻结/ACK/TTL合同不变。
- [x] 差异与换行检查通过，28种后缀集合一致；只暂存本单改动，保留并行文档和删除项。桌面提交 `dbeb57f`，手机提交 `11503e2`；后端以本工单同批独立提交交付（`git log -- cc-tasks/331-multiformat-chat-attachments.md`可定位）。

## 验证命令与范围

- Python 3.12：`pytest tests/test_multiformat_upload.py tests/test_media_ingest_errors.py tests/test_long_document_reading.py tests/test_chat_media.py tests/test_character_document_library.py tests/test_desktop_chat_receipts.py tests/test_owner_turn_api_contract.py tests/test_n8_auth.py -q`：59 passed。
- `pytest tests/test_multiformat_upload.py tests/test_long_document_reading.py tests/test_line_endings.py -q`：31 passed；有界表格最终补丁后 `pytest tests/test_multiformat_upload.py -q`：15 passed。
- 桌面：`npm.cmd test -- src/windows/chat/draftAttachments.test.ts tests/protocol-fixtures.test.ts`：7 passed；`npm.cmd run build`；`cargo check --manifest-path src-tauri/Cargo.toml`；`cargo test --manifest-path src-tauri/Cargo.toml chat_attachment_memory_source_validates_and_decodes --lib`：1 passed。
- 手机：`flutter gen-l10n`、`flutter analyze`；`flutter test test/attachment_coordinator_test.dart test/chat_recovery_upload_test.dart test/mobile_outbox_deadline_test.dart test/chat_interaction_regression_test.dart`：84 passed；Android `gradlew.bat :app:compileDevDebugKotlin --console=plain`：BUILD SUCCESSFUL。
- 跨仓验证基线：backend `6a1ed65f4727391ba8a6f8450bd009332f4bd0dd`；desktop `773f7271e19666ece723306f4f4f3d4dd495162d`；mobile `35e7c9eadf6fd130cbf5f2f951854daf22ed6acf` 加本单修改。另核对backend/TS/Rust/Dart文档后缀集合一致；其他版本组合 `not_verified`。
- 原生安装版、手机文件提供器/触摸/真机上传、真实模型识别与生产部署：`observe / not-run`。浏览器以Tauri IPC替身和合成资料验证交互；HTTP测试只使用隔离沙盒，不涉及生产数据。现有图片/语音能力未新增真实模型验收。

## 删除候选（本次不执行）

后续可将各端重复后缀/MIME表迁移为后端能力发现；删除旧白名单时连同守卫、测试与文档同步迁移。本次保留兼容入口，不清理无关 brief。

## 交付边界

本单实施、验证并本地提交；不隐含生产部署或正式安装包发布。浏览器/真机/真实模型效果分别记录，不以编译成功代替。
