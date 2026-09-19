from .ollama import OllamaOcrProvider


def _deepseek_available() -> bool:
    from importlib.util import find_spec

    if find_spec("torch") is None or find_spec("transformers") is None:
        return False
    import torch
    return torch.cuda.is_available()


def _build_providers() -> dict:
    providers: dict = {f"Ollama - {OllamaOcrProvider.MODEL}": OllamaOcrProvider()}
    if _deepseek_available():
        from .deepseek import DeepseekOcrProvider
        providers["DeepSeek OCR 2"] = DeepseekOcrProvider()
    return providers


OCR_PROVIDERS = _build_providers()
