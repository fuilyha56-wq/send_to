"""send_to 服务：通过单一服务实例统一暴露本插件所有 tool。

设计目标：
- 一个 service 即可对外暴露插件内所有 tool，无需为每个 tool 单独设计服务接口
- 自描述：调用方运行时即可发现能力与参数，无需翻阅外部文档
- 兼容现有 tool：service 仅在 tool 外包一层，tool 内部逻辑不变

调用示例：

    from src.app.plugin_system.api.service_api import get_service

    service = get_service("send_to:service:send_to")
    if service is not None:
        # 1. 发现可用能力
        tools = service.list()
        # 2. 调用具体 tool
        ok, result = await service.invoke("send_to_list_groups", {"limit": 10})
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

from src.core.components.base.service import BaseService
from src.kernel.logger import get_logger

from .tools import (
    ListGroupsTool,
    ListUsersTool,
    LookupUsersTool,
    SendToDailyMemoryTool,
    SendToFindStreamTool,
    SendToStreamContextTool,
    SendToUserContextTool,
    SendToUserMemoryTool,
)

logger = get_logger("send_to.service")


# 对外暴露的 tool 类（按注册顺序）
_TOOL_CLASSES: list[type] = [
    ListGroupsTool,
    ListUsersTool,
    LookupUsersTool,
    SendToFindStreamTool,
    SendToStreamContextTool,
    SendToDailyMemoryTool,
    SendToUserMemoryTool,
    SendToUserContextTool,
]


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
        returns={
            "type": "tuple[bool, str | dict]",
            "description": "(是否成功, 返回结果)",
        },
    )


class SendToService(BaseService):
    """send_to 服务组件。

    通过统一入口 ``invoke(tool_name, params)`` 暴露 send_to 插件所有 tool，
    并通过 ``list()`` 提供自描述能力，调用方无需翻阅文档即可发现可用能力。

    Class Attributes:
        service_name: 服务名（与 plugin_name 一致，便于通过签名调用）
        service_description: 服务描述
        version: 服务版本

    Examples:
        >>> from src.app.plugin_system.api.service_api import get_service
        >>> service = get_service("send_to:service:send_to")
        >>> tools = service.list()
        >>> ok, result = await service.invoke("send_to_list_groups", {"limit": 10})
    """

    service_name: str = "send_to"
    service_description: str = "跨聊天流查询/发送服务，统一暴露 send_to 插件所有 tool"
    version: str = "1.0.0"

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
            {
                "name": d.name,
                "description": d.description,
                "params": [
                    {
                        "name": p.name,
                        "type": p.type,
                        "required": p.required,
                        "description": p.description,
                        **({"default": p.default} if not p.required else {}),
                    }
                    for p in d.params
                ],
                "returns": d.returns,
            }
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
            tool_name: tool 名称（如 ``"send_to_list_groups"``）
            params: 关键字参数字典，将原样传给 ``tool.execute``

        Returns:
            ``(是否成功, 返回结果)``，与 ``BaseTool.execute`` 返回格式一致。

        Examples:
            >>> ok, result = await service.invoke("send_to_list_groups", {"limit": 10})
        """

        tool_cls = self._find_tool_class(tool_name)
        if tool_cls is None:
            return False, f"Tool '{tool_name}' not found in send_to service"

        kwargs = dict(params or {})

        # 必填参数校验：缺参直接失败，不进入 execute
        descriptor = self.get_descriptor(tool_name)
        if descriptor is not None:
            missing = [
                p.name for p in descriptor.params
                if p.required and p.name not in kwargs
            ]
            if missing:
                return False, f"缺少必填参数: {', '.join(missing)}"

        # 每次调用创建新实例，避免 stream_id/trigger_message 等运行时上下文污染
        tool = tool_cls(self.plugin)
        try:
            return await tool.execute(**kwargs)
        except TypeError as e:
            return False, f"参数类型不匹配: {e}"
        except Exception as e:  # noqa: BLE001
            logger.warning(f"调用 tool {tool_name} 失败: {e}")
            return False, f"调用失败: {e}"

    @classmethod
    def _find_tool_class(cls, tool_name: str) -> type | None:
        """按名称查找 tool 类。"""

        for tool_cls in cls._registered_tool_classes():
            if getattr(tool_cls, "tool_name", None) == tool_name:
                return tool_cls
        return None
