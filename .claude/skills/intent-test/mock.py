"""Dependency mocking and LLM mock support."""

import sys
import types
import importlib
from pathlib import Path
from typing import Dict, Any, Optional

from common import resolve_project_root

# Comprehensive mock list covering mainstream AI/LLM frameworks
MOCK_MODULES = {
    # LangChain
    "langchain_core": ["messages", "prompts", "language_models", "output_parsers", "callbacks"],
    "langchain_core.messages": ["HumanMessage", "AIMessage", "SystemMessage", "ToolMessage"],
    "langchain_core.prompts": ["ChatPromptTemplate", "PromptTemplate", "MessagesPlaceholder"],
    "langchain_core.language_models": ["BaseChatModel", "BaseLanguageModel"],
    "langchain_core.output_parsers": ["JsonOutputParser", "StrOutputParser", "PydanticOutputParser"],
    "langchain_core.callbacks": ["BaseCallbackHandler"],
    "langchain_core.runnables": ["RunnablePassthrough", "RunnableLambda"],
    # LangGraph
    "langgraph": ["graph", "prebuilt", "checkpoint"],
    "langgraph.graph": ["StateGraph", "MessageGraph", "END", "START"],
    "langgraph.graph.message": ["add_messages"],
    "langgraph.prebuilt": ["ToolNode", "create_react_agent"],
    "langgraph.checkpoint": ["MemorySaver"],
    # LLM providers
    "langchain_openai": ["ChatOpenAI", "OpenAIEmbeddings"],
    "langchain_deepseek": ["ChatDeepSeek"],
    "langchain_anthropic": ["ChatAnthropic"],
    "langchain_community": ["chat_models", "llms", "embeddings"],
    "openai": ["OpenAI", "AsyncOpenAI"],
    "anthropic": ["Anthropic", "AsyncAnthropic"],
    # Other common
    "pydantic": ["BaseModel", "Field"],
    "pydantic.v1": ["BaseModel", "Field"],
}


def mock_all_dependencies():
    """Mock heavy AI framework dependencies so project modules can be imported."""
    for mod_name, attrs in MOCK_MODULES.items():
        if mod_name not in sys.modules:
            mock = types.ModuleType(mod_name)
            for attr in attrs:
                mock_cls = type(attr, (), {
                    "__init__": lambda self, *a, **kw: None,
                    "__call__": lambda self, *a, **kw: None,
                    "content": "",
                    "model_fields": {},
                })
                setattr(mock, attr, mock_cls)
            sys.modules[mod_name] = mock

    # Mock project-specific generator modules
    for gen_mod in [
        "app", "app.core", "app.core.task_plan",
        "app.core.task_plan.generator",
        "app.core.intent", "app.core.intent.generator",
        "app.services", "app.services.llm",
        "core", "core.generator", "core.llm",
    ]:
        if gen_mod not in sys.modules:
            sys.modules[gen_mod] = types.ModuleType(gen_mod)

    mock_gen = sys.modules["app.core.task_plan.generator"]
    if not hasattr(mock_gen, "_get_chat_model"):
        def _mock_get_chat_model():
            raise ImportError("Mocked — triggers fallback to default questions")
        mock_gen._get_chat_model = _mock_get_chat_model


def check_dependencies(project_root: Path = None) -> Dict:
    """Check which dependencies are available vs mocked vs missing."""
    root = project_root or resolve_project_root()
    results = {"available": [], "mocked": [], "missing": []}

    for mod_name in sorted(MOCK_MODULES.keys()):
        try:
            saved = sys.modules.pop(mod_name, None)
            importlib.import_module(mod_name)
            results["available"].append(mod_name)
        except ImportError:
            if mod_name in sys.modules:
                results["mocked"].append(mod_name)
            else:
                results["missing"].append(mod_name)
        finally:
            if saved is not None:
                sys.modules[mod_name] = saved

    src = root / "src"
    if src.is_dir():
        sys.path.insert(0, str(src))

    for mod in ["intent_recognition", "dialog", "prompts", "models"]:
        try:
            importlib.import_module(mod)
            results["available"].append(f"project:{mod}")
        except ImportError:
            results["missing"].append(f"project:{mod}")

    return results


# ===================================================================
# LLM Mock (for run_layer1)
# ===================================================================

def apply_llm_mocks(config: Dict):
    """Apply LLM mock strategies from config."""
    import asyncio

    for layer_name, layer in config.get("layers", {}).items():
        mocks = layer.get("llm_mocks", {})
        if not mocks:
            continue

        for func_name, strategy in mocks.items():
            for mod_name, mod in list(sys.modules.items()):
                if hasattr(mod, func_name):
                    original = getattr(mod, func_name)
                    if strategy == "keyword_fallback":
                        def make_fallback(fn_name, orig):
                            def fallback(*args, **kwargs):
                                if asyncio.iscoroutinefunction(orig):
                                    async def _async_fb():
                                        return keyword_fallback_for(fn_name, args, kwargs)
                                    return _async_fb()
                                return keyword_fallback_for(fn_name, args, kwargs)
                            return fallback
                        setattr(mod, func_name, make_fallback(func_name, original))
                    elif strategy == "return_true":
                        if asyncio.iscoroutinefunction(original):
                            async def _t(): return True
                            setattr(mod, func_name, _t)
                        else:
                            setattr(mod, func_name, lambda *a, **kw: True)
                    elif strategy == "return_false":
                        if asyncio.iscoroutinefunction(original):
                            async def _f(): return False
                            setattr(mod, func_name, _f)
                        else:
                            setattr(mod, func_name, lambda *a, **kw: False)
                    elif strategy.startswith("return:"):
                        import json as _json
                        val = _json.loads(strategy.split(":", 1)[1])
                        if asyncio.iscoroutinefunction(original):
                            async def _v(): return val
                            setattr(mod, func_name, _v)
                        else:
                            setattr(mod, func_name, lambda *a, **kw: val)


def keyword_fallback_for(func_name: str, args, kwargs) -> Any:
    """Generic keyword-based fallback for mocked LLM functions."""
    input_text = ""
    for arg in args:
        if isinstance(arg, str):
            input_text = arg
            break
        elif isinstance(arg, dict):
            input_text = arg.get("content", arg.get("text", arg.get("input", "")))
            break

    name_lower = func_name.lower()

    if "plan_related" in name_lower or "plan_intent" in name_lower:
        return any(kw in input_text for kw in ["计划", "学习", "plan", "learn", "安排", "制定"])
    elif "exit" in name_lower or "quit" in name_lower or "stop" in name_lower:
        return any(kw in input_text for kw in ["退出", "取消", "exit", "quit", "stop", "不要", "算了"])
    elif "yes" in name_lower or "confirm" in name_lower or "accept" in name_lower:
        return any(kw in input_text for kw in ["好的", "是的", "确认", "yes", "ok", "对"])
    elif "should_generate" in name_lower or "enough_info" in name_lower:
        return len(input_text) > 5
    return False


def load_config(config_path: str = None) -> Dict:
    """Load architecture config generated by Claude."""
    import json

    if config_path is None:
        for candidate in [
            Path(".claude/skills/intent-test/config.json"),
            Path("tests/generated/config.json"),
            Path("intent_test_config.json"),
        ]:
            if candidate.exists():
                config_path = str(candidate)
                break

    if config_path is None:
        return {}

    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)
