"""当前系统服务的显式领域契约；不扩充内核基础入口。"""

NAMESPACE_SCHEMES = [
    {"name": "ns", "contract": "ghrah.namespace/v0", "actions": ["bind", "unbind", "inspect"]}
]
