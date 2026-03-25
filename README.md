# DeltaSync

## 中文简介
DeltaSync 是一个用 C 实现的文件差分同步示例项目。

在这个项目里：
- `server` 表示文件发送端。
- `client` 表示文件接收端。

它通过以下步骤减少重传数据量：
1. client 对本地旧文件按块计算弱校验和与强校验和。
2. server 根据这些块摘要在自己的源文件中做滑动窗口匹配。
3. server 按协议返回“直接字节 + 复用旧块”的重建指令。
4. client 使用旧文件临时副本和 server 返回的消息重建新文件。

> 这是一个自定义的 DeltaSync / 差分同步实现，用来演示基本思路；它**不是**标准 rsync 的兼容实现。

## English Overview
DeltaSync is a C implementation of a file delta synchronization demo.

In this project:
- `server` means the side that sends the source file.
- `client` means the side that rebuilds and receives the file.

The workflow reduces retransmission by:
1. Letting the client calculate weak and strong checksums for each chunk of the old file.
2. Letting the server scan its source file with a rolling checksum window.
3. Emitting rebuild instructions that combine literal bytes with reusable old chunks.
4. Reconstructing the new client file from the temporary old copy and the server messages.

> This is a custom DeltaSync implementation for demonstration purposes. It is **not** a standards-compatible rsync implementation.

## 文件结构 / File Layout

- [main.c](main.c)
  - 程序入口，负责串起一次完整的 DeltaSync 演示流程。
  - Thin entry point that wires one end-to-end DeltaSync run.
- [libwpsyncclient.c](libwpsyncclient.c)
  - client 侧逻辑：文件一致性判断、块摘要生成、目标文件重建。
  - Client-side logic: file comparison, chunk signature generation, and file rebuilding.
- [libwpsyncserver.c](libwpsyncserver.c)
  - server 侧逻辑：文件信息输出、块索引建立、滑动窗口匹配、重建消息生成。
  - Server-side logic: file info export, chunk index creation, rolling matching, and rebuild message generation.
- [deltasync.h](deltasync.h)
  - 共享常量、消息类型、辅助函数和对外 API 声明。
  - Shared constants, message types, helpers, and public API declarations.

## 核心 API / Core API

### Server side
- `serverReturnFileInfo`
- `serverProcessMessage`
- `serverMainDeltaSync`
- `serverRecover`

### Client side
- `clientCompareFileInfo`
- `clientTransform`
- `clientPrepareRebuildFile`
- `clientRebuildFile`
- `clientRecover`

## 同步流程 / Synchronization Flow

### 1. server 输出文件元信息 / server exports file metadata
`serverReturnFileInfo` 返回：
- 文件名 / file name
- 文件大小 / file size
- 修改时间 / modified time

client 用 `clientCompareFileInfo` 判断本地文件是否已经一致。
The client uses `clientCompareFileInfo` to decide whether synchronization can be skipped.

### 2. client 生成块摘要 / client generates chunk signatures
`clientTransform` 将旧文件按固定块大小切分，并为每块生成：
- 4 字节弱校验 / 4-byte weak checksum
- 16 字节强校验 / 16-byte strong checksum

### 3. server 建立索引 / server builds an index
`serverProcessMessage` 把 client 发来的块摘要组织成哈希桶，供滚动匹配使用。
`serverProcessMessage` stores client chunk signatures in hash buckets for rolling lookup.

### 4. server 输出重建指令 / server emits rebuild instructions
`serverMainDeltaSync` 使用滚动 Adler32 和 MD5 校验，在 server 文件中查找可复用块。
它输出以下几类消息：

- `DELTASYNC_MSG_LITERAL_AND_BLOCK` (`'a'`)
  - 先写入一段原始字节，再复用一个旧块。
  - Write literal bytes first, then reuse one old chunk.
- `DELTASYNC_MSG_LITERAL_ONLY` (`'b'`)
  - 只写入一段原始字节。
  - Write literal bytes only.
- `DELTASYNC_MSG_BLOCK_ONLY` (`'c'`)
  - 直接复用一个旧块。
  - Reuse one old chunk directly.
- `DELTASYNC_MSG_END` (`'e'`)
  - 同步结束；如有剩余字节，会一并带回。
  - End of synchronization; remaining literal bytes may be attached.

### 5. client 重建文件 / client rebuilds the file
`clientPrepareRebuildFile` 会先把旧文件重命名为临时文件，再创建新的目标文件。
`clientRebuildFile` 按 server 指令，把字节流和临时文件中的旧块拼接成新文件。
`clientRecover` 在结束后删除临时文件。

## 关键常量 / Key Constants

这些常量定义在 [deltasync.h](deltasync.h)：

- `CHUNK_SIZE = 5120`
- `SLIDE_WINDOW_SIZE = 10240`
- `WEAK_CHECKSUM_SIZE = 4`
- `STRONG_CHECKSUM_SIZE = 16`
- `CHUNK_SIGNATURE_SIZE = 20`
- `FILE_INFO_TRAILER_SIZE = 8`
- `HASH_BUCKET_COUNT = 65536`

## 构建方式 / Build

在项目目录执行：

```bash
gcc -std=c11 -Wall -Wextra -pedantic main.c libwpsyncclient.c libwpsyncserver.c -o deltasync
```

Build from the project root with the same command above.

## 运行方式 / Run

当前 [main.c](main.c) 使用的是硬编码示例路径：
- server file: `1.mp4`
- client file: `test/1.mp4`
- temp file: `temp`

运行前请确保这些文件路径存在并符合你的测试场景。
Before running, make sure these sample paths exist and match your local test setup.

运行：

```bash
./deltasync
```

## 验证建议 / Verification Tips

同步完成后，可以比较 server 文件和 client 重建后的文件是否一致，例如：

```bash
cmp 1.mp4 test/1.mp4
```

You can also compare checksums if you prefer a checksum-based validation.

## 当前限制 / Current Limitations

- 目前 `main.c` 仍然是一个固定路径的演示入口，不是通用 CLI。
- This demo still uses hard-coded file paths instead of command-line arguments.
- 使用的是项目内自带的 MD5 与 Adler32 逻辑，没有做进一步抽象。
- The bundled MD5 and Adler32 logic is intentionally kept local to this demo.
- 错误处理比原始版本更统一，但仍然偏向演示代码而不是生产级实现。
- Error handling is cleaner than the original version, but this is still demo-oriented code rather than production-ready software.
- 不宣称与 rsync 协议兼容。
- No compatibility with the rsync protocol is claimed.
