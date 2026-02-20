# To run this code you need to install the following dependencies:
# pip install google-genai

import os
from google import genai
from google.genai import types


def generate():
    client = genai.Client(
        api_key=os.environ.get("GEMINI_API_KEY"),
    )

    model = "gemini-3-flash-preview"
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_bytes(
                    mime_type="image/png",
                    data=base64.b64decode(
                        """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"""
                    ),
                ),
                types.Part.from_text(text="""give me the keyboard shortcuts to pass this step of an installer"""),
            ],
        ),
        types.Content(
            role="model",
            parts=[
                types.Part.from_text(text="""**Pinpointing Shortcuts**

I'm currently analyzing the dialog box image. My primary focus right now is to pinpoint the keyboard shortcuts assigned to the \"Yes\" and \"No\" buttons. I've noted the dialog's title and the confirmation text, which are \"Are you sure?\" and \"Do you want\". I am looking at visual cues within the button labels themselves to identify the corresponding shortcuts.


"""),
                types.Part.from_text(text="""Based on the visual cues in the dialog box (the underlined letters), here are the keyboard shortcuts you can use:

*   **To select \"Yes\":** Press **`Alt + Y`**. Since the \"Yes\" button has a focus border around it, you can also just press **`Enter`**.
*   **To select \"No\":** Press **`Alt + N`** or the **`Esc`** key.

You can also use the **`Tab`** key to switch focus between the buttons and then press **`Spacebar`** or **`Enter`** to click the selected one."""),
            ],
        ),
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text="""INSERT_INPUT_HERE"""),
            ],
        ),
    ]
    generate_content_config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(
            thinking_level="HIGH",
        ),
    )

    for chunk in client.models.generate_content_stream(
        model=model,
        contents=contents,
        config=generate_content_config,
    ):
        print(chunk.text, end="")

if __name__ == "__main__":
    generate()


