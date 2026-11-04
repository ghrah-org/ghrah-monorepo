"""可信命名替身：只维护显式挂载，不承担内核对象表。"""


class NamespaceMock:
    def __init__(self):
        self.mounts: dict[str, dict] = {}

    def bind(self, arguments):
        path, target = arguments["path"], arguments["target"]
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError("挂载路径必须为绝对路径")
        if not isinstance(target["scheme_id"], str) or type(target["domain_generation"]) is not int:
            raise ValueError("挂载必须钉住方案实例与代次")
        self.mounts[path.rstrip("/")] = dict(target)
        return {"bound": True}

    def resolve(self, path):
        for mount in sorted(self.mounts, key=len, reverse=True):
            if path == mount or path.startswith(mount + "/"):
                return {"result": "redirect", **self.mounts[mount], "path": path[len(mount) :]}
        raise ValueError("路径没有挂载")
