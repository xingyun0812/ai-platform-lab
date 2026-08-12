package platform.tools

# 没有 tool 字段时不应用此策略
# 默认拒绝（当 tool 存在时）

# calc 工具允许所有人使用
allow {
    input.tool == "calc"
}

# web_search 只允许 admin 和 developer 角色
allow {
    input.tool == "web_search"
    input.role == "admin"
}
allow {
    input.tool == "web_search"
    input.role == "developer"
}

# sql_query 只允许 admin
allow {
    input.tool == "sql_query"
    input.role == "admin"
}
