from pathlib import Path

from ollama import chat, generate


class OllamaOcrProvider:
    MODEL = "glm-ocr:latest"
    #: Ignores the structured flag, so a second "structured" pass would just repeat the OCR.
    grounding = False
    PROMPT = "Extract all text from this image exactly as shown, preserving layout."

    def run(self, path: Path, structured: bool = False) -> str:
        response = chat(
            model=self.MODEL,
            messages=[{"role": "user", "content": self.PROMPT, "images": [path.read_bytes()]}],
        )
        return response.message.content

    def teardown(self) -> None:
        generate(model=self.MODEL, prompt="", keep_alive=0)
