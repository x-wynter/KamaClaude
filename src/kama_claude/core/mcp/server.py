# 职责：在 daemon 生命周期内维护 MCP 连接与工具快照。
# 协作：Core 启停；Runner 每轮调用 get_tools 后按 Skill 白名单登记。
# 推荐阅读：start_all → _connect → get_tools → stop_all；register_tools 是未接入主线的辅助接口。

from __future__ import annotations

import asyncio
import logging

from kama_claude.core.config import McpServerConfig
from kama_claude.core.mcp.client import McpClient
from kama_claude.core.mcp.tool import McpTool
from kama_claude.core.tools.registry import ToolRegistry

log = logging.getLogger(__name__)


# 管理所有 MCP server 连接的生命周期：启动、工具发现、注册、关闭
class McpServerManager:
    # 创建连接索引与工具快照，实际 IO 留到 start_all。
    def __init__(self) -> None:
        self._clients: dict[str, McpClient] = {}
        self._tools: list[McpTool] = []

    # 依次连接每个 MCP server，发现工具后缓存供后续 registry 使用；失败时记录日志并跳过
    async def start_all(self, servers: list[McpServerConfig]) -> None:
        # 顺序连接并发现；任一服务失败只跳过该服务，取消则向 Core 传播。
        # 连接尚未登记时也必须关闭，否则失败服务的子进程无法被 stop_all 找到。
        for cfg in servers:
            client: McpClient | None = None
            try:
                client = await self._connect(cfg)
                tool_defs = await client.list_tools()
                for tool_def in tool_defs:
                    self._tools.append(McpTool(client, cfg.name, tool_def))
                self._clients[cfg.name] = client
                log.info(
                    "mcp: server '%s' connected, %d tool(s) discovered",
                    cfg.name,
                    len(tool_defs),
                )
            except asyncio.CancelledError:
                if client is not None:
                    await client.close()
                raise
            except Exception:
                if client is not None:
                    await client.close()
                log.exception("mcp: server '%s' failed to start, skipping", cfg.name)

    # 将所有已发现的 MCP 工具注册到指定 registry
    def register_tools(self, registry: ToolRegistry) -> None:
        for tool in self._tools:
            registry.register(tool)

    # 返回已发现的 MCP 工具列表（用于 runner 每次 run 时注入新 registry）
    def get_tools(self) -> list[McpTool]:
        return list(self._tools)

    # 关闭所有 MCP 连接并终止 stdio 子进程
    async def stop_all(self) -> None:
        for name, client in list(self._clients.items()):
            try:
                await client.close()
                log.info("mcp: server '%s' closed", name)
            except Exception:
                log.warning("mcp: error closing server '%s'", name)
        self._clients.clear()
        self._tools.clear()

    # 根据 transport 类型建立连接
    async def _connect(self, cfg: McpServerConfig) -> McpClient:
        client = McpClient()
        try:
            if cfg.transport == "stdio":
                if not cfg.command:
                    raise ValueError(f"mcp server '{cfg.name}': stdio transport requires 'command'")
                await client.connect_stdio(cfg.command, cfg.args, cfg.env or None)
            elif cfg.transport == "tcp":
                await client.connect_tcp(cfg.host, cfg.port)
            else:
                raise ValueError(f"mcp server '{cfg.name}': unknown transport '{cfg.transport}'")
        except BaseException:
            await client.close()
            raise
        return client
