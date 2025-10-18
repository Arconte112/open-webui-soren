from importlib import import_module

__all__ = [
    "audio",
    "images",
    "ollama",
    "openai",
    "retrieval",
    "pipelines",
    "tasks",
    "auths",
    "channels",
    "chats",
    "notes",
    "folders",
    "configs",
    "groups",
    "files",
    "functions",
    "memories",
    "models",
    "knowledge",
    "prompts",
    "evaluations",
    "tools",
    "users",
    "utils",
    "scim",
    "soren",
]

for module_name in __all__:
    import_module(f"{__name__}.{module_name}")
