# Design Decisions
This is more of an AI(both LLM and classic ML)-assisted manual workflow due to these real-world problems I encountered:
- Most OCR models are abysmal at half-width katakana. I tried to identify the boxes then stretching them, but half-width dakutens often still break them. (In case you want to give it a shot, I found 2.5x-4x width to be the sweet spot for those Cardnet slips) Apparently CLOVA can handle it, but I had to give up due to time constraints.
- Japanese calendars are sometimes used undeclared(looking at you, ゆうちょ銀行) and there are absolutely no way to differentiate between Heisei 25(2013) and 2025 in YY-MM-DD format (nor Reiwa 6 vs 2006), unless you are willing to constrain the date range or build some sort of system that allow rules for each merchant.

The repo has multi-provider setup for flexibility. In my testing, Deepseek OCR 2 and gpt5.4 for batch processing, and for receipts that Deepseek OCR 2 failed, GLM-OCR can often get it right. Qwen3-8b is usable if you want to process everything fully locally, but gpt5.4's better accuracy really cuts down the manual re-editing.

I also experimented with Datalab's Chandra, but it was too slow to host locally even at int4 (on a GeForce RTX 4070 Super(12GB VRAM)). The accuracy was very good, but it also do not handle half-width katakana well, so the subscription felt too expensive to justify.
