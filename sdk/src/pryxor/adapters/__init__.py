"""
Framework adapters for Pryxor.

Each adapter lives in its own module and imports its framework lazily, so
importing this package never requires any framework to be installed. Import
the one you need:

    from pryxor.adapters.langchain import PryxorTool, pryxor_tool
    from pryxor.adapters.crewai import PryxorTool, pryxor_tool
    from pryxor.adapters.openai_agents import PryxorTool, pryxor_tool

Only ``langchain_core`` / ``crewai`` / ``openai-agents`` respectively are
required, and only when you import that specific adapter.
"""

from __future__ import annotations

__all__ = ["langchain", "crewai", "openai_agents"]
