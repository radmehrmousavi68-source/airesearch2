# LocalChat — دستیار محلی با حافظهٔ بلندمدت

LocalChat یک رابط چت فارسی برای مدل‌های محلی Ollama است. بخش طراحی سایت/Django از این نسخه حذف شده و برنامه روی گفت‌وگوی عمومی سریع، درک بهتر فارسی محاوره‌ای و حافظهٔ بلندمدتِ صریحاً تأییدشده تمرکز دارد.

## اجرا در ویندوز

1. Python 3 و Ollama را نصب و اجرا کنید.
2. مدل دلخواه را با `ollama pull <model>` دریافت کنید.
3. فایل ZIP را استخراج و `start.bat` را اجرا کنید.
4. پنجرهٔ سرور را تا پایان کار باز نگه دارید.

اجرای دستی:
```powershell
python .\\local_chat\\server.py
```
سپس `http://127.0.0.1:8000` را باز کنید.

## سرعت پاسخ‌گویی

- درخواست‌های عادی دیگر قبل از ارسال به Ollama، درخواست شبکه‌ای جداگانه برای کشف قابلیت‌ها ایجاد نمی‌کنند.
- تشخیص نیاز آشکار به جست‌وجوی وب با یک بررسی محلی و سبک انجام می‌شود؛ جست‌وجوی دستی هم در اختیار شماست.
- فقط ۱۲ پیام آخر به مدل ارسال می‌شود و متن‌های بسیار بلند محدود می‌شوند تا context بی‌دلیل رشد نکند.
- سقف خروجی پیش‌فرض ۱۰۲۴ توکن است و مدل برای ۱۰ دقیقه در Ollama گرم نگه داشته می‌شود.
- رابط، در صورت نصب بودن، ابتدا مدل‌های سبک‌تر ۷B/۸B و سپس ۱۴B را پیشنهاد می‌دهد. انتخاب مدل همچنان دستی و آزاد است.

سرعت واقعی به اندازه و کوانت مدل، VRAM آزاد، تنظیمات Ollama و بار سیستم بستگی دارد. مدل‌های ۳۰B روی RTX 3060 12GB ممکن است بخش زیادی از محاسبه را روی CPU انجام دهند و کند باشند.

## حافظهٔ بلندمدت

حافظه به‌صورت پیش‌فرض از مکالمات چیزی را خودکار ذخیره نمی‌کند. فقط با درخواست صریح شما موردی ذخیره می‌شود. نمونه‌ها:
- «یادت باشه من فارسی صحبت می‌کنم»
- «این رو ذخیره کن: ترجیح می‌دم جواب‌ها کوتاه باشن»
- «من ویندوز استفاده می‌کنم، اینو یادت باشه»

برای دیدن موارد ذخیره‌شده، دکمهٔ «حافظهٔ بلندمدت من» را در نوار کناری باز کنید یا بپرسید «چه چیزهایی یادت هست؟».

برای حذف:
- «این رو فراموش کن: ترجیح می‌دم جواب‌ها کوتاه باشن»
- «همهٔ حافظه رو پاک کن»

حافظه در SQLite محلی در مسیر `%LOCALAPPDATA%\\LocalChat\\memory.sqlite3` نگهداری می‌شود و بین اجراها و گفت‌وگوهای جدید باقی می‌ماند. هیچ دادهٔ حافظه‌ای برای ذخیره‌سازی به سرویس ابری فرستاده نمی‌شود. حذف برنامه یا پاک‌کردن تاریخچهٔ مرورگر، این فایل حافظه را پاک نمی‌کند؛ برای حذف، از دستور بالا یا مدیریت حافظه استفاده کنید.

## ابزارهای محلی Python با تأیید شما

LocalChat می‌تواند برای کارهای برنامه‌نویسی از ابزارهای محدود محلی استفاده کند:
- ساخت پروژه با پوشه‌های مرتب `src/`، `tests/`، `data/`، `output/` و `logs/`.
- ساخت محیط مجازی مستقل `.venv` برای هر پروژه.
- ایجاد فایل Python جدید در `src/`.
- نصب کتابخانه‌ها در همان محیط مجازی و ثبت آن‌ها در `requirements.txt`.
- اجرای فایل Python با interpreter همان محیط مجازی.

پروژه‌ها در `%LOCALAPPDATA%\LocalChat\projects\` نگهداری می‌شوند. جزئیات هر عملیات باید پیش از اجرا نمایش داده شود و تأیید مستقل شما لازم است. فایل موجود بازنویسی نمی‌شود و هیچ فرمان عمومی سیستم در اختیار مدل قرار نمی‌گیرد.

**محدودیت فعلی:** API آماده‌سازی و تأیید عملیات در سرور تعریف شده است؛ اتصال چرخهٔ tool-calling مدل Ollama و کنترل تأیید در رابط چت هنوز باید تکمیل شود. تا آن زمان، مدل صرفاً از وجود این قابلیت آگاه است و نباید ادعا کند عملیات را اجرا کرده است.

مستند فنی: `capability_docs/system_tools.md`.

## جست‌وجوی وب

جست‌وجوی وب از طریق DuckDuckGo انجام می‌شود. می‌توانید دکمهٔ جست‌وجو را روشن کنید. برخی عبارت‌های آشکارا روزآمد مثل «آخرین خبرها»، «قیمت امروز» یا «آب‌وهوا» نیز جست‌وجوی خودکار را فعال می‌کنند. هنگام جست‌وجو، عبارت و درخواست صفحات عمومی به اینترنت ارسال می‌شوند؛ چت عادی به Ollama محلی فرستاده می‌شود.

## امنیت و حریم خصوصی

سرور فقط روی `127.0.0.1` گوش می‌دهد. حافظه و تأیید هویت در `%LOCALAPPDATA%\\LocalChat` ذخیره می‌شوند. گفت‌وگوهای قابل مشاهده در مرورگر به‌صورت محلی نگهداری می‌شوند.


## Recent chat reliability and controls

- Rejecting an approval stops the active tool-call chain immediately. The model is not allowed to continue the same turn by proposing another operation after a rejection.
- In programming mode, common traceback/exception/error-log messages are routed to a diagnostic-only chat path. The model should explain evidence, likely root cause, and verification steps without calling tools or changing files until the user explicitly requests a fix.
- The paperclip supports text/code, PDF, ZIP, PNG/JPEG/WebP/GIF/BMP, DOCX, XLSX, and PPTX. PDF text is extracted; scanned PDFs are rendered as page images for a vision-capable Ollama model. ZIP files are inspected in memory (no archive paths are written to disk), with path-traversal, encrypted-entry, entry-count, and expanded-size limits. Supported text and office documents inside ZIPs are extracted; nested archives and unsupported binaries are skipped. Each upload is limited to 12 MiB, total selected files to 20 MiB, and up to four images (1 MiB each) are passed to Ollama. PDF/DOCX/XLSX/PPTX readers and Pillow/PyMuPDF are installed automatically by the launcher on first run from `requirements.txt`. When the selected model is text-only, the server automatically routes image requests to an already-installed vision model (for example Gemma 3, Qwen-VL, or LLaVA); if none is installed, it reports the exact requirement and does not download a model. Text-only models are still used for ordinary messages. All extracted content is explicitly marked as untrusted reference data and is never treated as system instructions, identity, or memory.
- The topbar **Stop Ollama** control terminates the Ollama process tree only; the Local Chat server remains running. It is protected by the verified creator session and the same Origin/CSRF checks as other state-changing endpoints.
- GitHub Actions packages tracked repository source into a single ZIP artifact; no archive is embedded in the source tree.
