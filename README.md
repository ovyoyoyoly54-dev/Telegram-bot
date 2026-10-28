# 🤖 AI Telegram Bot (Groq + Gemini + Cloudflare)

Bitta botda: suhbat xotirasi, 4 ta javob rejimi, rasmni tushunish, rasm chizish va tahrirlash, ovozli xabarlarni matnga aylantirish. **GitHub → Northflank** orqali avtomatik deploy qilinadi.

## Imkoniyatlar

| Funksiya | Qanday ishlaydi |
|---|---|
| Suhbat | Groq `gpt-oss-120b`, har foydalanuvchi uchun xotira |
| Zaxira | Groq limiti tugasa yoki xato bersa, avtomatik **Gemini**'ga o'tadi (agar `GEMINI_API_KEY` berilgan bo'lsa) |
| Rasmni tushunish | Rasm yuboring (tagiga savol yozish mumkin) |
| Rasm chizish | «… rasm chiz» deb yozing yoki `/rasm tavsif` |
| Rasm tahrirlash | Rasm yuborib tagiga «fonni tun qil» deb yozing yoki `/tahrir …` |
| Ovozli xabar | Whisper orqali matnga o'giriladi va javob beriladi |
| Rejimlar | `/mode` — Do'stona, Dasturchi, O'qituvchi, Qisqa |
| Himoya | `ALLOWED_USERS`, tezlik limiti, kunlik rasm limiti, bir vaqtda bitta so'rov |
| Web ilova | `APP_PASSWORD` sozlanganda, 8080-portda parol bilan himoyalangan web chat va fayl yuklash |

## Fayllar

```
bot.py          # handlerlar, yo'naltirish, ishga tushirish
llm.py          # Groq + Gemini zaxira, vision, ovoz, tasniflagich
images.py       # Cloudflare rasm chizish/tahrirlash, Pillow
formatting.py   # markdown → Telegram HTML, uzun matnni bo'lish
state.py        # xotiradagi holat va limitlar
config.py       # muhit o'zgaruvchilari
keyboards.py    # tugmalar va rejimlar
Dockerfile      # Northflank shu orqali quradi
```

## Muhit o'zgaruvchilari

| Nomi | Majburiy | Izoh |
|---|---|---|
| `TELEGRAM_TOKEN` | ✅ | @BotFather dan |
| `GROQ_API_KEY`, `OPENROUTER_API_KEY` yoki `GEMINI_API_KEY` | ✅ | Asosiy AI xizmatlaridan kamida bittasi kerak. OpenRouter uchun `OPENROUTER_API_KEY_1` va `_2` ham ishlaydi |
| `APP_PASSWORD` | ❌ | Ixtiyoriy web ilova paroli. Sozlanganda 8080-portdagi health-check o'rniga web ilova ishlaydi |
| `CF_ACCOUNT_ID`, `CF_API_TOKEN` | ❌ | Ikkalasi ham berilsa, rasm funksiyalari yoqiladi |
| `GEMINI_API_KEY` | ❌ | Zaxira (aistudio.google.com/apikey) |
| `PRESENTON_API_KEY` | ❌ | Sozlanganda `/fayl ... taqdimot` so'rovlari Presenton API orqali PPTX yaratadi; bo'sh bo'lsa ichki generator ishlaydi |
| `PRESENTON_BASE_URL` | ❌ | Presenton API manzili (standart `https://api.presenton.ai`) |
| `PRESENTON_TIMEOUT` | ❌ | Presenton yaratish/yuklab olish timeouti, soniyada (standart 300) |
| `ALLOWED_USERS` | ❌ | Vergul bilan ID'lar: `123456,789012`. Bo'sh = hamma uchun ochiq. ID'ni botdagi `/id` dan oling |
| `IMAGE_DAILY_LIMIT` | ❌ | Bir kishiga kuniga rasm soni (standart 5, `0` = cheksiz) |
| `RATE_LIMIT_SECONDS` | ❌ | Xabarlar orasidagi minimal vaqt (standart 3) |
| `CHAT_MODEL`, `VISION_MODEL`, `CLASSIFIER_MODEL` | ❌ | Groq model nomlari. Model topilmasa ("model topilmadi") shu yerdan almashtiring |
| `OPENROUTER_MODEL`, `OPENROUTER_VISION_MODEL` | ❌ | OpenRouter uchun matn va rasmni alohida qo'llaydigan model nomlari; vision modeli bo'sh bo'lsa OpenRouter rasm tahliliga tanlanmaydi |
| `CHAT_MAX_TOKENS`, `FILE_MAX_TOKENS`, `PRESENTATION_MAX_TOKENS` | ❌ | Har vazifa uchun chiqish token limiti; runtime prompt va model kontekst sig'imiga qarab limitni yana pasaytiradi |
| `GROQ_CONTEXT_TOKENS`, `OPENROUTER_CONTEXT_TOKENS`, `GEMINI_CONTEXT_TOKENS` | ❌ | Model/provider kontekst oynasi uchun konservativ taxmin; modelga mos qiymat belgilang |
| `REASONING_EFFORT` | ❌ | `low` (standart) / `medium` / `high` — token tejash uchun past |
| `WORKERS` | ❌ | Bir vaqtda ishlov beriladigan xabarlar (standart 8) |

> ⚠️ Kalitlarni **hech qachon** GitHub'ga yoki zip'ga qo'shmang. `.env` `.gitignore` va `.dockerignore` da.

## Lokal ishga tushirish (ixtiyoriy)

```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env     # to'ldiring
python bot.py
```

## Northflank + GitHub orqali deploy

**1. GitHub'ga yuklash**
```bash
git init && git add . && git commit -m "AI bot"
git branch -M main
git remote add origin https://github.com/<login>/<repo>.git
git push -u origin main
```
Repo'ni **private** qiling. `git status` da `.env` ko'rinmasligini tekshiring.

**2. Northflank'da servis yaratish**
1. Project yarating → **Add new service** → **Combined service** (GitHub'dan quradi va ishga tushiradi).
2. **Repository**: GitHub akkauntingizni ulang, repo va `main` branch'ni tanlang.
3. **Build**: *Dockerfile*, yo'l `/Dockerfile`, kontekst `/`. Dockerfile start command qiymati `python bot.py`; Northflank ichida alohida override shart emas.
4. **Networking**: port `8080`, protokol HTTP. `APP_PASSWORD` berilgan bo'lsa web ilova shu portda ochiladi; berilmasa faqat health-check ishlaydi.
5. **Environment variables** (Runtime variables) bo'limiga `TELEGRAM_TOKEN` va kamida bitta AI kalitini kiriting. `BRAVE_API_KEY` `/qidir` uchun, `APP_PASSWORD` web ilova uchun, Cloudflare kalitlari rasm funksiyalari uchun ixtiyoriy. Maxfiylarni *secret* sifatida belgilang.
6. **Resources**: **instances = 1** (muhim!). 0.1–0.2 vCPU va 256 MB odatda yetadi; xotira yetmasa 512 MB qiling.
7. (Ixtiyoriy) Health check: HTTP, port `8080`, yo'l `/healthz`.
8. **Create service**. Loglarda `@botnomi ishga tushdi | ...` qatori chiqsa, tayyor.

**3. Yangilash**: kodni `git push` qilsangiz, Northflank o'zi qayta quradi va deploy qiladi.

## Muammolar

- **`409 Conflict: terminated by other getUpdates request`** — bir token ikki joyda ishlayapti (eski Render/Railway/kompyuter nusxasi yoki ikkinchi instance). Eskisini o'chiring; instances = 1 bo'lsin. Deploy vaqtida bir necha soniya chiqishi normal.
- **«So'rovlar limiti tugadi»** — Groq/Gemini limiti. `GEMINI_API_KEY` ni qo'shing yoki `CHAT_MODEL=llama-3.1-8b-instant` qiling (kunlik limiti kattaroq), `HISTORY_LIMIT` ni kamaytiring.
- **«Rasmni o'qib bo'lmadi» / «model topilmadi»** — `VISION_MODEL` nomini Groq konsolidagi mavjud modellar ro'yxati bilan solishtiring.
- **Bot hamma uchun ochiq** — `ALLOWED_USERS` ni to'ldiring, aks holda boshqalar kvotangizni ishlatadi.
- Xotira (suhbat tarixi) RAM'da: qayta deploy qilinsa tozalanadi. Bu kutilgan holat.


## Suhbat tarixi va saqlash

Suhbat konteksti `state.py` dagi `_history` xotira lug'atida `Telegram user_id` kaliti bilan saqlanadi. Har bir foydalanuvchiga mustaqil tarix beriladi; `get_history()` nusxa qaytaradi. Tarix faqat muvaffaqiyatli javoblardan keyin qo'shiladi, `/clear` esa tarix va vaqtinchalik media kontekstini o'chiradi. `HISTORY_LIMIT` (standart 10, juft savol-javob xabarlari) va `HISTORY_MSG_CHARS` (standart 1500) tarixni cheklaydi. Jarayon qayta ishga tushsa yoki deploy qilinsa, tarix yo'qoladi.

Hujjat matni RAM keshida saqlanadi: `DOC_TTL_MIN` standart 30 daqiqa, `FILE_CACHE` standart 500 foydalanuvchi, matn `FILE_MAX_CHARS` bilan chegaralangan. Rasm va taqdimot keshlari ham RAM da.

`TELEGRAM_TOKEN` hamda `GROQ_API_KEY`, `OPENROUTER_API_KEY` (yoki `_1`/`_2`) yoki `GEMINI_API_KEY` dan kamida bittasi majburiy. `CHAT_PROVIDER` va `FILE_PROVIDER` bo'sh bo'lsa mavjud kalitlarga qarab tanlanadi. API kalitlari loglarga chiqarilmaydi.
