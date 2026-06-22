# Phase K — 对象存储接入 S3/OSS（Issue #33 / Roadmap #49）

> **目标**：创建统一的对象存储抽象层，支持本地文件系统（开发/测试）、AWS S3 及阿里云 OSS，使 RAG 文件上传、审计归档、Memory 快照等场景可无缝切换存储后端。

---

## 1. 设计要点

构建思路、使用链路与逐文件代码说明见 [phase-k-build-and-code-guide.md](./phase-k-build-and-code-guide.md)。

### 1.1 三层后端架构

```
┌─────────────────────────────────────────────────────┐
│              StorageBackend (ABC)                    │
│  put / get / delete / list / exists / get_metadata  │
│  put_file / get_file (convenience wrappers)         │
└─────────────────────────────────────────────────────┘
         ↓               ↓               ↓
 LocalStorageBackend  S3StorageBackend  OSStorageBackend
 (本地文件系统)        (boto3 lazy)     (oss2 lazy)
```

- **LocalStorageBackend**：开发/测试/单机部署首选，零依赖，元数据通过 `.meta.json` sidecar 文件存储
- **S3StorageBackend**：AWS S3 及 S3 兼容协议（MinIO、Ceph RGW），依赖 `boto3`（可选）
- **OSStorageBackend**：阿里云 OSS，依赖 `oss2`（可选）

### 1.2 工厂模式（Factory Pattern）

```python
cfg = StorageConfig(backend="s3", bucket="my-bucket", ...)
backend = create_backend(cfg)   # 自动选择正确后端
init_storage(cfg)               # 全局单例
storage = get_storage()         # 获取单例
```

### 1.3 懒加载依赖（Lazy Import）

S3/OSS SDK 不在核心依赖中，仅在对应后端初始化时 import。未安装时抛出清晰错误信息（包含 pip 安装命令），避免无关场景的 ImportError。

### 1.4 异步接口 + 同步 SDK 桥接

boto3 和 oss2 均为同步 SDK，通过 `asyncio.to_thread()` 封装为协程，与 FastAPI 异步生态无缝集成。

### 1.5 本地后端元数据 sidecar

```
data/storage/<bucket>/<prefix>/
├── uploads/
│   ├── report.pdf           ← 对象文件
│   └── report.pdf.meta.json ← 元数据（key, size, last_modified, metadata, etag）
```

元数据 JSON 结构：
```json
{
  "key": "uploads/report.pdf",
  "size": 102400,
  "last_modified": 1703000000.0,
  "metadata": {"tenant_id": "t1", "original_filename": "report.pdf"},
  "etag": null
}
```

### 1.6 预签名 URL（Presigned URL）

S3/OSS 后端支持 `presign_get(key, expires=3600)` 方法，生成临时可访问的下载链接。本地后端不支持此功能（返回 501）。

### 1.7 优雅降级

- 存储后端未初始化 → 路由返回 `503 STORAGE_DISABLED`，不崩溃
- boto3/oss2 未安装 → 初始化时抛出带安装建议的 `ImportError`，不在运行时崩溃
- `get_storage()` 返回 `None` 而非抛出异常

---

## 2. 数据模型

### 2.1 StorageObject

```python
@dataclass
class StorageObject:
    key: str                    # 对象 key
    size: int                   # 字节数
    last_modified: float        # Unix 时间戳
    metadata: dict              # 用户自定义元数据
    etag: str | None            # ETag（内容哈希，S3/OSS 提供）
```

### 2.2 StorageConfig

```python
@dataclass
class StorageConfig:
    backend: str = "local"                  # "local" | "s3" | "oss"
    bucket: str = "ai-platform-lab"         # 存储桶名
    prefix: str = ""                        # key 前缀（命名空间隔离）
    region: str = "us-east-1"              # AWS 区域（S3）
    endpoint: str | None = None             # 自定义 endpoint（OSS/MinIO）
    access_key: str | None = None           # 访问密钥 ID
    secret_key: str | None = None           # 访问密钥 Secret
    local_root: Path = Path("data/storage") # 本地后端根目录
```

### 2.3 StorageBackend ABC

```python
class StorageBackend:
    async def put(key, data, metadata=None) -> str        # 返回 key
    async def get(key) -> bytes
    async def delete(key) -> bool
    async def list(prefix="") -> list[str]
    async def exists(key) -> bool
    async def get_metadata(key) -> dict
    async def put_file(local_path, key) -> str            # 便捷：读文件+put
    async def get_file(key, local_path) -> Path           # 便捷：get+写文件
```

---

## 3. REST API 接口表

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| `POST` | `/internal/storage/upload` | 普通用户 | 上传文件（multipart）|
| `GET` | `/internal/storage/list` | 普通用户 | 列出对象（`?prefix=`）|
| `GET` | `/internal/storage/config` | **admin** | 查看后端配置（掩码密钥）|
| `POST` | `/internal/storage/presign/{key}` | **admin** | 生成预签名下载 URL |
| `GET` | `/internal/storage/{key}` | 普通用户 | 下载文件（StreamingResponse）|
| `DELETE` | `/internal/storage/{key}` | **admin** | 删除对象 |

### 3.1 上传示例

```bash
curl -X POST http://localhost:8000/internal/storage/upload \
  -H "X-Tenant-Id: t1" \
  -H "Authorization: Bearer tok1" \
  -F "file=@report.pdf" \
  -F "key=uploads/report.pdf"
```

响应：
```json
{"key": "uploads/report.pdf", "size": 102400, "content_type": "application/pdf"}
```

### 3.2 列出对象

```bash
curl "http://localhost:8000/internal/storage/list?prefix=uploads/" \
  -H "X-Tenant-Id: t1" -H "Authorization: Bearer tok1"
```

响应：
```json
{"objects": ["uploads/report.pdf", "uploads/data.csv"], "count": 2, "prefix": "uploads/"}
```

### 3.3 预签名 URL（仅 S3/OSS）

```bash
curl -X POST "http://localhost:8000/internal/storage/presign/uploads/report.pdf?expires=3600" \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer admin-tok"
```

响应：
```json
{"key": "uploads/report.pdf", "url": "https://...", "expires_in": 3600}
```

---

## 4. 配置表（需添加到 settings.py）

| 字段 | 环境变量 | 默认值 | 说明 |
|------|----------|--------|------|
| `storage_backend` | `STORAGE_BACKEND` | `"local"` | 后端类型：`local`\|`s3`\|`oss` |
| `storage_bucket` | `STORAGE_BUCKET` | `"ai-platform-lab"` | 存储桶名 |
| `storage_prefix` | `STORAGE_PREFIX` | `""` | 对象 key 前缀 |
| `storage_region` | `STORAGE_REGION` | `"us-east-1"` | S3 区域 |
| `storage_endpoint` | `STORAGE_ENDPOINT` | `None` | 自定义 endpoint（OSS/MinIO）|
| `storage_access_key` | `STORAGE_ACCESS_KEY` | `None` | 访问密钥 ID |
| `storage_secret_key` | `STORAGE_SECRET_KEY` | `None` | 访问密钥 Secret |
| `storage_local_root` | `STORAGE_LOCAL_ROOT` | `REPO_ROOT / "data" / "storage"` | 本地后端根目录 |
| `storage_presign_expiry_seconds` | `STORAGE_PRESIGN_EXPIRY_SECONDS` | `3600` | 预签名 URL 有效期（秒）|

### 需添加到 `apps/gateway/settings.py` 的字段：

```python
# ── 对象存储 ─────────────────────────────────────────────
storage_backend: str = Field(default="local", validation_alias="STORAGE_BACKEND", description="存储后端: local|s3|oss")
storage_bucket: str = Field(default="ai-platform-lab", validation_alias="STORAGE_BUCKET", description="存储桶名")
storage_prefix: str = Field(default="", validation_alias="STORAGE_PREFIX", description="对象 key 前缀")
storage_region: str = Field(default="us-east-1", validation_alias="STORAGE_REGION", description="S3 区域")
storage_endpoint: str | None = Field(default=None, validation_alias="STORAGE_ENDPOINT", description="自定义 endpoint（OSS/MinIO）")
storage_access_key: str | None = Field(default=None, validation_alias="STORAGE_ACCESS_KEY", description="访问密钥 ID")
storage_secret_key: str | None = Field(default=None, validation_alias="STORAGE_SECRET_KEY", description="访问密钥 Secret")
storage_local_root: Path = Field(default=REPO_ROOT / "data" / "storage", validation_alias="STORAGE_LOCAL_ROOT", description="local 后端根目录")
storage_presign_expiry_seconds: int = Field(default=3600, validation_alias="STORAGE_PRESIGN_EXPIRY_SECONDS", description="预签名 URL 有效期（秒）")
```

---

## 5. main.py 集成（需添加到 `apps/gateway/main.py`）

```python
# 在 import 区域添加
from apps.gateway.storage_routes import router as storage_router
from packages.storage import init_storage, StorageConfig, get_storage

# 在 lifespan/startup 区域添加（初始化存储）
storage_cfg = StorageConfig(
    backend=settings.storage_backend,
    bucket=settings.storage_bucket,
    prefix=settings.storage_prefix,
    region=settings.storage_region,
    endpoint=settings.storage_endpoint,
    access_key=settings.storage_access_key,
    secret_key=settings.storage_secret_key,
    local_root=settings.storage_local_root,
)
init_storage(storage_cfg)

# 在 app.include_router 区域添加
app.include_router(storage_router)
```

---

## 6. .env.example 需新增环境变量

```dotenv
# ── 对象存储（S3/OSS）─────────────────────────────────────
STORAGE_BACKEND=local                  # local | s3 | oss
STORAGE_BUCKET=ai-platform-lab
STORAGE_PREFIX=
STORAGE_REGION=us-east-1
# STORAGE_ENDPOINT=https://oss-cn-hangzhou.aliyuncs.com   # OSS/MinIO
# STORAGE_ACCESS_KEY=your-access-key-id
# STORAGE_SECRET_KEY=your-secret-access-key
STORAGE_LOCAL_ROOT=data/storage
STORAGE_PRESIGN_EXPIRY_SECONDS=3600
```

---

## 7. README 补充（需添加到 README.md）

```markdown
### 对象存储（Phase K）

支持三种存储后端：
- **local**（默认）：本地文件系统，无需额外配置，适合开发/测试
- **s3**：AWS S3 及 S3 兼容协议（MinIO、Ceph RGW）；需 `pip install boto3`
- **oss**：阿里云 OSS；需 `pip install oss2`

通过 `STORAGE_BACKEND` 环境变量切换，其他配置见 `.env.example`。
```

---

## 8. 测试说明

测试文件：`tests/test_storage.py`

运行：
```bash
python3 tests/test_storage.py
```

测试覆盖（14 个用例）：

| # | 测试名 | 说明 |
|---|--------|------|
| 1 | `test_storage_config_defaults` | StorageConfig 默认值验证 |
| 2 | `test_local_put_get` | LocalBackend 写入/读取 |
| 3 | `test_local_exists` | 对象存在性检查 |
| 4 | `test_local_delete` | 删除对象（含不存在时返回 False）|
| 5 | `test_local_list_prefix` | 列出对象，前缀过滤 |
| 6 | `test_local_put_get_file` | put_file/get_file 便捷方法 |
| 7 | `test_local_metadata_sidecar` | 元数据 sidecar 文件读写 |
| 8 | `test_s3_put_get_delete` | S3 后端 mock 测试（put/get/delete）|
| 9 | `test_s3_list_exists` | S3 后端 mock 测试（list/exists）|
| 10 | `test_oss_put_get` | OSS 后端 mock 测试（put/get/list）|
| 11 | `test_create_backend_factory` | 工厂函数返回正确类型 |
| 12 | `test_singleton_lifecycle` | 全局单例 init/get/reset |
| 13 | `test_create_backend_unknown` | 未知后端 → ValueError |
| 14 | `test_local_get_missing_raises` | 读取不存在对象 → FileNotFoundError |

---

## 9. 代码导航

```
packages/storage/
├── __init__.py        # 公开 API（8 个导出符号）
├── backend.py         # StorageBackend ABC + LocalStorageBackend + StorageConfig + StorageObject
├── s3.py              # S3StorageBackend（boto3 lazy import）
├── oss.py             # OSStorageBackend（oss2 lazy import）
└── factory.py         # create_backend + init_storage + get_storage + reset_for_tests

apps/gateway/
└── storage_routes.py  # REST API router（/internal/storage）

tests/
└── test_storage.py    # 14 个单元测试
```

---

## 10. 已知限制

1. **不支持 GCS/Azure Blob**：当前仅实现 S3 和 OSS，需要额外添加 `GCSStorageBackend` 和 `AzureBlobStorageBackend`。
2. **无分片上传（Multipart Upload）**：大文件（>5GB）在 S3 上需使用 multipart upload API，当前 `put` 方法将整个文件加载到内存，不适合超大文件。
3. **无服务端加密（SSE-KMS）**：当前不支持 AWS SSE-KMS 或 OSS 服务端加密配置，敏感数据需在应用层加密后上传。
4. **无生命周期策略（Lifecycle）**：不支持通过 SDK 设置对象过期规则，需在云控制台手动配置。
5. **本地后端不适合多实例部署**：LocalStorageBackend 基于本地文件系统，多个服务实例之间不共享，仅适合单机或开发环境。
6. **无断点续传**：下载大文件时不支持 HTTP Range 请求，网络中断需重新下载完整文件。
7. **无内容寻址（CAS）**：put 操作不返回 ETag/哈希，本地后端 etag 为 null，无法做客户端去重。

---

## 11. 面试考点

### Q1: 为什么使用 `asyncio.to_thread()` 而不是直接 await boto3 调用？

boto3 是同步 SDK，不支持 `async/await`。`asyncio.to_thread()` 将同步调用放入线程池执行，避免阻塞事件循环。对比方案：`aioboto3`（基于 aiobotocore）可提供真正的异步接口，但增加了额外依赖复杂度。

### Q2: 为什么 S3/OSS SDK 使用懒加载（lazy import）？

如果在模块顶层 import boto3，所有使用者都需要安装 boto3，即使他们只使用 local 后端。懒加载确保可选依赖仅在真正需要时才触发 ImportError，降低安装成本（`pip install ai-platform-lab[s3]`）。

### Q3: LocalStorageBackend 的 `.meta.json` sidecar 文件解决了什么问题？

本地文件系统没有原生元数据存储能力（除 extended attributes，跨平台兼容性差）。sidecar 文件以 JSON 格式存储 `metadata`、`etag`、`last_modified` 等字段，与对象文件同目录存放，使 `get_metadata()` 接口在本地后端与云后端语义一致。

### Q4: StorageConfig.prefix 字段的作用是什么？

prefix 提供**命名空间隔离**：多个服务共用同一 bucket 时，通过不同 prefix（如 `rag/`、`audit/`、`memory/`）隔离各自的对象，避免 key 冲突。这是比创建多个 bucket 更轻量的隔离方案。

### Q5: 工厂模式（create_backend）相比直接实例化的优势？

工厂模式将"选择哪个后端"的决策集中在一处，调用方不需要了解具体后端类名。未来添加 GCS 后端时只需修改 factory.py，不影响任何使用 `create_backend()` 的代码（开闭原则）。

### Q6: 全局单例（init_storage/get_storage）的线程安全如何保证？

`_lock = threading.RLock()` 保护 `init_storage()` 和 `reset_for_tests()` 中对 `_instance` 的写操作。读操作（`get_storage()`）不加锁，因为 Python GIL 保证对象引用读取的原子性，且单例一旦初始化后通常只读。
