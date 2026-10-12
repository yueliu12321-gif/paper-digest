from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from paper_digest.config import WeComWebhookConfig
from paper_digest.wecom_delivery import WeComDeliveryError, send_wecom_message


class DummyHTTPResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> DummyHTTPResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class WeComDeliveryTests(unittest.TestCase):
    @patch("paper_digest.wecom_delivery.urlopen")
    def test_send_wecom_message_posts_markdown_content(self, mock_urlopen) -> None:
        mock_urlopen.return_value = DummyHTTPResponse(b'{"errcode":0,"errmsg":"ok"}')
        config = WeComWebhookConfig(
            webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc",
            title_prefix="[Robot]",
            skip_if_empty=True,
        )

        send_wecom_message(
            config,
            title="[Robot] 2026-04-08 | LLM=1",
            body="# Daily Paper Digest\n\n1. [Agent systems](https://arxiv.org/abs/1)\n",
        )

        request = mock_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["msgtype"], "markdown")
        self.assertIn("# [Robot] 2026-04-08 | LLM=1", payload["markdown"]["content"])
        self.assertIn("1. Agent systems", payload["markdown"]["content"])
        self.assertIn("> https://arxiv.org/abs/1", payload["markdown"]["content"])

    @patch("paper_digest.wecom_delivery.urlopen")
    def test_send_wecom_message_rejects_nonzero_code(self, mock_urlopen) -> None:
        mock_urlopen.return_value = DummyHTTPResponse(
            b'{"errcode":93000,"errmsg":"invalid webhook url"}'
        )
        config = WeComWebhookConfig(
            webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc",
            title_prefix="[Robot]",
            skip_if_empty=True,
        )

        with self.assertRaises(WeComDeliveryError):
            send_wecom_message(
                config,
                title="[Robot] 2026-04-08 | LLM=1",
                body="Digest body",
            )

    @patch.dict("os.environ", {}, clear=True)
    @patch("paper_digest.wecom_delivery.time.sleep")
    @patch("paper_digest.wecom_delivery.urlopen")
    def test_send_wecom_message_splits_long_reports_without_truncating(
        self,
        mock_urlopen,
        mock_sleep,
    ) -> None:
        mock_urlopen.return_value = DummyHTTPResponse(b'{"errcode":0,"errmsg":"ok"}')
        config = WeComWebhookConfig(
            webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc",
            title_prefix="[Robot]",
            skip_if_empty=True,
        )
        body = "\n\n".join(
            f"第 {index} 个领域：" + "中文论文摘要 " * 120
            for index in range(1, 8)
        )

        send_wecom_message(
            config,
            title="[Robot] 每日论文简报",
            body=body,
        )

        self.assertGreater(mock_urlopen.call_count, 1)
        self.assertEqual(mock_sleep.call_count, mock_urlopen.call_count - 1)
        payloads = [
            json.loads(call.args[0].data.decode("utf-8"))
            for call in mock_urlopen.call_args_list
        ]
        contents = [payload["markdown"]["content"] for payload in payloads]
        self.assertTrue(all(len(content.encode("utf-8")) <= 3800 for content in contents))
        self.assertTrue(all("内容过长，已截断" not in content for content in contents))
        self.assertIn("第 1 个领域", "\n".join(contents))
        self.assertIn("第 7 个领域", "\n".join(contents))
        self.assertIn("第 1/", payloads[0]["markdown"]["content"])

    @patch.dict(
        "os.environ",
        {
            "GITHUB_REPOSITORY": "yueliu12321-gif/paper-digest",
            "GITHUB_SERVER_URL": "https://github.com",
        },
        clear=True,
    )
    @patch("paper_digest.wecom_delivery.urlopen")
    def test_send_wecom_message_includes_github_pages_link(
        self,
        mock_urlopen,
    ) -> None:
        mock_urlopen.return_value = DummyHTTPResponse(b'{"errcode":0,"errmsg":"ok"}')
        config = WeComWebhookConfig(
            webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc",
            title_prefix="[Robot]",
            skip_if_empty=True,
        )

        send_wecom_message(config, title="日报", body="只有一段摘要")

        payload = json.loads(mock_urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertIn(
            "https://yueliu12321-gif.github.io/paper-digest/",
            payload["markdown"]["content"],
        )
