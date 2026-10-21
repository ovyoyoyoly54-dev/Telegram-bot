"""Model markdown'ini Telegram HTML'ga o'girish va uzun matnni xavfsiz bo'laklash."""
import html
import re

_FENCE = re.compile(r"(```[\s\S]*?```)")
_INLINE_CODE = re.compile(r"(`[^`\n]+`)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$")
_BULLET = re.compile(r"^(\s*)[*\-+]\s+(.*)$")
_RULE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
_LANG = re.compile(r"^([\w+#.-]+)\n")


# ---------------------------------------------------------------- bo'lish

def _balance_fences(chunks: list[str]) -> list[str]:
    """Kod bloki (```) bo'laklar orasida kesilib qolsa, har bo'lakda yopib, keyingisida qayta ochadi."""
    out, is_open = [], False
    for chunk in chunks:
        if is_open:
            chunk = "```\n" + chunk
        if chunk.count("```") % 2 == 1:
            chunk += "\n```"
            is_open = True
        else:
            is_open = False
        out.append(chunk)
    return out


def split_text(text: str, limit: int) -> list[str]:
    """Matnni qator chegarasidan bo'lib, limitdan oshmaydigan bo'laklarga ajratadi."""
    # TUZATILDI: limit juda kichik bo'lsa, xato bermaslik uchun
    if limit < 1:
        limit = 1
    chunks, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if cur and len(cur) + 1 + len(line) > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    return _balance_fences(chunks or [""])


# ---------------------------------------------------------------- markdown → HTML

def _inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*(?![\s*])(.+?)(?<![\s*])\*(?![\w*])", r"<i>\1</i>", s)
    s = re.sub(r"\[([^\]\n]+)\]\((https?://[^\s)\"]+)\)", r'<a href="\2">\1</a>', s)
    return s


def _inline_line(line: str) -> str:
    out = []
    for i, piece in enumerate(_INLINE_CODE.split(line)):
        if i % 2 == 1:
            # TUZATILDI: yopilmagan ` bo'lsa, xato bermaslik uchun
            if piece.startswith("`") and piece.endswith("`") and len(piece) > 2:
                out.append("<code>" + html.escape(piece[1:-1], quote=False) + "</code>")
            else:
                out.append(_inline(piece))
        else:
            out.append(_inline(piece))
    return "".join(out)


def _text_block(block: str) -> str:
    lines = []
    for line in block.split("\n"):
        if _RULE.match(line):
            lines.append("──────────")
            continue
        m = _HEADING.match(line)
        if m:
            lines.append("<b>" + _inline_line(m.group(1)) + "</b>")
            continue
        m = _BULLET.match(line)
        if m:
            lines.append(m.group(1) + "• " + _inline_line(m.group(2)))
            continue
        lines.append(_inline_line(line))
    return "\n".join(lines)


def to_telegram_html(text: str) -> str:
    out = []
    for part in _FENCE.split(text):
        # TUZATILDI: kod bloki yopilmagan bo'lsa ham ishlash uchun
        if len(part) >= 6 and part.startswith("```") and part.endswith("```"):
            body = part[3:-3]
            lang = ""
            m = _LANG.match(body)
            if m:
                lang, body = m.group(1), body[m.end():]
            elif body.startswith("\n"):
                body = body[1:]
            code = html.escape(body.strip("\n"), quote=False)
            if lang:
                out.append(f'<pre><code class="language-{lang}">{code}</code></pre>')
            else:
                out.append(f"<pre>{code}</pre>")
        else:
            out.append(_text_block(part))
    return "".join(out)