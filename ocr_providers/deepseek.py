import gc
import tempfile
import threading
from pathlib import Path

from grounding import parse_grounding_output  # noqa: F401  (re-exported for existing imports)

# One model per process, loaded on first use and shared by every OCR job until it is unloaded.
_model_lock = threading.Lock()
_model = None


def _load_model():
    global _model
    with _model_lock:
        if _model is None:
            _model = _build_model()
        return _model


def _build_model():
    import torch
    from transformers import AutoModel, AutoTokenizer

    model_name = "deepseek-ai/DeepSeek-OCR-2"
    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    model = AutoModel.from_pretrained(
        model_name,
        _attn_implementation="flash_attention_2",
        torch_dtype=torch.bfloat16,
        trust_remote_code=True,
        use_safetensors=True,
    ).eval().cuda()
    return model, tokenizer


PROMPT_STRUCTURED = "<image>\n<|grounding|>OCR this image. "
PROMPT_PLAIN = "<image>\nFree OCR. "


class DeepseekOcrProvider:
    #: Its structured prompt returns grounding boxes worth a second pass per image.
    grounding = True

    def run(self, path: Path, structured: bool = True) -> str:
        model, tokenizer = _load_model()
        return model.infer(
            tokenizer,
            prompt=PROMPT_STRUCTURED if structured else PROMPT_PLAIN,
            image_file=str(path),
            output_path=tempfile.gettempdir(),
            base_size=1024,
            image_size=768,
            crop_mode=True,
            save_results=False,
            eval_mode=True,
        )

    def teardown(self) -> None:
        global _model
        import torch
        with _model_lock:
            _model = None
        gc.collect()
        torch.cuda.empty_cache()
