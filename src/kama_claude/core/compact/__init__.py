# 压缩包导出：运行代码直接导入具体模块；此文件提供包级辅助导入接口。
from kama_claude.core.compact.budget import truncate_tool_results
from kama_claude.core.compact.compactor import CompactionResult, Compactor

__all__ = ["Compactor", "CompactionResult", "truncate_tool_results"]
