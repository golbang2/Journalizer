import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services import llm


def response(status, body):
    return SimpleNamespace(status_code=status, headers={}, json=lambda: body, text=json.dumps(body))


SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"], "additionalProperties": False}
PDF = llm.File(name="a.pdf", media_type="application/pdf", data=b"%PDF")


class ProviderTests(unittest.TestCase):
    def test_openai_sends_strict_schema_and_files(self):
        body = {"choices": [{"finish_reason": "stop", "message": {"content": '{"x": 3}'}}]}
        with patch("app.services.llm.httpx.post", return_value=response(200, body)) as post:
            out = llm.complete(llm.Client("openai", "sk-1", "gpt-9-mini"), "sys", ["읽어 주세요", PDF], SCHEMA)
        self.assertEqual(out, {"x": 3})
        url, sent = post.call_args.args[0], post.call_args.kwargs["json"]
        self.assertEqual(url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(post.call_args.kwargs["headers"], {"Authorization": "Bearer sk-1"})
        self.assertEqual(sent["response_format"]["json_schema"]["strict"], True)
        self.assertEqual(sent["messages"][0], {"role": "system", "content": "sys"})
        self.assertEqual([p["type"] for p in sent["messages"][1]["content"]], ["text", "file"])

    def test_openai_refusal_and_length_are_errors(self):
        for body, message in (({"choices": [{"message": {"refusal": "no"}}]}, "거절"),
                              ({"choices": [{"finish_reason": "length", "message": {"content": "{"}}]}, "길이")):
            with patch("app.services.llm.httpx.post", return_value=response(200, body)), self.assertRaises(llm.LlmError) as ctx:
                llm.complete(llm.Client("openai", "k", "m"), "", ["q"], SCHEMA)
            self.assertIn(message, str(ctx.exception))

    def test_auth_errors_are_readable(self):
        with patch("app.services.llm.httpx.post", return_value=response(401, {"error": {"message": "bad key"}})), self.assertRaises(llm.LlmError) as ctx:
            llm.complete(llm.Client("openai", "k", "m"), "", ["q"], SCHEMA)
        self.assertIn("인증", str(ctx.exception))

    def test_model_lists_keep_text_models_newest_first(self):
        openai = {"data": [{"id": i} for i in ("gpt-8-mini", "gpt-9-mini", "gpt-9-audio", "text-embedding-3", "whisper-1", "o9", "dall-e-3")]}
        with patch("app.services.llm.httpx.get", return_value=response(200, openai)):
            models = llm.list_models("openai", "k")
        self.assertEqual(models, ["o9", "gpt-9-mini", "gpt-8-mini"])
        self.assertEqual(llm.default_model("openai", models), "gpt-9-mini")
        with self.assertRaises(llm.LlmError):
            llm.list_models("gemini", "k")  # no longer offered

    def test_a_model_must_be_chosen(self):
        with self.assertRaises(llm.LlmError):
            llm.complete(llm.Client("openai", "k", ""), "", ["q"], SCHEMA)


if __name__ == "__main__":
    unittest.main()
