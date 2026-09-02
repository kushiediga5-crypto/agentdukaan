"""Buyer plane: the agent runtime.

The runtime is brain-agnostic and transport-agnostic:
  - Brain: ScriptedBrain (deterministic, no API key) | LLMBrain (Anthropic/OpenAI)
  - Tools: MCPToolClient (real MCP server) | DirectToolClient (in-process, for tests)

The runtime owns the loop, the step budget, and the safety stops. Brains only
decide the next action. This is the "hand-rolled agent, no framework" core.
"""
