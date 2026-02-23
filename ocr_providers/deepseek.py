import tempfile
from pathlib import Path

import streamlit as st


@st.cache_resource
def _load_model():
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


class DeepseekOcrProvider:
    def run(self, path: Path) -> str:
        model, tokenizer = _load_model()
        return model.infer(
            tokenizer,
            prompt="<image>\nFree OCR. ",
            image_file=str(path),
            output_path=tempfile.gettempdir(),
            base_size=1024,
            image_size=768,
            crop_mode=True,
            save_results=False,
            eval_mode=True,
        )

    def teardown(self) -> None:
        import torch
        _load_model.clear()
        torch.cuda.empty_cache()
