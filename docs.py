"""Hujjatlar bilan ishlash: yuklangan faylni saqlash, kontekstga qo'shish,
yangi fayl/taqdimot yaratish (best-of-N bilan)."""
import logging
from dataclasses import dataclass

import config
import deck as deck_mod
import files
import images
import llm
import state

log = logging.getLogger("docs")


class DocError(Exception):
    """Foydalanuvchiga ko'rsatish mumkin bo'lgan xato."""


# Limitlar (config.py da bo'lishi kerak, aks holda default)
MAX_FILE_BYTES = getattr(config, "FILE_MAX_BYTES", 20 * 1024 * 1024)
MAX_TEXT_CHARS = getattr(config, "FILE_MAX_CHARS", 20000)

# Best-of-N: taqdimot uchun nechta modeldan sinash
BEST_OF_N = max(1, min(getattr(config, "BEST_OF_N", 2), 3))


@dataclass
class GenerateResult:
    """docs.generate() natijasi."""
    name: str
    data: bytes
    text: str
    summary: str = ""
    images_added: int = 0


# ================================================================ YUKLANGAN FAYL

def store(uid: int, name: str, text: str, truncated: bool = False) -> None:
    """Yuklangan faylning matnini saqlaydi."""
    state.set_file(uid, name, text, truncated)


def get(uid: int) -> dict | None:
    """Saqlangan faylni qaytaradi: {"name", "text", "truncated", "ts"}."""
    return state.get_file(uid)


def clear(uid: int) -> None:
    """Foydalanuvchining saqlangan faylini o'chiradi."""
    with state._lock:
        state._files.pop(uid, None)


def context_block(doc: dict) -> str:
    """System promptga qo'shiladigan fayl konteksti."""
    if not doc or not doc.get("text"):
        return ""
    name = doc.get("name", "fayl")
    text = doc["text"]
    truncated = doc.get("truncated", False)
    note = " (qisqartirilgan)" if truncated else ""
    return (
        f"\n\n--- FOYDALANUVCHI YUKLAGAN FAYL: «{name}»{note} ---\n"
        f"{text}\n"
        f"--- FAYL TUGADI ---\n"
        f"Foydalanuvchi savoli yoki ko'rsatmasi shu faylga tegishli bo'lsa, javobda fayl mazmunidan foydalan. "
        f"Agar faylni tahrirlash yoki boshqa formatga o'tkazish so'ralsa, to'liq yangi mazmunni qaytar."
    )


# ================================================================ YUKLASHNI TEKSHIRISH

def precheck(name: str, mime_type: str = "") -> None:
    """Faylni yuklab olishdan OLDIN qo'llab-quvvatlanishini tekshiradi.
    Qo'llab-quvvatlanmasa — DocError ko'taradi (foydalanuvchi kutmasin)."""
    ext = files.ext_of(name)
    if not ext:
        raise DocError(f"Fayl nomida kengaytma yo'q: «{name}». Qayta nomlab yuboring.")
    if ext in files.BUILD_EXTS:
        return
    if ext in files.OLD_EXTS:
        raise DocError(
            f".{ext} eski format. Uni .docx / .xlsx / .pptx / .pdf ga o'tkazib qayta yuboring."
        )
    if ext in files.TEXT_EXTS:
        return
    if not ext and mime_type.startswith("text/"):
        return
    raise DocError(
        f"Bu formatni o'qiy olmayman (.{ext}). Qo'llab-quvvatlanadi: {files.SUPPORTED_HINT}."
    )


def extract(name: str, raw: bytes, mime_type: str = "") -> tuple[str, bool]:
    """Baytlardan matn ajratadi. (matn, qisqartirilganmi) qaytaradi."""
    text = files.extract_text(raw, name)
    return files.clip(text) if hasattr(files, "clip") else _clip(text)


def _clip(text: str) -> tuple[str, bool]:
    if len(text) > MAX_TEXT_CHARS:
        return text[:MAX_TEXT_CHARS], True
    return text, False


# ================================================================ YARATISH — ASOSIY

def generate(history: list[dict], instruction: str, doc: dict | None = None,
             allow_images: bool = True) -> GenerateResult:
    """Ko'rsatma asosida fayl yoki taqdimot yaratadi.

    - Taqdimot (pptx) bo'lsa: best-of-N bilan eng sifatli JSON tanlanadi
    - Boshqa format (docx, xlsx, pdf, csv, txt) bo'lsa: bitta chaqiruv + files.build_file
    """
    # Taqdimot ekanligini aniqlash
    is_deck = bool(deck_mod._PRESENTATION_HINTS.search(instruction)) if hasattr(deck_mod, "_PRESENTATION_HINTS") else _is_deck(instruction)
    if is_deck:
        return _generate_deck(history, instruction, doc, allow_images)
    return _generate_file(history, instruction, doc)


def _is_deck(text: str) -> bool:
    import re
    return bool(re.search(r"(taqdimot|prezentatsiya|presentation|slayd|slide|pptx|powerpoint)", text or "", re.I))


# ================================================================ TAQDIMOT (best-of-N)

def _score_deck(deck_data: dict) -> int:
    """Taqdimot sifatini baholaydi (0-100)."""
    slides = deck_data.get("slides", [])
    n = len(slides)
    if n < 5:
        return n * 5
    score = 50
    score += min(n * 3, 30)
    score += sum(1 for s in slides if s.get("image_prompt")) * 2
    score += sum(1 for s in slides if s.get("layout") not in ("title", "section", "closing")) * 1
    return min(score, 100)


def _generate_deck(history: list[dict], instruction: str, doc: dict | None,
                   allow_images: bool) -> GenerateResult:
    """Taqdimotni best-of-N bilan yaratadi: bir nechta modeldan JSON olib, eng yaxshisini tanlaydi."""
    # 1. Best-of-N: bir nechta modeldan sinash
    providers = llm._available_providers("file", False)[:BEST_OF_N]
    if not providers:
        raise DocError("Hech qanday API kaliti sozlanmagan.")

    system = llm.PRESENTATION_PROMPT
    if doc:
        system += context_block(doc)

    base_messages = list(history) + [{"role": "user", "content": instruction}]
    candidates: list[tuple[int, dict, str]] = []

    for provider in providers:
        try:
            log.info("Taqdimot yaratilmoqda: %s", provider)
            text = llm._call_one(
                provider,
                base_messages,
                system,
                None,
                config.PRESENTATION_MAX_TOKENS,
                0.7,
            )
            data = deck_mod.parse_json(text)
            norm = deck_mod.normalize(data)
            score = _score_deck(norm)
            candidates.append((score, norm, provider))
            log.info("✓ %s: %s slayd, score=%s", provider, len(norm["slides"]), score)
        except (llm.LLMError, deck_mod.DeckError) as exc:
            log.warning("✗ %s taqdimot xatosi: %s", provider, exc)

    # 2. Fallback: agar hamma best-of-N muvaffaqiyatsiz bo'lsa — oddiy llm.chat
    if not candidates:
        log.warning("Best-of-N muvaffaqiyatsiz, oddiy llm.chat sinab ko'riladi")
        try:
            text = llm.chat(history, instruction, system, task="file",
                            max_tokens=config.PRESENTATION_MAX_TOKENS, temperature=0.7)
            data = deck_mod.parse_json(text)
            norm = deck_mod.normalize(data)
            candidates.append((_score_deck(norm), norm, "fallback"))
        except (llm.LLMError, deck_mod.DeckError) as exc:
            raise DocError(f"Taqdimot yaratib bo'lmadi: {exc}") from exc

    # 3. Eng yaxshisini tanlash
    candidates.sort(key=lambda x: x[0], reverse=True)
    best_score, best_deck, best_provider = candidates[0]
    log.info("Eng yaxshi taqdimot: %s (score=%s, %s slayd)",
             best_provider, best_score, len(best_deck["slides"]))

    # 4. Rasmlarni yaratish
    images_map: dict[str, bytes] = {}
    images_added = 0
    if allow_images and config.IMAGES_ENABLED:
        prompts = deck_mod.image_prompts(best_deck)
        for p in prompts:
            try:
                images_map[p] = images.make_image(p)
                images_added += 1
            except Exception as exc:  # noqa: BLE001 — bitta rasm xato bersa, qolganlari davom etsin
                log.warning("Rasm yaratilmadi (%s): %s", p[:60], exc)

    # 5. PPTX qurish
    try:
        pptx_bytes = deck_mod.build_pptx(best_deck, images_map)
    except Exception as exc:
        raise DocError(f"PPTX faylini qurib bo'lmadi: {exc}") from exc

    # 6. Nom va xulosa
    deck_title = best_deck.get("title") or instruction[:60] or "Taqdimot"
    safe_title = files.safe_name(deck_title, "taqdimot.pptx")
    if not safe_title.lower().endswith(".pptx"):
        safe_title = files.safe_name(deck_title, "taqdimot") + ".pptx"
    n_slides = len(best_deck["slides"])
    summary = f"Taqdimot tayyor: {n_slides} slayd, {images_added} rasm, mavzu «{deck_title}»"

    # 7. Matn (keyingi tahrirlar uchun)
    text_for_state = deck_mod.compact(best_deck)

    return GenerateResult(
        name=safe_title,
        data=pptx_bytes,
        text=text_for_state,
        summary=summary,
        images_added=images_added,
    )


# ================================================================ ODDIY FAYL (docx, xlsx, pdf, csv, txt)

def _generate_file(history: list[dict], instruction: str, doc: dict | None) -> GenerateResult:
    """Oddiy fayl yaratadi (bitta model chaqiruvi)."""
    system = (
        "Sen professional hujjat yaratuvchisan. Foydalanuvchi ko'rsatmasi asosida "
        "to'liq, tayyor fayl mazmunini yoz.\n\n"
        "Javob formati (aniq amal qil):\n"
        "FILENAME: fayl_nomi.kengaytma\n"
        "SUMMARY: qisqa izoh (1-2 gap)\n"
        "=====\n"
        "faylning to'liq mazmuni (markdown yoki CSV formatida)\n\n"
        "QOIDALAR:\n"
        "- FILENAME da to'g'ri kengaytma ko'rsat (.docx, .xlsx, .pdf, .csv, .txt)\n"
        "- Markdown formatida yoz (# sarlavha, - bullet, **qalin**, | jadval |)\n"
        "- Excel/CSV uchun: ### Varaq nomi va CSV qatorlar\n"
        "- Hech qanday qo'shimcha izoh yozma\n"
    )
    if doc:
        system += context_block(doc)

    try:
        answer = llm.chat(history, instruction, system, task="file",
                          max_tokens=config.PRESENTATION_MAX_TOKENS, temperature=0.5)
    except llm.LLMError as exc:
        raise DocError(f"Fayl mazmuni yaratilmadi: {exc}") from exc

    # Nom, mazmun, izohni ajratish
    default_name = _guess_name(instruction)
    try:
        name, body, summary = files.parse_file_response(answer, default_name)
    except files.FileError as exc:
        raise DocError(str(exc)) from exc

    # Fayl baytlarini yaratish
    try:
        data = files.build_file(name, body)
    except files.FileError as exc:
        raise DocError(str(exc)) from exc

    if not summary:
        summary = f"Fayl tayyor: {name}"

    return GenerateResult(
        name=name,
        data=data,
        text=body,
        summary=summary,
        images_added=0,
    )


def _guess_name(instruction: str) -> str:
    """Ko'rsatmalardan taxminiy fayl nomi va kengaytmasini ajratadi."""
    t = (instruction or "").lower()
    ext = "docx"
    if "excel" in t or "xlsx" in t or "jadval" in t or "таблица" in t:
        ext = "xlsx"
    elif "pdf" in t:
        ext = "pdf"
    elif "csv" in t:
        ext = "csv"
    elif "txt" in t or "matn" in t:
        ext = "txt"
    elif "word" in t or "docx" in t or "hujjat" in t:
        ext = "docx"
    return f"fayl.{ext}"