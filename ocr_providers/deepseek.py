import ast
import re
import tempfile
from pathlib import Path

import streamlit as st

from models import DetectedBox

_GROUNDING_RE = re.compile(
    r"<\|ref\|>(.*?)<\|/ref\|><\|det\|>(.*?)<\|/det\|>", re.DOTALL
)


def parse_grounding_output(raw: str) -> tuple[str, list[DetectedBox]]:
    boxes: list[DetectedBox] = []
    clean_parts: list[str] = []
    last_end = 0

    for match in _GROUNDING_RE.finditer(raw):
        before = raw[last_end:match.start()]
        if before.strip():
            clean_parts.append(before)
        last_end = match.end()

        ref_text = match.group(1).strip()
        det_raw = match.group(2).strip()

        try:
            parsed = ast.literal_eval(det_raw)
        except (SyntaxError, ValueError):
            if ref_text:
                clean_parts.append(ref_text)
            continue
        if isinstance(parsed, list) and parsed:
            if isinstance(parsed[0], list):
                coords = [c for c in parsed if len(c) == 4]
            elif len(parsed) == 4:
                coords = [parsed]
            else:
                coords = []
        else:
            coords = []

        if coords:
            boxes.append(DetectedBox(ref_type=str(len(boxes)), coords=coords, text=ref_text))
            clean_parts.append(ref_text)

    tail = raw[last_end:]
    if tail.strip():
        clean_parts.append(tail)

    return "\n".join(clean_parts), boxes


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


PROMPT_STRUCTURED = "<image>\n<|grounding|>OCR this image. "
PROMPT_PLAIN = "<image>\nFree OCR. "


class DeepseekOcrProvider:
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
        import torch
        _load_model.clear()
        torch.cuda.empty_cache()
