from app import llm


def test_harness_input_preserves_text_and_images():
    messages = [
        {"role": "system", "content": "只回答中文"},
        {"role": "user", "content": [
            {"type": "text", "text": "看这张图"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abc"}},
        ]},
    ]
    result = llm._harness_input(messages)
    assert result[0]["text"] == "[system]\n只回答中文"
    assert result[1]["text"] == "[user]\n看这张图"
    assert result[2] == {"type": "image", "data": "abc", "mimeType": "image/jpeg"}
