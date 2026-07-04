"""
Service 组件模板（Neo-MoFox 架构）—— 符合"一个 service 暴露所有 tool"设计方案

本模板替代 mpdt 默认的 store/retrieve 示例，生成的 service 通过统一入口
``invoke(tool_name, params)`` 暴露插件内所有 BaseTool，并通过 ``list()``
提供自描述能力，调用方无需翻阅文档即可发现可用能力。

安装方式（任选其一）：
1. 覆盖 site-packages/mpdt/templates/service_template.py（影响全局 mpdt）
2. 将本文件内容复制到目标插件目录作为参考实现

模板占位符（由 mpdt 通过 .format() 渲染）：
    {description}       服务描述
    {author}            作者
    {date}              创建日期
    {class_name}        类名（PascalCase）
    {component_name}    组件名（snake_case，作为 service_name）
"""

SERVICE_TEMPLATE = '''"""
{description}

Created by: {author}
Created at: {date}
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.core.components.base import BaseService

logger = get_logger(__name__)


# 对外暴露的 tool 类列表（按注册顺序）
# 从本插件中导入需要对外暴露的 BaseTool 子类，例如：
#   from .tools import MyToolA, MyToolB
#   _TOOL_CLASSES: list[type] = [MyToolA, MyToolB]
_TOOL_CLASSES: list[type] = []


@dataclass(slots=True)
class ParamDescriptor:
    """单个参数的自描述。"""

    name: str
    type: str
    required: bool
    description: str
    default: Any = None


@dataclass(slots=True)
class ToolDescriptor:
    """单个 tool 的自描述。"""

    name: str
    description: str
    params: list[ParamDescriptor]
    returns: dict[str, str]


def _python_type_to_str(annotation: Any) -> str:
    """将 Python 类型注解转换为可读字符串。"""

    if annotation is inspect.Parameter.empty or annotation is None:
        return "any"
    # 处理 Annotated[T, "description"] 形式
    args = getattr(annotation, "__args__", None)
    metadata = getattr(annotation, "__metadata__", None)
    if args and metadata:
        annotation = args[0]
    name = getattr(annotation, "__name__", None)
    if name:
        return name
    return str(annotation).replace("typing.", "")


def _extract_param_descriptors(tool_cls: type) -> list[ParamDescriptor]:
    """从 BaseTool.execute 签名提取参数描述。"""

    sig = inspect.signature(tool_cls.execute)
    descriptors: list[ParamDescriptor] = []
    for name, param in sig.parameters.items():
        if name == "self":
            continue
        annotation = param.annotation
        description = ""
        metadata = getattr(annotation, "__metadata__", None)
        if metadata:
            description = str(metadata[0])
        type_str = _python_type_to_str(annotation)
        required = param.default is inspect.Parameter.empty
        default = None if required else param.default
        descriptors.append(ParamDescriptor(
            name=name,
            type=type_str,
            required=required,
            description=description,
            default=default,
        ))
    return descriptors


def _build_tool_descriptor(tool_cls: type) -> ToolDescriptor:
    """构建单个 tool 的描述符。"""

    return ToolDescriptor(
        name=tool_cls.tool_name,
        description=tool_cls.tool_description,
        params=_extract_param_descriptors(tool_cls),
        returns={{
            "type": "tuple[bool, str | dict]",
            "description": "(是否成功, 返回结果)",
        }},
    )


class {class_name}(BaseService):
    """{description}

    通过单一服务实例统一暴露插件内所有 tool，支持自描述与动态调用。

    使用方式：

        # 发现能力
        tools = service.list()

        # 调用
        ok, result = await service.invoke("tool_name", {{"param": "value"}})
    """

    service_name = "{component_name}"
    service_description = "{description}"
    version = "1.0.0"

    _descriptors_cache: list[ToolDescriptor] | None = None

    @classmethod
    def _registered_tool_classes(cls) -> list[type]:
        """返回此服务对外暴露的所有 tool 类。"""

        return _TOOL_CLASSES

    def _descriptors(self) -> list[ToolDescriptor]:
        """惰性构建并缓存所有 tool 的描述符。"""

        if self._descriptors_cache is None:
            self._descriptors_cache = [
                _build_tool_descriptor(cls)
                for cls in self._registered_tool_classes()
            ]
        return self._descriptors_cache

    def list(self) -> list[dict[str, Any]]:
        """列出所有可用 tool 的描述符。

        Returns:
            tool 描述符列表（dict 格式，便于序列化跨插件传递）
        """

        return [
            {{
                "name": d.name,
                "description": d.description,
                "params": [
                    {{
                        "name": p.name,
                        "type": p.type,
                        "required": p.required,
                        "description": p.description,
                        **({{"default": p.default}} if not p.required else {{}}),
                    }}
                    for p in d.params
                ],
                "returns": d.returns,
            }}
            for d in self._descriptors()
        ]

    def get_descriptor(self, tool_name: str) -> ToolDescriptor | None:
        """获取指定 tool 的描述符。未找到返回 None。"""

        for d in self._descriptors():
            if d.name == tool_name:
                return d
        return None

    def has(self, tool_name: str) -> bool:
        """检查 tool 是否存在。"""

        return self.get_descriptor(tool_name) is not None

    async def invoke(
        self,
        tool_name: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[bool, Any]:
        """通过名称调用 tool。

        Args:
            tool_name: tool 名称
            params: 关键字参数字典，将原样传给 ``tool.execute``

        Returns:
            ``(是否成功, 返回结果)``，与 ``BaseTool.execute`` 返回格式一致。
        """

        tool_cls = self._find_tool_class(tool_name)
        if tool_cls is None:
            return False, f"Tool '{{tool_name}}' not found"

        kwargs = dict(params or {{}})

        # 必填参数校验
        descriptor = self.get_descriptor(tool_name)
        if descriptor is not None:
            missing = [
                p.name for p in descriptor.params
                if p.required and p.name not in kwargs
            ]
            if missing:
                return False, f"缺少必填参数: {{', '.join(missing)}}"

        # 每次调用创建新实例，避免运行时上下文污染
        tool = tool_cls(self.plugin)
        try:
            return await tool.execute(**kwargs)
        except TypeError as e:
            return False, f"参数类型不匹配: {{e}}"
        except Exception as e:  # noqa: BLE001
            logger.warning(f"调用 tool {{tool_name}} 失败: {{e}}")
            return False, f"调用失败: {{e}}"

    @classmethod
    def _find_tool_class(cls, tool_name: str) -> type | None:
        """按名称查找 tool 类。"""

        for tool_cls in cls._registered_tool_classes():
            if getattr(tool_cls, "tool_name", None) == tool_name:
                return tool_cls
        return None
'''


def get_service_template() -> str:
    """获取 Service 组件模板"""

    return SERVICE_TEMPLATE
