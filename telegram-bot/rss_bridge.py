import os
import re
import logging
import asyncio
import aiohttp

import telegramify_markdown
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

MINIFLUX_URL = os.environ.get("MINIFLUX_URL", "http://miniflux:8080")
MINIFLUX_USERNAME = os.environ.get("MINIFLUX_USERNAME", "")
MINIFLUX_PASSWORD = os.environ.get("MINIFLUX_PASSWORD", "")
RSS_POLL_INTERVAL = int(os.environ.get("RSS_POLL_INTERVAL", "300"))
RSS_CHAT_ID = os.environ.get("RSS_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get(
    "OPENROUTER_MODEL", "google/gemini-2.0-flash-lite-preview-02-05:free"
)

SUMMARY_PROMPT = (
    "Summarize this article in 2-3 concise sentences. "
    "Focus on the key takeaway. Do not use markdown headers or bullet points. "
    "Just return the summary text, nothing else."
)


class MinifluxClient:
    def __init__(
        self,
        base_url: str = MINIFLUX_URL,
        username: str = MINIFLUX_USERNAME,
        password: str = MINIFLUX_PASSWORD,
    ):
        self.base_url = base_url.rstrip("/")
        self.auth = aiohttp.BasicAuth(username, password)

    async def get_unread_entries(self) -> list[dict]:
        url = f"{self.base_url}/v1/entries"
        params = {"status": "unread", "order": "published_at", "direction": "asc"}
        async with aiohttp.ClientSession(auth=self.auth) as session:
            try:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        logger.error(
                            f"Miniflux GET entries returned {resp.status}: {await resp.text()}"
                        )
                        return []
                    data = await resp.json()
                    return data.get("entries", [])
            except Exception as e:
                logger.error(f"Failed to fetch unread entries: {e}")
                return []

    async def mark_as_read(self, entry_ids: list[int]):
        if not entry_ids:
            return
        url = f"{self.base_url}/v1/entries"
        payload = {"entry_ids": entry_ids, "status": "read"}
        async with aiohttp.ClientSession(auth=self.auth) as session:
            try:
                async with session.put(url, json=payload) as resp:
                    if resp.status not in (200, 204):
                        logger.error(
                            f"Miniflux mark-as-read returned {resp.status}: {await resp.text()}"
                        )
                    else:
                        logger.info(f"Marked {len(entry_ids)} entries as read.")
            except Exception as e:
                logger.error(f"Failed to mark entries as read: {e}")


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text


async def summarize_entry(entry: dict) -> str:
    title = entry.get("title", "")
    content = entry.get("content", "")
    description = entry.get("description", "")

    raw_text = content or description
    clean_text = _strip_html(raw_text)

    if not clean_text and not title:
        return "No content available."

    if len(clean_text) > 3000:
        clean_text = clean_text[:3000]

    user_content = f"Title: {title}\n\n{clean_text}"

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": SUMMARY_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.3,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    logger.error(
                        f"OpenRouter summarization returned {resp.status}: {await resp.text()}"
                    )
                    return (
                        clean_text[:300] + "..."
                        if len(clean_text) > 300
                        else clean_text
                    )
                result = await resp.json()
                return result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Summarization failed: {e}")
        return clean_text[:300] + "..." if len(clean_text) > 300 else clean_text


def format_telegram_message(entry: dict, summary: str) -> str:
    feed_title = entry.get("feed", {}).get("title", "RSS")
    title = entry.get("title", "No Title")
    url = entry.get("url", "")

    msg = f"📡 *{feed_title}*\n*{title}*\n\n{summary}"
    if url:
        msg += f"\n\n🔗 [Read more]({url})"

    return telegramify_markdown.markdownify(msg)


async def poll_and_notify(bot):
    if not MINIFLUX_USERNAME or not MINIFLUX_PASSWORD:
        logger.warning("MINIFLUX_USERNAME/PASSWORD not set. RSS bridge disabled.")
        return

    if not RSS_CHAT_ID:
        logger.warning("RSS_CHAT_ID not set. RSS bridge disabled.")
        return

    client = MinifluxClient()
    logger.info(
        f"RSS bridge started. Polling every {RSS_POLL_INTERVAL}s. Chat ID: {RSS_CHAT_ID}"
    )

    while True:
        try:
            entries = await client.get_unread_entries()

            if entries:
                logger.info(f"Found {len(entries)} unread entries.")
                processed_ids = []

                for entry in entries:
                    try:
                        summary = await summarize_entry(entry)
                        formatted = format_telegram_message(entry, summary)

                        chat_id = (
                            int(RSS_CHAT_ID)
                            if RSS_CHAT_ID.lstrip("-").isdigit()
                            else RSS_CHAT_ID
                        )
                        await bot.send_message(
                            chat_id=chat_id,
                            text=formatted,
                            parse_mode=ParseMode.MARKDOWN_V2,
                            disable_web_page_preview=True,
                        )
                        processed_ids.append(entry["id"])
                        await asyncio.sleep(1)
                    except Exception as e:
                        logger.error(f"Failed to process entry {entry.get('id')}: {e}")

                await client.mark_as_read(processed_ids)
            else:
                logger.debug("No unread entries.")

        except Exception as e:
            logger.error(f"RSS poll cycle failed: {e}")

        await asyncio.sleep(RSS_POLL_INTERVAL)


async def start_rss_polling(bot):
    asyncio.create_task(poll_and_notify(bot))
