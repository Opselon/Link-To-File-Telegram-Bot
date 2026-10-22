"""Bilingual copy engine: English + Persian, one catalog, real fallbacks.

The catalog is the only source of user-facing wording. Every entry carries both
languages, values are ``str.format`` templates, and ``t`` never raises: an
unknown key returns the key itself and a failed interpolation returns the raw
template instead of blowing up a handler.

Persian messages are prefixed with an RLM mark so paragraphs on mixed-LTR/RTL
screens (like Telegram, full of Latin filenames and commands) align right.
"""

from __future__ import annotations

RLM_MARK = "\u200f"

LANGUAGES: tuple[str, ...] = ("en", "fa")
DEFAULT_LANGUAGE = "en"

# Language names are shown in their own language on both screens — that is how
# language pickers work — so they live outside the format catalog.
LANGUAGE_NAMES: dict[str, str] = {"en": "English", "fa": "فارسی"}


def normalize(lang_code: str | None) -> str:
    """Map any Telegram-ish language tag onto a catalog language.

    ``fa-IR``/``fa_ANY`` → ``fa`` (``_`` is aliased to ``-`` first), ``en-US`` →
    ``en``, anything unknown or empty → the default.
    """
    if not lang_code:
        return DEFAULT_LANGUAGE
    primary = str(lang_code).strip().lower().replace("_", "-").split("-")[0]
    return primary if primary in LANGUAGES else DEFAULT_LANGUAGE


def is_language(lang: str | None) -> bool:
    return normalize(lang) in LANGUAGES


def rlm(lang: str) -> str:
    """The RTL paragraph mark, for messages the bot renders in Persian."""
    return RLM_MARK if normalize(lang) == "fa" else ""


CATALOG: dict[str, dict[str, str]] = {
    # --- menu / buttons ------------------------------------------------------
    "btn_retry": {"en": "🔄 Try again", "fa": "🔄 دوباره امتحان کن"},
    "btn_home": {"en": "🏠 Menu", "fa": "🏠 منو"},
    "btn_close": {"en": "✖️ Close", "fa": "✖️ بستن"},
    "btn_sites": {"en": "✨ What I can fetch", "fa": "✨ چه چیزهایی می‌گیرم"},
    "btn_stats": {"en": "📊 My stats", "fa": "📊 آمار من"},
    "btn_help": {"en": "❓ How it works", "fa": "❓ نحوهٔ کار"},
    "btn_history": {"en": "🕘 History", "fa": "🕘 تاریخچه"},
    "btn_admin": {"en": "🛠 Admin dashboard", "fa": "🛠 پیشخوان مدیریت"},
    "btn_language": {"en": "🌐 Language", "fa": "🌐 زبان"},
    "btn_releases": {"en": "⭐️ Releases", "fa": "⭐️ انتشارها"},
    "btn_my_usual": {"en": "⚡ My usual · {label}", "fa": "⚡ سلیقهٔ همیشگی · {label}"},
    "btn_reset": {"en": "↺ Reset", "fa": "↺ بازنشانی"},
    "btn_best": {"en": "⚡ Best quality", "fa": "⚡ بهترین کیفیت"},
    "btn_audio": {"en": "🎵 {bitrate} kbps", "fa": "🎵 {bitrate} کیلوبیت بر ثانیه"},
    "btn_cancel_download": {"en": "🛑 Cancel download", "fa": "🛑 لغو دانلود"},
    "btn_again": {"en": "🔁 Download again", "fa": "🔁 دانلود دوباره"},
    "btn_back": {"en": "⬅️ Back", "fa": "⬅️ بازگشت"},
    "btn_change_quality": {"en": "🎚 Change quality", "fa": "🎚 تغییر کیفیت"},
    "btn_quality": {"en": "🎚 Quality", "fa": "🎚 کیفیت"},
    "btn_start_over": {"en": "🔄 Start over", "fa": "🔄 از نو"},
    "btn_supported": {"en": "✨ Supported sites", "fa": "✨ سایت‌های پشتیبانی‌شده"},
    "btn_refresh": {"en": "🔄 Refresh", "fa": "🔄 تازه‌سازی"},
    "btn_join_on": {"en": "🔓 Force join: ON", "fa": "🔓 عضویت اجباری: روشن"},
    "btn_join_off": {"en": "🔒 Force join: OFF", "fa": "🔒 عضویت اجباری: خاموش"},
    "btn_channel": {"en": "📢 Channel: {channel}", "fa": "📢 کانال: {channel}"},
    "btn_broadcast": {"en": "📣 Broadcast", "fa": "📣 پیام همگانی"},
    "btn_clear_cache": {"en": "🧹 Clear join cache", "fa": "🧹 پاک‌کردن حافظهٔ عضویت"},
    "btn_queue": {"en": "📦 Queue", "fa": "📦 صف"},
    "btn_errors": {"en": "⚠️ Errors", "fa": "⚠️ خطاها"},
    "btn_set_channel": {"en": "📢 Set a channel first", "fa": "📢 اول یک کانال تنظیم کن"},
    "btn_join_channel": {"en": "📢 Join channel", "fa": "📢 عضویت در کانال"},
    "btn_i_joined": {"en": "✅ I joined", "fa": "✅ عضو شدم"},
    "btn_send_all": {"en": "📣 Send to everyone", "fa": "📣 ارسال به همه"},
    "btn_abort": {"en": "✖️ Abort", "fa": "✖️ لغو"},
    # --- brand ---------------------------------------------------------------
    "tagline": {
        "en": "Any link in. A file out.",
        "fa": "هر لینکی وارد، یک فایل بیرون.",
    },
    "welcome": {
        "en": (
            "⚡ <b>{brand}</b> — <i>{tagline}</i>\n\n"
            "Hey {name}! Send me a link and I'll hand the file back.\n\n"
            "📦 Up to <b>{cap_mb} MB</b> per file · 🆓 free · ⚡ parallel downloads\n"
            "🎬 Video, 🎵 audio or any direct file link.{admin_tail}"
        ),
        "fa": (
            "⚡ <b>{brand}</b> — <i>{tagline}</i>\n\n"
            "سلام {name}! یک لینک بفرست تا فایلش را تحویلت بدهم.\n\n"
            "📦 تا <b>{cap_mb} مگابایت</b> برای هر فایل · 🆓 رایگان · ⚡ دانلود موازی\n"
            "🎬 ویدیو، 🎵 صدا یا هر لینک مستقیم فایل.{admin_tail}"
        ),
    },
    "welcome_fallback_name": {"en": "there", "fa": "دوست عزیز"},
    "admin_tail": {
        "en": "\n\n🛠 /admin — dashboard",
        "fa": "\n\n🛠 /admin — پیشخوان مدیریت",
    },
    # --- screens -------------------------------------------------------------
    "help": {
        "en": (
            "<b>How {brand} works</b>\n\n"
            "<b>1.</b> Paste any public link.\n"
            "<b>2.</b> Long video: pick a quality. Instagram, TikTok and X have no "
            "quality step — they download the moment you send the link.\n"
            "<b>3.</b> I download it, send the file and delete it from disk.\n\n"
            "<b>Commands</b>\n"
            "• /start — main menu\n"
            "• /help — this guide\n"
            "• /history — your recent downloads\n"
            "• /stats — your numbers\n"
            "• /lang — change the language\n"
            "• /cancel — stop your running downloads\n"
            "• /id — your Telegram ID\n\n"
            "<b>Good to know</b>\n"
            "• Private or DRM-protected media can't be fetched.\n"
            "• Files over {cap_mb} MB (Telegram's cap) are refused up front.\n"
            "• Temporary files are deleted right after sending."
        ),
        "fa": (
            "<b>{brand} چطور کار می‌کند</b>\n\n"
            "<b>۱.</b> هر لینک عمومی را بفرست.\n"
            "<b>۲.</b> ویدیوی بلند: کیفیت را انتخاب کن. اینستاگرام، تیک‌تاک و ایکس "
            "مرحله انتخاب کیفیت ندارند — همان لحظه که لینک را بفرستی دانلود شروع می‌شود.\n"
            "<b>۳.</b> من دانلود می‌کنم، فایل را می‌فرستم و از دیسک پاکش می‌کنم.\n\n"
            "<b>دستورها</b>\n"
            "• /start — منوی اصلی\n"
            "• /help — همین راهنما\n"
            "• /history — دانلودهای اخیرت\n"
            "• /stats — آمار خودت\n"
            "• /lang — تغییر زبان\n"
            "• /cancel — توقف دانلودهای در حال اجرا\n"
            "• /id — شناسه تلگرامت\n\n"
            "<b>چند نکته</b>\n"
            "• رسانه خصوصی یا محافظت‌شده با DRM قابل دریافت نیست.\n"
            "• فایل‌های بزرگ‌تر از {cap_mb} مگابایت (سقف تلگرام) از همان ابتدا رد می‌شوند.\n"
            "• فایل‌های موقت بلافاصله بعد از ارسال پاک می‌شوند."
        ),
    },
    "sites": {
        "en": (
            "<b>✨ What I can fetch</b>\n\n"
            "<b>Media (auto-detected)</b>\n"
            "YouTube · Instagram · TikTok · X/Twitter · Facebook · Vimeo · Reddit · "
            "SoundCloud · Twitch · Pinterest · Dailymotion · Bilibili · VK · Rumble · "
            "Odysee · Streamable\n\n"
            "<b>Direct files</b>\n"
            "Any public http(s) link, including signed CDN links.\n\n"
            "<blockquote>If a page is supported by yt-dlp it usually just works. "
            "Not sure? Paste the link and see.</blockquote>"
        ),
        "fa": (
            "<b>✨ چه چیزهایی می‌توانم بگیرم</b>\n\n"
            "<b>رسانه (خودکار تشخیص داده می‌شود)</b>\n"
            "YouTube · Instagram · TikTok · X/Twitter · Facebook · Vimeo · Reddit · "
            "SoundCloud · Twitch · Pinterest · Dailymotion · Bilibili · VK · Rumble · "
            "Odysee · Streamable\n\n"
            "<b>فایل‌های مستقیم</b>\n"
            "هر لینک عمومی http(s)، از جمله لینک‌های امضاشدهٔ CDN.\n\n"
            "<blockquote>اگر yt-dlp از صفحه‌ای پشتیبانی کند معمولاً بی‌دردسر کار می‌کند. "
            "مطمئن نیستی؟ لینک را بفرست و ببین.</blockquote>"
        ),
    },
    "stats_title": {"en": "📊 Your stats", "fa": "📊 آمار تو"},
    "stats": {
        "en": (
            "<b>{title}</b>\n\n"
            "📥 Downloads: <b>{total}</b>\n"
            "✅ Completed: <b>{completed}</b>\n"
            "⚠️ Failed: <b>{failed}</b>\n"
            "💾 Delivered: <b>{bytes}</b>\n"
            "🎯 Success rate: <b>{rate}</b>"
        ),
        "fa": (
            "<b>{title}</b>\n\n"
            "📥 دانلودها: <b>{total}</b>\n"
            "✅ تکمیل‌شده: <b>{completed}</b>\n"
            "⚠️ ناموفق: <b>{failed}</b>\n"
            "💾 تحویل‌شده: <b>{bytes}</b>\n"
            "🎯 نرخ موفقیت: <b>{rate}</b>"
        ),
    },
    # --- admin ---------------------------------------------------------------
    "admin": {
        "en": (
            "<b>🛠 Admin dashboard</b>\n\n"
            "👥 Users: <b>{users}</b>\n"
            "📥 Downloads: <b>{total}</b> (today: {today})\n"
            "✅ Completed: <b>{completed}</b> · ⚠️ Failed: <b>{failed}</b>\n"
            "💾 Delivered: <b>{bytes}</b>\n\n"
            "⚙️ Workers: <b>{active}</b> busy · <b>{pending}</b> waiting\n"
            "📦 In flight: <b>{queued}</b> queued · <b>{downloading}</b> downloading · "
            "<b>{uploading}</b> uploading\n"
            "🔁 Resumed this boot: <b>{resumed}</b> · ⚠️ Errors today: <b>{errors}</b>\n"
            "🗄 Disk free: <b>{disk}</b> · temp: <b>{temp}</b>\n"
            "{memory_line}"
            "🔒 Force join: <b>{force_join}</b> · 📢 {channel}\n"
            "🏷 Version: <b>{version}</b>"
        ),
        "fa": (
            "<b>🛠 پیشخوان مدیریت</b>\n\n"
            "👥 کاربران: <b>{users}</b>\n"
            "📥 دانلودها: <b>{total}</b> (امروز: {today})\n"
            "✅ تکمیل‌شده: <b>{completed}</b> · ⚠️ ناموفق: <b>{failed}</b>\n"
            "💾 تحویل‌شده: <b>{bytes}</b>\n\n"
            "⚙️ کارگرها: <b>{active}</b> مشغول · <b>{pending}</b> در انتظار\n"
            "📦 در جریان: <b>{queued}</b> در صف · <b>{downloading}</b> در حال دانلود · "
            "<b>{uploading}</b> در حال بارگذاری\n"
            "🔁 ادامه‌یافته در این راه‌اندازی: <b>{resumed}</b> · ⚠️ خطاهای امروز: <b>{errors}</b>\n"
            "🗄 فضای آزاد دیسک: <b>{disk}</b> · موقت: <b>{temp}</b>\n"
            "{memory_line}"
            "🔒 عضویت اجباری: <b>{force_join}</b> · 📢 {channel}\n"
            "🏷 نسخه: <b>{version}</b>"
        ),
    },
    "admin_memory": {
        "en": "🧠 RAM: <b>{memory_mb} MB</b> · ⏱ up: <b>{uptime}</b>\n",
        "fa": "🧠 RAM: <b>{memory_mb} MB</b> · ⏱ روشن: <b>{uptime}</b>\n",
    },
    "force_state_on": {"en": "🟢 on", "fa": "🟢 روشن"},
    "force_state_off": {"en": "⚪️ off", "fa": "⚪️ خاموش"},
    "channel_not_set": {"en": "not set", "fa": "تنظیم نشده"},
    "queue_empty": {
        "en": "📦 <b>Queue</b>\n\nNothing is queued or running right now.",
        "fa": "📦 <b>صف</b>\n\nالان هیچ کاری در صف یا در حال اجرا نیست.",
    },
    "queue_title": {"en": "📦 <b>Queue</b>\n", "fa": "📦 <b>صف</b>\n"},
    "queue_more": {"en": "…and {n} more", "fa": "…و {n} مورد دیگر"},
    "queue_footer": {
        "en": "\n<i>status comes from the live queue when the job is in memory.</i>",
        "fa": "\n<i>وضعیت وقتی کار در حافظه باشد از صف زنده خوانده می‌شود.</i>",
    },
    "errors_empty": {
        "en": "✅ <b>Errors</b>\n\nNo failed tickets recorded.",
        "fa": "✅ <b>خطاها</b>\n\nهیچ تیکت ناموفقی ثبت نشده.",
    },
    "errors_title": {"en": "⚠️ <b>Recent failures</b>\n", "fa": "⚠️ <b>خطاهای اخیر</b>\n"},
    "errors_no_reason": {"en": "no reason recorded", "fa": "دلیلی ثبت نشده"},
    "errors_unknown_kind": {"en": "unknown", "fa": "نامشخص"},
    # --- link / queue lifecycle ----------------------------------------------
    "duplicate": {
        "en": (
            "⏳ <b>Already on it</b>\n\n"
            "That exact link is already queued as <code>#{ticket}</code>. "
            "I won't start it twice — cancel the running one first if you want a fresh copy."
        ),
        "fa": (
            "⏳ <b>در دست انجام است</b>\n\n"
            "همین لینک قبلاً با <code>#{ticket}</code> در صف قرار گرفته. "
            "دو بار شروعش نمی‌کنم — اگر نسخهٔ تازه می‌خواهی، اول نسخهٔ در حال اجرا را لغو کن."
        ),
    },
    "join": {
        "en": (
            "🔒 <b>One quick step</b>\n\n"
            "Join {channel} to use the bot, then tap <b>I joined</b>."
        ),
        "fa": (
            "🔒 <b>فقط یک قدم کوتاه</b>\n\n"
            "برای استفاده از ربات در {channel} عضو شو، بعد روی <b>عضو شدم</b> بزن."
        ),
    },
    "join_unavailable": {
        "en": (
            "🔒 <b>Join required</b>\n\n"
            "The channel isn't reachable right now. Please try again in a minute."
        ),
        "fa": (
            "🔒 <b>عضویت لازم است</b>\n\n"
            "الان نمی‌توانم کانال را ببینم. لطفاً یک دقیقه بعد دوباره امتحان کن."
        ),
    },
    "rate_limited": {
        "en": "⏳ Slow down a touch — try again in <b>{seconds}s</b>.",
        "fa": "⏳ کمی آرام‌تر — <b>{seconds} ثانیه</b> دیگر دوباره امتحان کن.",
    },
    "queue_full": {
        "en": "🚦 The queue is full right now. Try again in a moment.",
        "fa": "🚦 صف الان پر است. یک لحظه بعد دوباره امتحان کن.",
    },
    "too_many_jobs": {
        "en": "⏳ You already have downloads in flight. Finish or cancel one first.",
        "fa": "⏳ دانلود در حال اجرا داری. اول یکی را تمام یا لغو کن.",
    },
    "blocked_url": {
        "en": "🚫 That link points to a private or local address, so I can't fetch it.",
        "fa": "🚫 این لینک به نشانی خصوصی یا محلی اشاره می‌کند، پس نمی‌توانم بگیرمش.",
    },
    "cancelled": {
        "en": "🛑 <b>Cancelled.</b> Nothing is left running.",
        "fa": "🛑 <b>لغو شد.</b> چیزی در حال اجرا نمانده.",
    },
    "missing_download": {
        "en": "⌛️ That download is gone. Send the link again.",
        "fa": "⌛️ آن دانلود دیگر در دسترس نیست. لینک را دوباره بفرست.",
    },
    "not_a_link": {
        "en": "🤔 That doesn't look like a link. Try again?",
        "fa": "🤔 این که لینک نیست. دوباره امتحان می‌کنی؟",
    },
    "nothing_running": {
        "en": "Nothing is running right now. 🌤",
        "fa": "الان چیزی در حال اجرا نیست. 🌤",
    },
    "your_id": {"en": "🆔 <code>{id}</code>", "fa": "🆔 شناسهٔ تلگرام تو: <code>{id}</code>"},
    "version": {"en": "⚡ <b>{brand}</b> v{version}", "fa": "⚡ <b>{brand}</b> نسخهٔ {version}"},
    # --- download card -------------------------------------------------------
    "header_fetching": {"en": "🔗 <b>Fetching your file</b>", "fa": "🔗 <b>فایلت را می‌گیرم</b>"},
    "header_audio": {"en": "🎵 <b>Audio · {label}</b>", "fa": "🎵 <b>صدا · {label}</b>"},
    "header_video": {"en": "🎬 <b>Video · {label}</b>", "fa": "🎬 <b>ویدیو · {label}</b>"},
    "queued": {
        "en": "{header}\n\n⏳ <b>Queued</b>",
        "fa": "{header}\n\n⏳ <b>در صف</b>",
    },
    "queued_position": {
        "en": "{header}\n\n⏳ <b>Queued</b> · {position} ahead of you",
        "fa": "{header}\n\n⏳ <b>در صف</b> · {position} نفر جلوی تو",
    },
    "stage_downloading": {"en": "⬇️ Downloading", "fa": "⬇️ در حال دانلود"},
    "stage_uploading": {"en": "⬆️ Uploading", "fa": "⬆️ در حال بارگذاری"},
    "uploading": {
        "en": "⬆️ <b>Uploading</b> · {size}",
        "fa": "⬆️ <b>در حال بارگذاری</b> · {size}",
    },
    "instant_header": {
        "en": "⚡ <b>{site}</b> · auto quality",
        "fa": "⚡ <b>{site}</b> · کیفیت خودکار",
    },
    "instant_note": {
        "en": (
            "{site} gives one file per post, so there is no quality to pick — "
            "I'm fetching the best version that fits the size cap."
        ),
        "fa": (
            "{site} برای هر پست فقط یک فایل می‌دهد، پس کیفیتی برای انتخاب نیست — "
            "بهترین نسخه‌ای که در سقف حجم جا شود را می‌گیرم."
        ),
    },
    "done_title": {"en": "✅ <b>{title}</b>", "fa": "✅ <b>{title}</b> ارسال شد"},
    "done_size": {"en": "📦 {size}", "fa": "📦 حجم {size}"},
    "done_quality": {"en": "🎞 {height}p", "fa": "🎞 کیفیت {height}p"},
    "done_duration": {"en": "⏱ {duration}", "fa": "⏱ مدت {duration}"},
    "done_source": {"en": "🔗 <i>{source}</i>", "fa": "🔗 منبع <i>{source}</i>"},
    "failed_card": {
        "en": "❌ <b>Couldn't finish that</b>\n\n{error}",
        "fa": "❌ <b>نشد این را تمام کنم</b>\n\n{error}",
    },
    # --- quality picker ------------------------------------------------------
    "pick_quality": {"en": "🎚 <b>Pick a quality</b>", "fa": "🎚 <b>یک کیفیت انتخاب کن</b>"},
    "pick_quality_other": {
        "en": "🎚 <b>Pick another quality</b>",
        "fa": "🎚 <b>کیفیت دیگری انتخاب کن</b>",
    },
    "preview_uploader": {"en": "👤 {name}", "fa": "👤 از {name}"},
    "preview_duration": {"en": "⏱ {duration}", "fa": "⏱ مدت {duration}"},
    "preview_views": {"en": "👁 {views} views", "fa": "👁 {views} بازدید"},
    "this_link": {"en": "this link", "fa": "این لینک"},
    # --- history -------------------------------------------------------------
    "history_empty": {
        "en": (
            "🕘 <b>History</b>\n\n"
            "Nothing here yet — send me a link and it will show up in this list."
        ),
        "fa": (
            "🕘 <b>تاریخچه</b>\n\n"
            "هنوز چیزی اینجا نیست — یک لینک بفرست تا در این فهرست بیاید."
        ),
    },
    "history_title": {
        "en": "🕘 <b>Your last downloads</b>",
        "fa": "🕘 <b>آخرین دانلودهای تو</b>",
    },
    "history_retry": {
        "en": "Tap a title below to fetch it again 🔁",
        "fa": "برای گرفتن دوباره، روی یکی از عنوان‌های پایین بزن 🔁",
    },
    # --- broadcast -----------------------------------------------------------
    "broadcast_prompt": {
        "en": (
            "📣 <b>Broadcast</b>\n\n"
            "Send the message every user should receive. HTML is allowed, and you "
            "get a preview plus a confirmation before anything is sent."
        ),
        "fa": (
            "📣 <b>پیام همگانی</b>\n\n"
            "پیامی را بفرست که همهٔ کاربران باید دریافت کنند. HTML مجاز است و پیش از "
            "ارسال، پیش‌نمایش و تأیید می‌گیری."
        ),
    },
    "broadcast_confirm": {
        "en": (
            "📣 <b>Ready to send</b>\n\n"
            "The message above goes to <b>{users}</b> user(s).\n\n"
            "Sending runs in the background — you'll get a summary here when it finishes."
        ),
        "fa": (
            "📣 <b>آمادهٔ ارسال</b>\n\n"
            "پیام بالا برای <b>{users}</b> کاربر می‌رود.\n\n"
            "ارسال در پس‌زمینه انجام می‌شود — وقتی تمام شد خلاصه‌اش را همین‌جا می‌گیری."
        ),
    },
    "broadcast_summary": {
        "en": (
            "📣 <b>Broadcast finished</b>\n\n"
            "✅ Delivered: <b>{sent}</b>\n"
            "⚠️ Skipped/failed: <b>{failed}</b>\n"
            "👥 Recipients: <b>{total}</b>"
        ),
        "fa": (
            "📣 <b>ارسال همگانی تمام شد</b>\n\n"
            "✅ تحویل‌شده: <b>{sent}</b>\n"
            "⚠️ رد شده/ناموفق: <b>{failed}</b>\n"
            "👥 گیرندگان: <b>{total}</b>"
        ),
    },
    "resumed_failed": {
        "en": (
            "❌ <b>A resumed download failed</b>\n\n"
            "<code>{error}</code>\n\n"
            "Tap /history and retry the link if you still need it."
        ),
        "fa": (
            "❌ <b>یک دانلود ادامه‌یافته شکست خورد</b>\n\n"
            "<code>{error}</code>\n\n"
            "اگر هنوز لازمش داری، /history را بزن و لینک را دوباره امتحان کن."
        ),
    },
    # --- admin flow prompts --------------------------------------------------
    "channel_prompt": {
        "en": (
            "📢 <b>Send the channel</b>\n\n"
            "Use <code>@username</code> or a <code>-100…</code> id. The bot must be an "
            "admin there so it can verify members."
        ),
        "fa": (
            "📢 <b>کانال را بفرست</b>\n\n"
            "از <code>@username</code> یا شناسهٔ <code>-100…</code> استفاده کن. ربات باید "
            "در آن کانال مدیر باشد تا بتواند عضویت افراد را بررسی کند."
        ),
    },
    "channel_bad": {
        "en": (
            "❌ I couldn't read that channel. Make sure the bot is an admin there, "
            "then send it again."
        ),
        "fa": (
            "❌ نتوانستم آن کانال را بخوانم. مطمئن شو ربات در آنجا مدیر است، "
            "بعد دوباره بفرستش."
        ),
    },
    "channel_saved": {"en": "✅ Channel saved: <b>{channel}</b>", "fa": "✅ کانال ذخیره شد: <b>{channel}</b>"},
    "channel_saved_off": {
        "en": "\nForce join is still off — toggle it when ready.",
        "fa": "\nعضویت اجباری هنوز خاموش است — هر وقت آماده بودی روشنش کن.",
    },
    "broadcast_too_long": {
        "en": "⚠️ Broadcast messages must be 1–4000 characters.\nSend the message again.",
        "fa": "⚠️ پیام همگانی باید بین ۱ تا ۴۰۰۰ نویسه باشد.\nپیام را دوباره بفرست.",
    },
    "broadcast_bad_html": {
        "en": "⚠️ That message has invalid HTML:\n{error}",
        "fa": "⚠️ HTML این پیام معتبر نیست:\n{error}",
    },
    # --- toasts (callback answers) -------------------------------------------
    "join_first": {"en": "Join the channel first ⚠️", "fa": "اول در کانال عضو شو ⚠️"},
    "bad_selection": {"en": "Bad selection.", "fa": "انتخاب نامعتبر."},
    "pref_cleared": {"en": "Preference cleared ↺", "fa": "پیش‌فرض پاک شد ↺"},
    "added_to_queue": {"en": "Added to the queue ⚡", "fa": "به صف اضافه شد ⚡"},
    "auto_quality_again": {
        "en": "⚡ Auto quality — fetching again",
        "fa": "⚡ کیفیت خودکار — دوباره می‌گیرم",
    },
    "stopped_toast": {"en": "🛑 Cancelled", "fa": "🛑 لغو شد"},
    "nothing_to_stop": {"en": "Nothing left to stop", "fa": "چیزی برای توقف نمانده"},
    "welcome_aboard": {"en": "Welcome aboard ⚡", "fa": "خوش آمدی ⚡"},
    "not_yet": {"en": "Not yet — join the channel first.", "fa": "هنوز نه — اول در کانال عضو شو."},
    "admin_only": {"en": "🚫 Admins only.", "fa": "🚫 فقط برای مدیر."},
    "admin_only_toast": {"en": "Admins only.", "fa": "فقط برای مدیر."},
    "gate_no_channel": {
        "en": "Gate armed, but no channel set — add one next.",
        "fa": "قفل فعال شد، اما کانالی تنظیم نشده — بعدش کانال را اضافه کن.",
    },
    "force_join_on_toast": {
        "en": "Force join is now on ✅",
        "fa": "عضویت اجباری حالا روشن است ✅",
    },
    "force_join_off_toast": {
        "en": "Force join is now off",
        "fa": "عضویت اجباری حالا خاموش است",
    },
    "cache_cleared_toast": {
        "en": "Membership cache cleared 🧹",
        "fa": "حافظهٔ عضویت پاک شد 🧹",
    },
    "broadcast_aborted_toast": {
        "en": "Broadcast aborted",
        "fa": "ارسال همگانی لغو شد",
    },
    "broadcast_started_toast": {
        "en": "Broadcast started 📣",
        "fa": "ارسال همگانی شروع شد 📣",
    },
    "no_draft_toast": {
        "en": "Nothing to send — the draft expired. Try again.",
        "fa": "چیزی برای ارسال نیست — پیش‌نویس منقضی شده. دوباره امتحان کن.",
    },
    # --- language picker -----------------------------------------------------
    "language": {
        "en": (
            "🌐 <b>Language</b>\n\n"
            "Pick the language the bot talks to you in. I'll answer in it from now on."
        ),
        "fa": (
            "🌐 <b>زبان</b>\n\n"
            "زبانی را انتخاب کن که ربات با تو حرف بزند. از این به بعد به همان زبان جواب می‌دهم."
        ),
    },
    "language_saved": {"en": "Language updated ✅", "fa": "زبان به‌روز شد ✅"},
}

def t(key: str, lang: str = DEFAULT_LANGUAGE, /, **kwargs: object) -> str:
    """Render ``key`` in ``lang`` (English fallback), never raising.

    Unknown keys return the key string itself, a missing language falls back to
    English, and a broken ``{placeholder}`` interpolation returns the raw
    template — the bot always answers, even when the catalog is inconsistent.
    """
    entry = CATALOG.get(key)
    if entry is None:
        return str(key)
    lang_key = normalize(lang)
    template = entry.get(lang_key) or entry.get(DEFAULT_LANGUAGE) or key
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError, TypeError):
        return template


__all__ = [
    "CATALOG",
    "DEFAULT_LANGUAGE",
    "LANGUAGE_NAMES",
    "LANGUAGES",
    "is_language",
    "normalize",
    "rlm",
    "t",
]
