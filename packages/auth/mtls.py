from __future__ import annotations

"""mTLS 支持 — Issue #44: OAuth2 / mTLS

提供 SSLContext 构建 + 客户端证书验证 + 身份提取。
全局单例：init_mtls_context / get_mtls_context / reset_for_tests
"""

import ssl
import threading
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class MTLSConfig:
    """mTLS 配置。"""

    enabled: bool = False
    ca_cert_path: str = ""
    server_cert_path: str = ""
    server_key_path: str = ""
    client_cert_required: bool = True
    verify_client: bool = True
    # 可选：允许的客户端证书指纹列表（SHA-256 hex）
    allowed_fingerprints: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


class MTLSContext:
    """封装 mTLS SSLContext 的构建与客户端证书验证。"""

    def __init__(self, config: MTLSConfig) -> None:
        self._config = config

    def get_ssl_context(self) -> ssl.SSLContext:
        """构建服务端 SSLContext。

        - 加载服务器证书与私钥
        - 若 ca_cert_path 存在，加载 CA 并按配置要求客户端证书
        """
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)

        if self._config.server_cert_path and self._config.server_key_path:
            ctx.load_cert_chain(
                certfile=self._config.server_cert_path,
                keyfile=self._config.server_key_path,
            )

        if self._config.ca_cert_path:
            ctx.load_verify_locations(cafile=self._config.ca_cert_path)
            if self._config.client_cert_required:
                ctx.verify_mode = ssl.CERT_REQUIRED
            else:
                ctx.verify_mode = ssl.CERT_OPTIONAL
        else:
            ctx.verify_mode = ssl.CERT_NONE

        return ctx

    def verify_client_cert(self, cert_pem: str) -> bool:
        """验证客户端证书（桩实现：检查指纹白名单）。

        生产环境应使用 cryptography 库进行完整验证。
        """
        if not self._config.verify_client:
            return True
        if not self._config.allowed_fingerprints:
            # 白名单为空时，允许任意有效证书（仅校验存在性）
            return bool(cert_pem and cert_pem.strip())
        # 计算 SHA-256 指纹
        try:
            # 去除 PEM 头尾，解码 DER
            import base64
            import hashlib
            lines = [ln for ln in cert_pem.strip().splitlines() if not ln.startswith("-----")]
            der = base64.b64decode("".join(lines))
            fp = hashlib.sha256(der).hexdigest()
            return fp in self._config.allowed_fingerprints
        except Exception:
            return False

    def extract_client_identity(self, cert: dict | str | None) -> str | None:
        """从客户端证书中提取 CN（通用名称）作为身份标识。

        cert 可以是 ssl.SSLSocket.getpeercert() 返回的 dict，或 PEM 字符串。
        """
        if cert is None:
            return None
        if isinstance(cert, dict):
            # ssl.SSLSocket.getpeercert() 格式
            subject = dict(x[0] for x in cert.get("subject", []))
            return subject.get("commonName")
        # PEM 字符串时仅做简单桩实现
        if isinstance(cert, str) and "CN=" in cert:
            for part in cert.split(","):
                part = part.strip()
                if part.startswith("CN="):
                    return part[3:]
        return None


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_lock = threading.RLock()
_context: MTLSContext | None = None


def init_mtls_context(config: MTLSConfig) -> MTLSContext:
    """初始化全局 MTLSContext（幂等）。"""
    global _context
    with _lock:
        _context = MTLSContext(config)
        return _context


def get_mtls_context() -> MTLSContext | None:
    """获取全局 MTLSContext；未初始化时返回 None。"""
    return _context


def reset_for_tests() -> None:
    """重置单例（仅供测试使用）。"""
    global _context
    with _lock:
        _context = None
