# AHO loyihasini ishga tushirish

Arxiv ichida to'liq dastur kodi, PostgreSQL migratsiyalari, Telegram-bot, veb-panel, testlar va Docker sozlamalari bor. Maxfiy tokenlar, parollar va kompyuterga bog'liq o'rnatilgan kutubxonalar arxivga qo'shilmaydi.

## Desktop'da ochish

1. `AHO-project.zip` faylini kompyuteringizdagi Desktop'ga yuklab oling.
2. Arxivni oching. `AHO-project` papkasida terminal oching.
3. Docker Desktop yoki Docker Engine + Compose o'rnatilgan bo'lishi kerak. Windows'da quyidagi shell buyruqlari uchun Git Bash yoki WSL ishlating.
4. Ishga tushiring:

```bash
./scripts/bootstrap.sh
```

5. Hosil bo'lgan `.env` faylida `TELEGRAM_BOT_TOKEN` va `TELEGRAM_BOT_USERNAME` qiymatlarini o'zingiz kiriting. Tokenni Git'ga yubormang.
6. Servislarni ishga tushiring:

```bash
docker compose up -d --build --wait
```

Veb-panel porti: **5173**. Backend porti: **8000**, Swagger yo'li: **/docs**. Brauzerda mahalliy `localhost:5173` manzilini oching.

Administrator: `admin@example.local`. Parol `.env` dagi `ADMIN_INITIAL_PASSWORD` qiymati. Boshqa demo foydalanuvchilar uchun parol — `DEMO_PASSWORD`.

## Murojaatlarni qabul qiluvchi

Admin paneldagi «АХО / Получатели» bo'limidan xodimni qo'shing, taklif havolasini bering, Telegram ulanganidan keyin aktivlashtiring va kategoriyalarni tanlang.

Aniq Telegram ID oldindan tasdiqlangan bo'lsa, operator quyidagi buyruqdan foydalanishi mumkin:

```bash
docker compose exec backend python -m aho.configure_receiver \
  --telegram-user-id TELEGRAM_USER_ID \
  --first-name ISM --last-name FAMILIYA \
  --username USERNAME --all-categories
```

Katta harflardagi parametrlarni haqiqiy ma'lumotlarga almashtiring. Qabul qiluvchi botga kirib **Start** bosishi shart. Username o'zi yetarli emas. Oldindan tasdiqlangan raqamli ID bilan shaxsiy Start kelgandan keyingina Telegram chat ulanishi saqlanadi.

Agar botda boshqa tizimning webhook'i bo'lsa, dastur uni avtomatik o'chirmaydi. Faqat botni yangi tizimga ko'chirishga qaror qilganingizdan keyin `.env` ichida `TELEGRAM_REPLACE_WEBHOOK=true` qo'ying va bot konteynerini qayta yarating.

## Yangilangan tokenni qo'llash

`.env` dagi `TELEGRAM_BOT_TOKEN`ni almashtirgandan keyin:

```bash
docker compose up -d --force-recreate backend telegram-bot
```

Bir token uchun bir vaqtning o'zida faqat bitta polling bot ishlashi kerak. Bulut va shaxsiy kompyuterda aynan bir botni parallel ishga tushirmang.

## Git va hujjatlar

Git'dagi kod: `935082703sher/git-jouney`, loyiha branch'i: `aho-request-system`.

To'liq ma'lumot: `README.md`, `docs/architecture.md`, `docs/api.md`, `docs/demo.md`, `docs/validation.md`.
