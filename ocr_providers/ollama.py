from pathlib import Path

from ollama import chat, generate


class OllamaOcrProvider:
    MODEL = "glm-ocr:latest"
    PROMPT = "Extract all text from this image exactly as shown, preserving layout."

    def run(self, path: Path) -> str:
        response = chat(
            model=self.MODEL,
            messages=[{"role": "user", "content": self.PROMPT, "images": [path.read_bytes()]}],
        )
        return response.message.content

    def teardown(self) -> None:
        generate(model=self.MODEL, prompt="", keep_alive=0)
