"""Regression tests for presentation request routing and PPTX delivery."""

import io
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GROQ_API_KEY", "test-key")

from pptx import Presentation

import bot
import config
import docs
import presenton


def _sample_pptx(slide_count=12):
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    for number in range(slide_count):
        slide = presentation.slides.add_slide(blank)
        box = slide.shapes.add_textbox(0, 0, 100, 100)
        box.text = f"Mazmunli slayd {number + 1}"
    output = io.BytesIO()
    presentation.save(output)
    return output.getvalue()


class PresentationRoutingTests(unittest.TestCase):
    def test_uzbek_12_slide_request_generates_and_sends_real_pptx(self):
        request = "Menga sun\u2019iy intellekt haqida 12 slaydli prezentatsiya qilib ber"
        pptx_bytes = _sample_pptx(12)
        result = docs.GenerateResult(
            name="suniy_intellekt.pptx",
            data=pptx_bytes,
            text="taqdimot",
            summary="Tayyor",
        )
        message = type("Message", (), {
            "from_user": type("User", (), {"id": 42})(),
            "chat": type("Chat", (), {"id": 42})(),
            "message_id": 100,
        })()

        with (
            patch.object(config, "IMAGES_ENABLED", False),
            patch.object(config, "FILE_DAILY_LIMIT", 10),
            patch.object(bot.state, "files_left", return_value=5),
            patch.object(bot.state, "get_history", return_value=[]),
            patch.object(bot.state, "count_file"),
            patch.object(bot.state, "remember"),
            patch.object(docs, "get", return_value=None),
            patch.object(docs, "store"),
            patch.object(docs, "generate", return_value=result) as generate,
            patch.object(bot.bot, "send_chat_action"),
            patch.object(bot.bot, "send_document") as send_document,
            patch.object(bot.llm, "classify_vote") as classify,
        ):
            bot.route_text(message, request)

        generate.assert_called_once()
        self.assertEqual(generate.call_args.args[1], request)
        classify.assert_not_called()
        send_document.assert_called_once()
        sent_file = send_document.call_args.args[1]
        sent_name = sent_file.name
        sent_data = sent_file.getvalue()
        self.assertTrue(sent_name.endswith(".pptx"))
        self.assertEqual(len(Presentation(io.BytesIO(sent_data)).slides), 12)

    def test_presenton_error_falls_back_to_internal_deck(self):
        internal_result = docs.GenerateResult(
            name="fallback.pptx",
            data=_sample_pptx(12),
            text="fallback",
        )
        with (
            patch.object(config, "PRESENTON_ENABLED", True),
            patch.object(
                presenton,
                "generate_presentation",
                side_effect=presenton.PresentonError("service error"),
            ) as api,
            patch.object(docs, "_generate_deck", return_value=internal_result) as fallback,
        ):
            result = docs.generate(
                [],
                "Menga sun\u2019iy intellekt haqida 12 slaydli prezentatsiya qilib ber",
            )

        self.assertEqual(result, internal_result)
        self.assertEqual(api.call_args.kwargs["n_slides"], 12)
        fallback.assert_called_once()

    def test_presentation_requests_are_recognized_in_three_languages(self):
        for text in (
            "Prezentatsiya qilib ber",
            "12 slaydli taqdimot yarat",
            "PPTX tayyorla",
            "\u0421\u0434\u0435\u043b\u0430\u0439 \u043f\u0440\u0435\u0437\u0435\u043d\u0442\u0430\u0446\u0438\u044e",
            "\u0421\u043e\u0437\u0434\u0430\u0439 12 \u0441\u043b\u0430\u0439\u0434\u043e\u0432",
            "Create a presentation",
            "Prepare 12 slides",
        ):
            with self.subTest(text=text):
                self.assertTrue(bot.is_presentation_request(text))


if __name__ == "__main__":
    unittest.main()
