package platform.tenants

# 默认拒绝（没有匹配的 allow 规则时返回 false）
default allow = false

# admin 租户允许所有操作
allow {
    input.tenant_id == "admin"
}

# demo-a 租户允许 chat 和 rag 操作
allow {
    input.tenant_id == "demo-a"
    startswith(input.path, "/v1/chat")
}
allow {
    input.tenant_id == "demo-a"
    startswith(input.path, "/v1/rag")
}

# developer 角色允许 agent 操作
allow {
    input.role == "developer"
    startswith(input.path, "/v1/agent")
}
