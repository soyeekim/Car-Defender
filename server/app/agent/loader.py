import importlib


def load_agent_class(path: str) -> type:
    if ":" not in path:
        raise ImportError(f"AGENT_IMPL 형식은 'pkg.module:Class' 이어야 해요: {path!r}")
    module_name, _, class_name = path.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as e:
        raise ImportError(f"AGENT_IMPL 모듈을 찾을 수 없어요: {module_name}") from e
    cls = getattr(module, class_name, None)
    if cls is None:
        raise ImportError(f"AGENT_IMPL 클래스를 찾을 수 없어요: {path}")
    return cls
