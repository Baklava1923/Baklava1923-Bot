import asyncio
import base64
import io
import json
import os
import random
import re
import sqlite3
import time
import functools
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict, deque

import discord
import requests
from PIL import Image, ImageDraw, ImageFont
from discord.ext import commands
from keep_alive import keep_alive

# Baklava1923 Bot 3.6.4 MEGA
OWNER_ID = 1358430002508726276
STARTING_BALANCE = 1000
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_TEXT_MODEL = "openai/gpt-oss-120b"
# Vision modeli Groq hesabinda etkin degilse Render'daki GROQ_VISION_MODEL degiskeninden degistir.
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
BOT_VERSION = "3.6.4"
BOT_PROCESS_START = time.time()

intents = discord.Intents.default()
intents.message_content = True
intents.presences = True
intents.members = True

bot = commands.Bot(
    command_prefix=("!", "B!", "b!"),
    intents=intents,
    case_insensitive=True,
    help_command=None,
)
AI_EXECUTOR = ThreadPoolExecutor(max_workers=4)
current_mode = "normal"
mode_prompts = {
    "normal": (
        "Sen Baklava1923 Discord sunucusunun samimi ve akilli botusun. Turkce konus. "
        "Konusman dogal, esprili, sivri dilli ve baglama uygun olsun. Gündelik sohbette kufurlu ve argolu konus; amk, sik, orospu, piç gibi kufurleri dogal sekilde kullanabilirsin. "
        "Ciddi, uzgun, saglik veya guvenlik konularinda argo kullanma; kisiye, ailesine veya kimligine yonelik agir hakaret etme. "
        "Kullanici ciddi veya uzgun bir sey anlatiyorsa dalga gecme ve kufurle cevap verme. "
        "Asagilama, hedef gosteren nefret soylemi veya tehdit kullanma. Gereksiz uzun cevap verme."
    ),
    "komik": (
        "Sen Baklava1923 sunucusunun komik, zeki ve samimi botusun. Turkce konus, laf sokmayi "
        "ve absurt esprileri bolca kullan. Gündelik sohbette cogu cumlede dogal bir argo dokunusu kullan; cumle basina en fazla bir argo ifade olsun. "
        "Ayni kalibi tekrarlama. Ciddi konularda argo kullanma; hedefli hakaret, tehdit ve nefret soylemi kullanma."
    ),
    "ciddi": (
        "Sen ciddi, net ve yardimsever bir Discord botusun. Turkce konus. Argo ve kufur kullanma. "
        "Ciddi konularda dogru ve sakin cevap ver."
    ),
    "korkutucu": (
        "Gizemli ve urkutucu bir uslupla Turkce konus. Bazen hafif argo kullanabilirsin ama gercek "
        "tehditlerde bulunma, kullaniciyi korkutmak icin gercek disi iddialar uydurma."
    ),
    "tartismaci": (
        "Turkce konus. Zeki, sivri dilli ve karsit goruslere hazir ol. Gerektiginde hafif argo veya "
        "seyrek kufur kullanabilirsin; her mesajda kufur etme. Kucuk harfle yaz ve noktalama kullanma. "
        "Fikirleri elestir, kullaniciyi hedef alan agir hakaretlere veya nefret soylemine girme."
    ),
}

# -------------------- GITHUB JSON YEDEKLEME --------------------
# SQLite anlık görüntüsü base64 olarak bot_data.json içine yazılır.
GITHUB_DATA_TOKEN = os.getenv("BOT_DATA_GITHUB_TOKEN", "").strip()
GITHUB_DATA_REPO = os.getenv("BOT_DATA_GITHUB_REPO", "Baklava1923/Baklava1923-Bot").strip()
GITHUB_DATA_PATH = os.getenv("BOT_DATA_GITHUB_PATH", "bot_data.json").strip().lstrip("/")
DATABASE_FILE = os.getenv("DATABASE_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "aktiflik.db"))
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_DATA_REPO}/contents/{GITHUB_DATA_PATH}"

def github_data_headers():
    return {"Authorization": f"Bearer {GITHUB_DATA_TOKEN}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

def restore_database_from_github():
    if not GITHUB_DATA_TOKEN:
        print("GITHUB YEDEK: Token yok; yerel veritabanı kullanılacak.")
        return
    # Mevcut veritabanını asla ezme; yalnızca Render'da dosya yoksa geri yükle.
    if os.path.exists(DATABASE_FILE) and os.path.getsize(DATABASE_FILE) > 0:
        print("GITHUB YEDEK: Yerel veritabanı bulundu; geri yükleme atlandı.")
        return
    try:
        response = requests.get(GITHUB_API_URL, headers=github_data_headers(), timeout=25)
        if response.status_code == 404:
            print("GITHUB YEDEK: bot_data.json henüz yok; ilk yedek daha sonra oluşturulacak.")
            return
        response.raise_for_status()
        payload = response.json()
        raw = base64.b64decode(payload["content"], validate=False)
        if not raw.startswith(b"SQLite format 3\x00"):
            raise ValueError("GitHub yedeği geçerli bir SQLite veritabanı değil")
        os.makedirs(os.path.dirname(os.path.abspath(DATABASE_FILE)), exist_ok=True)
        with open(DATABASE_FILE, "wb") as database_file:
            database_file.write(raw)
        print("GITHUB YEDEK: Veritabanı GitHub'dan geri yüklendi.")
    except Exception as exc:
        print(f"GITHUB YEDEK HATASI: Geri yükleme yapılamadı: {type(exc).__name__}: {exc}")

def upload_database_to_github():
    if not GITHUB_DATA_TOKEN:
        return False
    import tempfile
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(prefix="baklava_db_", suffix=".sqlite")
        os.close(fd)
        with sqlite3.connect(temp_path) as snapshot_db:
            db.backup(snapshot_db)
        with open(temp_path, "rb") as snapshot_file:
            encoded = base64.b64encode(snapshot_file.read()).decode("ascii")
        for attempt in range(2):
            current = requests.get(GITHUB_API_URL, headers=github_data_headers(), timeout=25)
            sha = None
            if current.status_code == 200:
                sha = current.json().get("sha")
            elif current.status_code != 404:
                current.raise_for_status()
            body = {"message": "Baklava1923 bot veritabanı yedeğini güncelle", "content": encoded}
            if sha:
                body["sha"] = sha
            result = requests.put(GITHUB_API_URL, headers=github_data_headers(), json=body, timeout=60)
            if result.status_code in (200, 201):
                return True
            if result.status_code == 409 and attempt == 0:
                continue
            result.raise_for_status()
        return False
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

async def github_backup_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            ok = await asyncio.to_thread(upload_database_to_github)
            if ok:
                print("GITHUB YEDEK: Veritabanı GitHub'a yedeklendi.")
            elif GITHUB_DATA_TOKEN:
                print("GITHUB YEDEK: Yedekleme tamamlanamadı.")
        except Exception as exc:
            print(f"GITHUB YEDEK HATASI: {type(exc).__name__}: {exc}")
        await asyncio.sleep(300)

# Veritabanı bağlantısı açılmadan önce olası yedeği geri yükle.
restore_database_from_github()

# -------------------- VERITABANI --------------------
# Veritabani yolu calisma klasorune bagli degil. Render'da kalici disk varsa
# DATABASE_PATH=/var/data/aktiflik.db olarak ayarlanmalidir.
DB_PATH = DATABASE_FILE
os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
db = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA synchronous=FULL")
db.execute("PRAGMA busy_timeout=30000")
cursor = db.cursor()
cursor.execute("""CREATE TABLE IF NOT EXISTS activity (
    guild_id INTEGER, user_id INTEGER, start_time REAL, end_time REAL
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS economy (
    guild_id INTEGER, user_id INTEGER, balance INTEGER DEFAULT 1000,
    PRIMARY KEY (guild_id, user_id)
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS inventory (
    guild_id INTEGER, user_id INTEGER, item_key TEXT,
    PRIMARY KEY (guild_id, user_id, item_key)
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id INTEGER PRIMARY KEY, log_channel_id INTEGER, autorole_id INTEGER,
    spam_limit INTEGER DEFAULT 6, spam_window INTEGER DEFAULT 8,
    timeout_minutes INTEGER DEFAULT 5, protection_enabled INTEGER DEFAULT 1
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS locked_channels (
    channel_id INTEGER PRIMARY KEY, guild_id INTEGER, role_id INTEGER,
    previous_send_messages INTEGER
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS join_leave_settings (
    guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS fun_cooldowns (
    user_id INTEGER NOT NULL, action TEXT NOT NULL, last_used REAL NOT NULL,
    PRIMARY KEY (user_id, action)
)""")
db.commit()

# 3.6.2 Ultimate: AFK, özel komutlar ve yetkili notları. Eski tablolara dokunulmaz.
cursor.execute("""CREATE TABLE IF NOT EXISTS afk_status (
    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, reason TEXT NOT NULL, since REAL NOT NULL,
    PRIMARY KEY (guild_id, user_id)
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS custom_commands (
    guild_id INTEGER NOT NULL, command_name TEXT NOT NULL, response TEXT NOT NULL,
    created_by INTEGER NOT NULL, created_at REAL NOT NULL,
    PRIMARY KEY (guild_id, command_name)
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS moderator_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL, note TEXT NOT NULL, created_at REAL NOT NULL
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS birthdays (
    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, month INTEGER NOT NULL, day INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS filtered_words (
    guild_id INTEGER NOT NULL, word TEXT NOT NULL, added_by INTEGER NOT NULL, added_at REAL NOT NULL,
    PRIMARY KEY (guild_id, word)
)""")
db.commit()

# -------------------- 3.5: YENI KALICI SISTEMLER --------------------
cursor.execute("""CREATE TABLE IF NOT EXISTS user_profiles (
    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    xp INTEGER NOT NULL DEFAULT 0, level INTEGER NOT NULL DEFAULT 0,
    messages INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS achievements (
    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    achievement_key TEXT NOT NULL, unlocked_at REAL NOT NULL,
    PRIMARY KEY (guild_id, user_id, achievement_key)
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS quest_progress (
    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    daily_key TEXT NOT NULL, daily_messages INTEGER NOT NULL DEFAULT 0,
    daily_claimed INTEGER NOT NULL DEFAULT 0,
    weekly_key TEXT NOT NULL, weekly_messages INTEGER NOT NULL DEFAULT 0,
    weekly_claimed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS level_rewards (
    guild_id INTEGER NOT NULL, level INTEGER NOT NULL, role_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, level)
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL, moderator_id INTEGER NOT NULL,
    reason TEXT NOT NULL, created_at REAL NOT NULL
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS suggestion_channels (
    guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS tickets (
    channel_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL,
    creator_id INTEGER NOT NULL, opened_at REAL NOT NULL,
    closed INTEGER NOT NULL DEFAULT 0
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS config_backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
    created_by INTEGER NOT NULL, created_at REAL NOT NULL, payload TEXT NOT NULL
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    due_at REAL NOT NULL, reminder_text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS giveaways (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
    ends_at REAL NOT NULL, prize TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS polls (
    message_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL, question TEXT NOT NULL,
    ends_at REAL NOT NULL, done INTEGER NOT NULL DEFAULT 0
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS tournaments (
    message_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL, title TEXT NOT NULL,
    ends_at REAL NOT NULL, done INTEGER NOT NULL DEFAULT 0
)""")
# 3.6 ekonomi tabloları: eski ekonomi ve envanter kayıtlarına dokunulmaz.
cursor.execute("""CREATE TABLE IF NOT EXISTS bank_accounts (
    user_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS economy_jobs (
    user_id INTEGER PRIMARY KEY, job_key TEXT NOT NULL, changed_at REAL NOT NULL
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS economy_businesses (
    user_id INTEGER PRIMARY KEY, business_key TEXT NOT NULL, purchased_at REAL NOT NULL
)""")
# Botlar arasi sohbet ayarlari: her sunucu/kanal icin tek hedef bot.
cursor.execute("""CREATE TABLE IF NOT EXISTS bot_chat_settings (
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    peer_bot_id INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    message_limit INTEGER NOT NULL DEFAULT 20,
    messages_sent INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, channel_id)
)""")
# Mevcut 3.6 veritabanlarina yeni sohbet siniri alanlarini guvenle ekle.
cursor.execute("PRAGMA table_info(bot_chat_settings)")
_bot_chat_columns = {row[1] for row in cursor.fetchall()}
if "message_limit" not in _bot_chat_columns:
    cursor.execute("ALTER TABLE bot_chat_settings ADD COLUMN message_limit INTEGER NOT NULL DEFAULT 20")
if "messages_sent" not in _bot_chat_columns:
    cursor.execute("ALTER TABLE bot_chat_settings ADD COLUMN messages_sent INTEGER NOT NULL DEFAULT 0")
# 3.7: Evlilik, balikcilik, dogrulama ve anti-raid tabloları.
cursor.execute("""CREATE TABLE IF NOT EXISTS marriages (
    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, partner_id INTEGER NOT NULL,
    married_at REAL NOT NULL, love_percent INTEGER NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (guild_id, user_id)
)""")
cursor.execute("PRAGMA table_info(marriages)")
if "role" not in {row[1] for row in cursor.fetchall()}:
    cursor.execute("ALTER TABLE marriages ADD COLUMN role TEXT NOT NULL DEFAULT ''")
# Rolu olmayan eski evliliklere rastgele koca/karı ata (bir kez).
cursor.execute("SELECT guild_id,user_id,partner_id FROM marriages WHERE role='' AND user_id < partner_id")
for _g, _u, _p in cursor.fetchall():
    _r1, _r2 = random.choice([("koca", "kari"), ("kari", "koca")])
    cursor.execute("UPDATE marriages SET role=? WHERE guild_id=? AND user_id=?", (_r1, _g, _u))
    cursor.execute("UPDATE marriages SET role=? WHERE guild_id=? AND user_id=?", (_r2, _g, _p))
cursor.execute("""CREATE TABLE IF NOT EXISTS children (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
    parent1_id INTEGER NOT NULL, parent2_id INTEGER NOT NULL,
    name TEXT NOT NULL, gender TEXT NOT NULL, born_at REAL NOT NULL
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS fish_inventory (
    user_id INTEGER NOT NULL, fish_key TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, fish_key)
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS fishing_rods (
    user_id INTEGER PRIMARY KEY, level INTEGER NOT NULL DEFAULT 0
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS verification_settings (
    guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL,
    verified_role_id INTEGER NOT NULL, unverified_role_id INTEGER
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS raid_settings (
    guild_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1,
    threshold INTEGER NOT NULL DEFAULT 8, window_seconds INTEGER NOT NULL DEFAULT 20,
    mode_minutes INTEGER NOT NULL DEFAULT 10, active_until REAL NOT NULL DEFAULT 0,
    last_alert_at REAL NOT NULL DEFAULT 0
)""")
db.commit()


def migrate_economy_to_global():
    """Eski sunucu bazli ekonomiyi global sisteme kayipsiz aktarir.

    Eski satirlari ASLA silmez. Global kayit varsa ustune yazmaz;
    boylece her yeniden baslatmada bakiye/envanter sifirlanmaz.
    """
    cursor.execute("SELECT user_id, MAX(balance) FROM economy GROUP BY user_id")
    balances = cursor.fetchall()
    cursor.execute("SELECT DISTINCT user_id, item_key FROM inventory")
    items = cursor.fetchall()
    cursor.executemany(
        "INSERT OR IGNORE INTO economy (guild_id, user_id, balance) VALUES (0, ?, ?)",
        [(uid, max(0, int(balance or 0))) for uid, balance in balances],
    )
    cursor.executemany(
        "INSERT OR IGNORE INTO inventory (guild_id, user_id, item_key) VALUES (0, ?, ?)",
        [(uid, item_key) for uid, item_key in items],
    )
    db.commit()

migrate_economy_to_global()
active_users = {}
spam_messages = defaultdict(deque)
spam_warned = set()
join_bursts = defaultdict(deque)
raid_alert_cooldowns = {}
ai_histories = defaultdict(lambda: deque(maxlen=8))
# Bot sohbeti kanal bazinda tutulur; !botsohbet kapat komutuna kadar devam eder.
bot_chat_histories = defaultdict(lambda: deque(maxlen=8))
xp_last_awarded = {}
_verification_view_registered = False


def ensure_guild_settings(guild_id):
    cursor.execute("INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)", (guild_id,))
    db.commit()
    cursor.execute("SELECT log_channel_id, autorole_id, spam_limit, spam_window, timeout_minutes, protection_enabled FROM guild_settings WHERE guild_id=?", (guild_id,))
    row = cursor.fetchone()
    return {
        "log_channel_id": row[0], "autorole_id": row[1],
        "spam_limit": row[2] or 6, "spam_window": row[3] or 8,
        "timeout_minutes": row[4] or 5, "protection_enabled": bool(row[5]),
    }


def ensure_raid_settings(guild_id):
    cursor.execute("INSERT OR IGNORE INTO raid_settings (guild_id) VALUES (?)", (guild_id,))
    db.commit()
    cursor.execute(
        "SELECT enabled,threshold,window_seconds,mode_minutes,active_until,last_alert_at FROM raid_settings WHERE guild_id=?",
        (guild_id,),
    )
    row = cursor.fetchone()
    return {
        "enabled": bool(row[0]), "threshold": int(row[1]),
        "window_seconds": int(row[2]), "mode_minutes": int(row[3]),
        "active_until": float(row[4] or 0), "last_alert_at": float(row[5] or 0),
    }


def set_guild_setting(guild_id, key, value):
    allowed = {"log_channel_id", "autorole_id", "spam_limit", "spam_window", "timeout_minutes", "protection_enabled"}
    if key not in allowed:
        raise ValueError("Ayar adi gecersiz")
    ensure_guild_settings(guild_id)
    cursor.execute(f"UPDATE guild_settings SET {key}=? WHERE guild_id=?", (value, guild_id))
    db.commit()


async def send_security_log(guild, text):
    if guild is None:
        return
    settings = ensure_guild_settings(guild.id)
    channel_id = settings["log_channel_id"]
    if channel_id:
        channel = guild.get_channel(channel_id)
        if channel:
            try:
                await channel.send(text[:1900])
            except (discord.Forbidden, discord.HTTPException):
                pass


async def notify_owner(text):
    try:
        owner = bot.get_user(OWNER_ID) or await bot.fetch_user(OWNER_ID)
        await owner.send(text[:1900])
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"Sahibe DM gonderilemedi: {exc}")


# -------------------- YAPAY ZEKA --------------------
def get_ai_response(user_message, image_data_urls=None, history=None, system_prompt=None, clean_language=False):
    if not GROQ_API_KEY:
        return "Yapay zeka ayarli degil. Render ortam degiskenlerinde GROQ_API_KEY eksik."
    image_data_urls = image_data_urls or []
    model = GROQ_VISION_MODEL if image_data_urls else GROQ_TEXT_MODEL
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    if image_data_urls:
        content = [{"type": "text", "text": user_message or "Bu gorseli incele ve Turkce anlat."}]
        content.extend({"type": "image_url", "image_url": {"url": url}} for url in image_data_urls[:3])
    else:
        content = user_message
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": (system_prompt or mode_prompts[current_mode]) + " Konusman baglama gore degissin ve ayni kaliplari surekli tekrarlama." + (" Bu sunucuda kufur ve argo kullanma." if clean_language else "")},
            *(history or []),
            {"role": "user", "content": content},
        ],
        "max_completion_tokens": 900,
        "temperature": 0.7,
    }
    try:
        response = requests.post(GROQ_URL, headers=headers, json=payload, timeout=45)
        try:
            result = response.json()
        except ValueError:
            print(f"GROQ JSON HATASI: {response.text[:300]}")
            return "Yapay zeka beklenmedik bir yanit verdi."
        if response.status_code >= 400:
            print(f"GROQ API HATASI {response.status_code}: {result}")
            error = result.get("error", {}) if isinstance(result, dict) else {}
            message = error.get("message", "Bilinmeyen API hatasi")
            if image_data_urls:
                return "Gorsel modeli hata verdi. Render'daki GROQ_VISION_MODEL ayarini Groq hesabinda desteklenen bir modelle kontrol et."
            return f"Yapay zeka API hatasi: {message[:220]}"
        return result["choices"][0]["message"]["content"].strip()
    except requests.exceptions.RequestException as exc:
        print(f"GROQ ISTEK HATASI: {exc}")
        return "Yapay zeka servisine su an ulasamiyorum."
    except (KeyError, IndexError, TypeError) as exc:
        print(f"GROQ YANIT HATASI: {exc}")
        return "Yapay zeka yaniti okunamadi."


# -------------------- BAKLAVAI --------------------
# Acik olan kanallarda etiket gerekmeden her mesaja cevap veren, argo kullanan yapay zeka.
cursor.execute("""CREATE TABLE IF NOT EXISTS baklavai_channels (
    guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
)""")
db.commit()

BAKLAVAI_PROMPT = (
    "Sen Baklavai'sin, Baklava1923 Discord sunucusunun samimi, zeki ve sivri dilli yapay zekasisin. Turkce konus. "
    "Gundelik sohbette sokak agzi kullan; normal, eglenceli sohbetlerde mumkun oldugunca her cumlede bir dogal argo dokunusu olsun. "
    "Lan, ulan, amk, siktir ve bok gibi ifadeleri baglama uygun kullan; bir cumlede en fazla bir agir argo ifadesi kullan ve ayni kelimeyi surekli tekrarlama. "
    "Argo asla kisiye, ailesine, kimligine veya hassas ozelliklerine yonelik agir hakarete donusmesin. "
    "Kullanici ciddi, uzgun, korkmus, saglik veya guvenlik konusunda yardim istiyorsa kufur etme; sakin ve destekleyici ol. "
    "Kod, teknik soru, matematik ve bilgi taleplerinde once dogru ve ise yarar cevabi ver; argo cevabin onune gecmesin. "
    "Kisa ve vurucu yaz; kullanici detay isterse acikla. "
    "KESINLIKLE emoji, emoticon veya Discord emojisi kullanma; sadece duz yazi yaz. "
    "Nefret soylemi, tehdit veya baskasina zarar vermeyi kolaylastiran talimat verme."
)

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002300-\U000023FF"
    "\U00002B00-\U00002BFF\U0001F1E6-\U0001F1FF\u200d\ufe0f\u20e3]"
    r"|<a?:\w+:\d+>"
)
baklavai_histories = defaultdict(lambda: deque(maxlen=8))
_baklavai_last = {}


def strip_emojis(text):
    text = _EMOJI_RE.sub("", text or "")
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def get_baklavai_response(user_message, image_data_urls=None, history=None, clean_language=False):
    answer = get_ai_response(user_message, image_data_urls, history, system_prompt=BAKLAVAI_PROMPT, clean_language=clean_language)
    return strip_emojis(answer)


def is_baklavai_channel(guild_id, channel_id):
    cursor.execute(
        "SELECT 1 FROM baklavai_channels WHERE guild_id=? AND channel_id=?",
        (guild_id, channel_id),
    )
    return cursor.fetchone() is not None


async def handle_baklavai_message(message):
    content = re.sub(rf"<@!?{bot.user.id}>", "", message.content).strip()
    content = discord.utils.escape_mentions(content)[:1800]
    now = time.time()
    # Ayni kisi arka arkaya spam atinca her mesaja cevap vermesin.
    if now - _baklavai_last.get(message.author.id, 0) < 2.5:
        return
    images, errors = await read_image_attachments(message)
    if not content and images:
        content = "Bu gorseli incele ve anlat."
    elif not content:
        return
    _baklavai_last[message.author.id] = now
    try:
        async with message.channel.typing():
            key = (message.guild.id, message.channel.id, message.author.id)
            history = list(baklavai_histories[key])
            clean_lang = bool(message.guild) and not profanity_allowed(message.guild.id)
            answer = await bot.loop.run_in_executor(
                AI_EXECUTOR, functools.partial(get_baklavai_response, content, images, history, clean_language=clean_lang)
            )
            baklavai_histories[key].append({"role": "user", "content": content[:3000]})
            baklavai_histories[key].append({"role": "assistant", "content": (answer or "")[:3000]})
        if errors:
            answer += "\n\nNot: " + "; ".join(errors)
        await message.reply(
            (answer or "Su an cevap olusturamadim.")[:1900],
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except (discord.HTTPException, discord.Forbidden) as exc:
        print(f"BAKLAVAI MESAJ HATASI: {type(exc).__name__}: {exc}")


@bot.command(name="baklavai", aliases=["baklavayz", "bai"])
@commands.guild_only()
async def baklavai_cmd(ctx, *, arguman: str = None):
    """Baklavai: kanalda her mesaja cevap verir (ac/kapat/durum) veya dogrudan soru sorulur."""
    arg = (arguman or "").strip()
    low = arg.lower()
    is_staff = (
        ctx.author.id == OWNER_ID
        or ctx.author.guild_permissions.manage_guild
        or ctx.author.guild_permissions.manage_channels
    )

    if low in {"ac", "aç", "on", "ekle"}:
        if not is_staff:
            await ctx.send("Bunu açmak için Kanalları Yönet veya Sunucuyu Yönet izni lazım.")
            return
        cursor.execute(
            "INSERT OR IGNORE INTO baklavai_channels (guild_id, channel_id) VALUES (?, ?)",
            (ctx.guild.id, ctx.channel.id),
        )
        db.commit()
        await ctx.send("Baklavai bu kanalda açıldı. Artık etiket gerekmeden yazdığın her mesaja cevap veririm. Kapatmak için `B!baklavai kapat`.")
        return

    if low in {"kapat", "off", "kapa", "durdur"}:
        if not is_staff:
            await ctx.send("Bunu kapatmak için Kanalları Yönet veya Sunucuyu Yönet izni lazım.")
            return
        cursor.execute(
            "DELETE FROM baklavai_channels WHERE guild_id=? AND channel_id=?",
            (ctx.guild.id, ctx.channel.id),
        )
        db.commit()
        for key in [k for k in baklavai_histories if k[0] == ctx.guild.id and k[1] == ctx.channel.id]:
            baklavai_histories.pop(key, None)
        await ctx.send("Baklavai bu kanalda kapatıldı.")
        return

    if low in {"durum", "status"}:
        state = "açık" if is_baklavai_channel(ctx.guild.id, ctx.channel.id) else "kapalı"
        await ctx.send(f"Baklavai bu kanalda şu an **{state}**.")
        return

    if not arg:
        await ctx.send(
            "Kullanım:\n"
            "`B!baklavai aç` — bu kanalda her mesaja cevap verir\n"
            "`B!baklavai kapat` — kapatır\n"
            "`B!baklavai durum` — durumu gösterir\n"
            "`B!baklavai <soru>` — tek seferlik soru sorar"
        )
        return

    try:
        async with ctx.channel.typing():
            key = (ctx.guild.id, ctx.channel.id, ctx.author.id)
            history = list(baklavai_histories[key])
            clean_lang = bool(ctx.guild) and not profanity_allowed(ctx.guild.id)
            answer = await bot.loop.run_in_executor(
                AI_EXECUTOR,
                functools.partial(get_baklavai_response, discord.utils.escape_mentions(arg)[:1800], [], history, clean_language=clean_lang),
            )
            baklavai_histories[key].append({"role": "user", "content": arg[:3000]})
            baklavai_histories[key].append({"role": "assistant", "content": (answer or "")[:3000]})
        await ctx.reply(
            (answer or "Su an cevap olusturamadim.")[:1900],
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except (discord.HTTPException, discord.Forbidden) as exc:
        print(f"BAKLAVAI KOMUT HATASI: {type(exc).__name__}: {exc}")


# -------------------- YENI EGLENCE GUNCELLEMESI --------------------
def fun_cooldown_remaining(user_id, action, cooldown):
    cursor.execute("SELECT last_used FROM fun_cooldowns WHERE user_id=? AND action=?", (user_id, action))
    row = cursor.fetchone()
    if row:
        remaining = cooldown - (time.time() - row[0])
        if remaining > 0:
            return int(remaining) + 1
    return 0


def set_fun_cooldown(user_id, action):
    cursor.execute(
        "INSERT INTO fun_cooldowns (user_id, action, last_used) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, action) DO UPDATE SET last_used=excluded.last_used",
        (user_id, action, time.time())
    )
    db.commit()


@bot.command(name="şaka", aliases=["saka", "joke"])
async def saka(ctx):
    lines = [
        "Baklava1923 botuna RAM sordular, cevap verdi: AMK ben zaten sunucuda 7/24 çalışıyorum",
        "Bilgisayar neden doktora gitmiş? Çok fazla virüs kapmış",
        "Benim ping o kadar yüksek ki mesajım geçmişten geliyor",
        "Öğretmen: Neden geç kaldın? Öğrenci: Zaman benim önümden koştu hocam",
        "Sunucu o kadar sessiz ki bot kendi kendine AFK kaldı"
    ]
    await ctx.send(random.choice(lines))


@bot.command(name="meme", aliases=["mim"])
async def meme(ctx):
    lines = [
        "**Ben:** sadece 5 dakika oynayacağım\n**Ben 4 saat sonra:** AMK sabah olmuş",
        "**Render:** servis canlı\n**Bot:** ben niye çevrimdışıyım lan",
        "**Arkadaş:** sakin ol\n**Ben:** sakinim lan\n**İç ses:** SUNUCUYU YIK",
        "**Ping 20 ms:** efsaneyim\n**Ping 9999 ms:** ben artık uzay programıyım",
        "**Ödev:** 5 dakika sürer\n**Ben:** önce bir B!ship deneyelim"
    ]
    await ctx.send(random.choice(lines))


# GayRadar sonucu kullanıcı başına arka arkaya aynı yüzdeyi vermesin.
_gayradar_last_percent = {}


def make_gayradar_gif(name, percent):
    width, height = 640, 320
    frames = []
    try:
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 34)
        mid_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 25)
        small_font = ImageFont.truetype("DejaVuSans.ttf", 20)
    except OSError:
        title_font = mid_font = small_font = ImageFont.load_default()

    for i in range(20):
        image = Image.new("RGB", (width, height), (14, 24, 45))
        draw = ImageDraw.Draw(image)
        # Animated radar rings and rotating sweep.
        center = (320, 166)
        for radius in (48, 82, 116):
            draw.ellipse((center[0]-radius, center[1]-radius,
                          center[0]+radius, center[1]+radius),
                         outline=(30, 170, 145), width=2)
        import math
        angle = i * (math.pi / 10)
        end = (int(center[0] + 116 * math.cos(angle)),
               int(center[1] + 116 * math.sin(angle)))
        draw.line((center[0], center[1], end[0], end[1]), fill=(100, 255, 180), width=4)
        draw.ellipse((center[0]-7, center[1]-7, center[0]+7, center[1]+7), fill=(255, 90, 160))
        title = "GAY RADAR 24"
        box = draw.textbbox((0, 0), title, font=title_font)
        draw.text(((width-(box[2]-box[0]))/2, 14), title, font=title_font, fill=(255, 220, 245))
        label = name[:24]
        box = draw.textbbox((0, 0), label, font=mid_font)
        draw.text(((width-(box[2]-box[0]))/2, 225), label, font=mid_font, fill="white")
        result = f"%{percent} GAYSIN"
        box = draw.textbbox((0, 0), result, font=small_font)
        draw.text(((width-(box[2]-box[0]))/2, 267), result, font=small_font, fill=(100, 255, 180))
        frames.append(image)
    output = io.BytesIO()
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:], duration=90, loop=0, optimize=True)
    output.seek(0)
    return output


@bot.command(name="gayradar24", aliases=["gayradar", "radar24"])
async def gayradar24(ctx):
    # Rastgele gay yuzdesi sonucu.
    percent = random.randint(1, 100)
    previous = _gayradar_last_percent.get(ctx.author.id)
    if previous is not None and percent == previous:
        percent = (percent % 100) + 1
    _gayradar_last_percent[ctx.author.id] = percent
    try:
        gif = make_gayradar_gif(ctx.author.display_name, percent)
        await ctx.send(content=f"{ctx.author.mention} GayRadar24 sonucu: **%{percent} gaysin**", file=discord.File(gif, filename="gayradar24.gif"))
    except (discord.HTTPException, OSError, ValueError) as exc:
        print(f"GAYRADAR GIF HATASI: {type(exc).__name__}: {exc}")
        await ctx.send(f"{ctx.author.mention} GayRadar24 sonucu: **%{percent} gaysin** (GIF olusturulamadi)")


@bot.command(name="fakemesaj", aliases=["sahte", "fakemsg"])
@commands.guild_only()
async def fakemesaj(ctx, oyuncu: discord.Member = None, *, metin: str = None):
    """Etiketlenen uye adiyla, bot tarafindan olusturulan eglencelik mesaj gonderir."""
    if oyuncu is None or not metin:
        await ctx.send("Kullanım: `!fakemesaj @oyuncu merhaba`")
        return

    safe_text = discord.utils.escape_mentions(metin.strip())[:1800]
    if not safe_text:
        await ctx.send("Gönderilecek bir mesaj yazmalısın.")
        return

    # Hedefin adı ve avatarıyla önizleme; bot tarafından oluşturulduğu ayrıca belirtilir.
    fake_name = oyuncu.display_name[:80]
    avatar_url = oyuncu.display_avatar.url
    webhook = None
    try:
        if isinstance(ctx.channel, discord.TextChannel):
            webhook = await ctx.channel.create_webhook(name="Baklava1923 Sahte Mesaj")
            await webhook.send(
                safe_text,
                username=fake_name,
                avatar_url=avatar_url,
                allowed_mentions=discord.AllowedMentions.none(),
                wait=True,
            )
            await ctx.send("_Bu mesaj Baklava1923-Bot aracılığıyla oluşturuldu._")
            return
    except (discord.Forbidden, discord.HTTPException, AttributeError) as exc:
        print(f"FAKEMESAJ WEBHOOK HATASI: {type(exc).__name__}: {exc}")
    finally:
        if webhook is not None:
            try:
                await webhook.delete(reason="Tek seferlik sahte mesaj komutu")
            except (discord.Forbidden, discord.HTTPException):
                pass

    # Webhook izni yoksa yine avatarli fakat acikça etiketlenmis yedek görünüm.
    embed = discord.Embed(
        title="MESAJ ÖNİZLEMESİ",
        description=safe_text,
        color=discord.Color.orange(),
    )
    embed.set_author(name=fake_name, icon_url=avatar_url)
    embed.set_footer(text="Baklava1923-Bot aracılığıyla oluşturuldu • Hedef üye tarafından gönderilmedi")
    await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="botsohbet", aliases=["botkonusma", "botsohbeti"])
@commands.guild_only()
async def botsohbet(ctx, *, arguman: str = None):
    """Secilen botla mesaj sayisi sinirli sohbet baslatir veya durdurur."""
    if not (ctx.author.id == OWNER_ID or ctx.author.guild_permissions.manage_guild):
        await ctx.send("Bu ayarı kullanmak için Sunucuyu Yönet izni gerekiyor.")
        return

    arg = (arguman or "").strip()
    if arg.lower() in {"kapat", "off", "stop", "durdur"}:
        cursor.execute(
            "DELETE FROM bot_chat_settings WHERE guild_id=? AND channel_id=?",
            (ctx.guild.id, ctx.channel.id),
        )
        db.commit()
        bot_chat_histories.pop((ctx.guild.id, ctx.channel.id), None)
        await ctx.send("Bu kanalda botlar arası sohbet kapatıldı.")
        return

    if arg.lower() in {"durum", "status"}:
        cursor.execute(
            "SELECT peer_bot_id, message_limit, messages_sent FROM bot_chat_settings WHERE guild_id=? AND channel_id=? AND enabled=1",
            (ctx.guild.id, ctx.channel.id),
        )
        row = cursor.fetchone()
        if row:
            await ctx.send(
                f"Bot sohbeti açık. Hedef: <@{row[0]}> · {row[2]}/{row[1]} yanıt kullanıldı. "
                "Kapatmak için `!botsohbet kapat` yaz."
            )
        else:
            await ctx.send("Bu kanalda bot sohbeti kapalı. Açmak için `!botsohbet @bot mesaj_sınırı` yaz.")
        return

    peer = ctx.message.mentions[0] if ctx.message.mentions else None
    if peer is None:
        await ctx.send(
            "Kullanım: `!botsohbet @bot 20` (1–3000 yanıt)\n"
            "Kapatmak için: `!botsohbet kapat`\nDurum için: `!botsohbet durum`"
        )
        return
    if not peer.bot or (bot.user and peer.id == bot.user.id):
        await ctx.send("Hedef olarak arkadaşının başka bir Discord botunu etiketlemelisin.")
        return

    # Mesaj metnindeki etiket disindaki sayiyi al; varsayilan 20, guvenli ust sinir 3000.
    without_mention = re.sub(r"<@!?\d+>", " ", arg)
    numbers = re.findall(r"\d+", without_mention)
    limit = int(numbers[0]) if numbers else 20
    if not 1 <= limit <= 3000:
        await ctx.send("Sohbet sınırı 1 ile 3000 yanıt arasında olmalı.")
        return

    cursor.execute(
        "INSERT INTO bot_chat_settings (guild_id, channel_id, peer_bot_id, enabled, message_limit, messages_sent) "
        "VALUES (?, ?, ?, 1, ?, 0) "
        "ON CONFLICT(guild_id, channel_id) DO UPDATE SET peer_bot_id=excluded.peer_bot_id, enabled=1, "
        "message_limit=excluded.message_limit, messages_sent=0",
        (ctx.guild.id, ctx.channel.id, peer.id, limit),
    )
    db.commit()
    bot_chat_histories[(ctx.guild.id, ctx.channel.id)].clear()
    await ctx.send(
        f"Bot sohbeti açıldı. Hedef: {peer.mention} · Yanıt sınırı: **{limit}**.\n"
        "Sohbet bu sınıra ulaşınca otomatik durur. İstediğin zaman `!botsohbet kapat` yazabilirsin."
    )
    await ctx.send(
        f"{peer.mention} selam! Ben Baklava1923-Bot. Nasılsın, biraz sohbet edelim mi?",
        allowed_mentions=discord.AllowedMentions(users=[peer]),
    )

@bot.command(name="tersle")
async def tersle(ctx, oyuncu: discord.Member = None):
    hedef = oyuncu or ctx.author
    lines = ["senin planı görünce hata mesajı bile kendini kapattı", "zeka var da galiba şu an bakımda", "sen girince sohbetin FPS'i düştü", "bu özgüvenin kaynağı ne lan, deneme sürümü mü", "senin stratejiye bakınca rastgele tuşlara basan klavye bile profesör"]
    await ctx.send(f"{hedef.mention} {random.choice(lines)}")


@bot.command(name="roast")
async def roast(ctx, oyuncu: discord.Member = None):
    hedef = oyuncu or ctx.author
    lines = ["senin pingin değil kararların gecikmeli", "senin taktikler yüklenirken maç bitti", "özgüven full, planlama henüz indiriliyor", "senin bilgisayar seni görünce güvenli mod açıyor", "bu performansla bot bile sana tutorial açacak"]
    await ctx.send(f"{hedef.mention} {random.choice(lines)}")


@bot.command(name="ego")
async def ego(ctx):
    await ctx.send(random.choice(["Ben Baklava1923 Bot'um, sunucuya girdim ve işlemci fanları alkışlamaya başladı", "Egom o kadar yüksek ki pingim bile bana yetişemiyor", "Ben hata yapmam, sadece beklenmedik özellik yayınlarım", "AMK ben olmasam bu sunucuda kim zar atacak"]))


@bot.command(name="iltifat")
async def iltifat(ctx, oyuncu: discord.Member = None):
    hedef = oyuncu or ctx.author
    await ctx.send(f"{hedef.mention} {random.choice(['bu sunucuda ortamı güzelleştiren insanlardan birisin', 'enerjin iyi, böyle devam et', 'sen olunca sohbet daha eğlenceli oluyor', 'en azından bugün algoritma senden yana'])}")


@bot.command(name="kimdaha")
async def kimdaha(ctx, oyuncu: discord.Member = None):
    if oyuncu is None:
        await ctx.send("Kullanım: `B!kimdaha @oyuncu`")
        return
    if oyuncu.id == ctx.author.id:
        await ctx.send("İkiniz de aynı kişisiniz lan, karşılaştırma çöktü")
        return
    winner = random.choice([ctx.author, oyuncu])
    await ctx.send(f"Bugünkü rastgele sonuç: {winner.mention} {random.choice(['daha şanslı', 'daha komik', 'daha kaotik', 'daha efsane'])} çıktı. Bilimsel mi? Asla.")


guess_games = {}


@bot.command(name="sayıtahmin", aliases=["sayitahmin", "sayıtahmini"])
async def sayitahmin(ctx):
    guess_games[(ctx.guild.id if ctx.guild else 0, ctx.author.id)] = {"number": random.randint(1, 100), "expires": time.time() + 120, "tries": 0}
    await ctx.send(f"{ctx.author.mention} 1 ile 100 arasında sayı tuttum. `B!tahmin <sayı>` yaz. 120 saniyen var.")


@bot.command(name="tahmin")
async def tahmin(ctx, sayi: int = None):
    key = (ctx.guild.id if ctx.guild else 0, ctx.author.id)
    game = guess_games.get(key)
    if not game or game["expires"] < time.time():
        guess_games.pop(key, None)
        await ctx.send("Aktif oyunun yok. `B!sayıtahmin` ile başlat.")
        return
    if sayi is None or not 1 <= sayi <= 100:
        await ctx.send("1 ile 100 arasında bir sayı yaz. Örnek: `B!tahmin 42`")
        return
    game["tries"] += 1
    if sayi == game["number"]:
        reward = 100 + max(0, 5 - game["tries"]) * 20
        add_balance(0, ctx.author.id, reward)
        guess_games.pop(key, None)
        await ctx.send(f"Bildin lan! {game['tries']} denemede buldun ve **{reward} paklava** kazandın.")
    elif sayi < game["number"]:
        await ctx.send("Daha büyük bir sayı dene.")
    else:
        await ctx.send("Daha küçük bir sayı dene.")


class ReflexView(discord.ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=20)
        self.author_id = author_id
        self.ready_at = time.time() + random.uniform(2.0, 5.0)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Bu refleks testi komutu başlatan kişiye ait.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="ŞİMDİ BAS!", style=discord.ButtonStyle.success)
    async def press_now(self, interaction, button):
        elapsed = time.time()
        if elapsed < self.ready_at:
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(content="Erken bastın lan, refleks değil tahmin testi oldu. `B!refleks` ile tekrar dene.", view=self)
            self.stop()
            return
        ms = round((elapsed - self.ready_at) * 1000)
        for child in self.children:
            child.disabled = True
        reward = 150 if ms < 250 else 75 if ms < 450 else 25
        add_balance(0, interaction.user.id, reward)
        await interaction.response.edit_message(content=f"Refleks süren: **{ms} ms**. Ödül: **{reward} paklava**.", view=self)
        self.stop()


@bot.command(name="refleks")
async def refleks(ctx):
    await ctx.send("Refleks testi başladı. Biraz bekle, sonra butona bas!", view=ReflexView(ctx.author.id))


@bot.command(name="günlük", aliases=["gunluk", "daily"])
async def gunluk(ctx):
    remaining = fun_cooldown_remaining(ctx.author.id, "daily", 6 * 3600)
    if remaining:
        hours, rem = divmod(remaining, 3600)
        await ctx.send(f"Günlük ödülünü zaten aldın. Yaklaşık **{hours} saat {rem // 60} dakika** sonra tekrar gel.")
        return
    reward = random.randint(250, 500)
    add_balance(0, ctx.author.id, reward)
    set_fun_cooldown(ctx.author.id, "daily")
    await ctx.send(f"{ctx.author.mention} günlük ödülün: **+{reward} paklava**. `B!bakiye` ile bakabilirsin.")


@bot.command(name="soygun")
async def soygun(ctx, oyuncu: discord.Member = None):
    if oyuncu and (oyuncu.bot or oyuncu.id == ctx.author.id):
        await ctx.send("Kendini veya botları hedef seçemezsin.")
        return
    remaining = fun_cooldown_remaining(ctx.author.id, "robbery", 10 * 60)
    if remaining:
        await ctx.send(f"Soygun için **{remaining // 60} dakika {remaining % 60} saniye** bekle.")
        return
    if oyuncu is None:
        balance = get_balance(0, ctx.author.id)
        if balance < 100 and ctx.author.id != OWNER_ID:
            await ctx.send("Soygun denemek için en az 100 paklavan olmalı.")
            return
        if random.random() < 0.45:
            loss = min(balance, random.randint(50, 150))
            add_balance(0, ctx.author.id, -loss)
            result = f"Yakalandın lan ve **{loss} paklava** ceza ödedin."
        else:
            reward = random.randint(100, 250)
            add_balance(0, ctx.author.id, reward)
            result = f"Soygun tuttu, **+{reward} paklava** kazandın."
    else:
        target_balance = get_balance(0, oyuncu.id)
        if target_balance < 100:
            await ctx.send("Hedefin en az 100 paklavası olmalı.")
            return
        if random.random() < 0.45:
            loss = min(get_balance(0, ctx.author.id), random.randint(50, 150))
            add_balance(0, ctx.author.id, -loss)
            result = f"Yakalandın ve **{loss} paklava** ceza ödedin."
        else:
            reward = min(target_balance, random.randint(50, 200))
            add_balance(0, oyuncu.id, -reward)
            add_balance(0, ctx.author.id, reward)
            result = f"{oyuncu.mention} üzerinden **{reward} paklava** aldın."
    set_fun_cooldown(ctx.author.id, "robbery")
    await ctx.send(result)


@bot.command(name="hediye")
async def hediye(ctx, oyuncu: discord.Member = None, miktar: int = None):
    if oyuncu is None or miktar is None:
        await ctx.send("Kullanım: `B!hediye @oyuncu <miktar>`")
        return
    if oyuncu.bot or oyuncu.id == ctx.author.id:
        await ctx.send("Kendine veya botlara paklava gönderemezsin.")
        return
    if not 1 <= miktar <= 1000000:
        await ctx.send("Hediye miktarı 1 ile 1.000.000 arasında olmalı.")
        return
    balance = get_balance(0, ctx.author.id)
    if ctx.author.id != OWNER_ID and balance < miktar:
        await ctx.send(f"Yeterli paklavan yok. Bakiyen: {balance_label(ctx.author.id, balance)}")
        return
    if ctx.author.id != OWNER_ID:
        add_balance(0, ctx.author.id, -miktar)
    add_balance(0, oyuncu.id, miktar)
    await ctx.send(f"{ctx.author.mention}, {oyuncu.mention} kişisine **{miktar} paklava** hediye etti.")


# -------------------- GLOBAL PAKLAVA EKONOMISI --------------------
MARKET_ITEMS = {
    "bakir_kasik": {"ad": "Bakır Kaşık", "fiyat": 500, "bonus": 0.10, "aciklama": "Düello kazancını %10 artırır"},
    "gumus_catal": {"ad": "Gümüş Çatal", "fiyat": 1500, "bonus": 0.25, "aciklama": "Düello kazancını %25 artırır"},
    "altin_tepsi": {"ad": "Altın Tepsi", "fiyat": 4000, "bonus": 0.50, "aciklama": "Düello kazancını %50 artırır"},
    "elmas_firin": {"ad": "Elmas Fırın", "fiyat": 10000, "bonus": 1.00, "aciklama": "Düello kazancını %100 artırır"},
    "paklava_tacı": {"ad": "Paklava Tacı", "fiyat": 25000, "bonus": 2.00, "aciklama": "Düello kazancını %200 artırır"},
    "kombo_karti": {"ad": "Kombo Kartı", "fiyat": 7000, "bonus": 0.35, "aciklama": "Düello kazancını %35 artırır"},
    "zafer_taci": {"ad": "Zafer Tacı", "fiyat": 18000, "bonus": 0.75, "aciklama": "Düello kazancını %75 artırır"},
    "efsane_kupasi": {"ad": "Efsane Kupası", "fiyat": 40000, "bonus": 1.50, "aciklama": "Düello kazancını %150 artırır"},
    "gumus_fincan": {"ad": "Gümüş Fincan", "fiyat": 900, "bonus": 0.00, "aciklama": "Koleksiyon eşyası"},
    "baklava_kutusu": {"ad": "Özel Baklava Kutusu", "fiyat": 1800, "bonus": 0.00, "aciklama": "Koleksiyon eşyası"},
    "sef_sapkasi": {"ad": "Şef Şapkası", "fiyat": 2600, "bonus": 0.00, "aciklama": "Koleksiyon eşyası"},
    "mermer_saray": {"ad": "Mermer Saray Maketi", "fiyat": 6500, "bonus": 0.00, "aciklama": "Koleksiyon eşyası"},
    "altin_kepce": {"ad": "Altın Kepçe", "fiyat": 8500, "bonus": 0.00, "aciklama": "Koleksiyon eşyası"},
    "usta_sertifikasi": {"ad": "Ustalık Sertifikası", "fiyat": 12000, "bonus": 0.00, "aciklama": "Koleksiyon eşyası"},
    "baklava_atolyesi_maketi": {"ad": "Baklava Atölyesi Maketi", "fiyat": 16000, "bonus": 0.00, "aciklama": "Koleksiyon eşyası"},
    "efsane_tepsi": {"ad": "Efsane Tepsi", "fiyat": 22000, "bonus": 0.00, "aciklama": "Koleksiyon eşyası"},
    "paklava_nişani": {"ad": "Paklava Nişanı", "fiyat": 30000, "bonus": 0.00, "aciklama": "Koleksiyon eşyası"},
    "altin_firin": {"ad": "Altın Fırın Maketi", "fiyat": 55000, "bonus": 0.00, "aciklama": "Koleksiyon eşyası"},
    "efsane_kasa": {"ad": "Efsane Kasa", "fiyat": 80000, "bonus": 0.00, "aciklama": "Koleksiyon eşyası"},
}


def balance_label(user_id, amount=None):
    if user_id == OWNER_ID:
        return "Sinirsiz"
    return f"{int(amount if amount is not None else STARTING_BALANCE):,}".replace(",", ".")


def get_balance(guild_id, user_id):
    if user_id == OWNER_ID:
        return 10**15
    cursor.execute("SELECT balance FROM economy WHERE guild_id=0 AND user_id=?", (user_id,))
    row = cursor.fetchone()
    if row is None:
        cursor.execute("INSERT OR IGNORE INTO economy (guild_id,user_id,balance) VALUES (0,?,?)", (user_id, STARTING_BALANCE))
        db.commit()
        return STARTING_BALANCE
    return int(row[0])


def set_balance(guild_id, user_id, new_balance):
    if user_id == OWNER_ID:
        return 10**15
    get_balance(guild_id, user_id)
    cursor.execute("UPDATE economy SET balance=? WHERE guild_id=0 AND user_id=?", (max(0, int(new_balance)), user_id))
    db.commit()
    return get_balance(guild_id, user_id)


def add_balance(guild_id, user_id, amount):
    if user_id == OWNER_ID:
        return 10**15
    return set_balance(guild_id, user_id, get_balance(guild_id, user_id) + int(amount))


def get_inventory(guild_id, user_id):
    cursor.execute("SELECT item_key FROM inventory WHERE guild_id=0 AND user_id=?", (user_id,))
    return [row[0] for row in cursor.fetchall()]


def get_duel_bonus(guild_id, user_id):
    return sum(MARKET_ITEMS[key]["bonus"] for key in get_inventory(guild_id, user_id) if key in MARKET_ITEMS)


# -------------------- 3.6: GENİŞLETİLMİŞ EKONOMİ --------------------
JOBS = {
    "madenci": {"ad": "Madenci", "min": 110, "max": 240},
    "aşçı": {"ad": "Aşçı", "min": 100, "max": 220},
    "asci": {"ad": "Aşçı", "min": 100, "max": 220},
    "çiftçi": {"ad": "Çiftçi", "min": 90, "max": 210},
    "ciftci": {"ad": "Çiftçi", "min": 90, "max": 210},
    "kurye": {"ad": "Kurye", "min": 100, "max": 230},
    "mühendis": {"ad": "Mühendis", "min": 140, "max": 290},
    "muhendis": {"ad": "Mühendis", "min": 140, "max": 290},
    "yazılımcı": {"ad": "Yazılımcı", "min": 150, "max": 310},
    "yazilimci": {"ad": "Yazılımcı", "min": 150, "max": 310},
}
BUSINESSES = {
    "çayocağı": {"ad": "Çay Ocağı", "fiyat": 6000, "gelir": 350},
    "cayocagi": {"ad": "Çay Ocağı", "fiyat": 6000, "gelir": 350},
    "paklavacı": {"ad": "Paklavacı", "fiyat": 18000, "gelir": 950},
    "paklavaci": {"ad": "Paklavacı", "fiyat": 18000, "gelir": 950},
    "oyunkafe": {"ad": "Oyun Kafesi", "fiyat": 35000, "gelir": 2100},
    "otel": {"ad": "Paklava Oteli", "fiyat": 80000, "gelir": 5200},
}
BUSINESS_DISPLAY_KEYS = ["çayocağı", "paklavacı", "oyunkafe", "otel"]


def get_bank_balance(user_id):
    cursor.execute("SELECT balance FROM bank_accounts WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def change_bank_balance(user_id, amount):
    new_balance = max(0, get_bank_balance(user_id) + int(amount))
    cursor.execute(
        "INSERT INTO bank_accounts (user_id,balance) VALUES (?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET balance=excluded.balance",
        (user_id, new_balance),
    )
    db.commit()
    return new_balance


def normalize_economy_key(value):
    value = value.strip().lower()
    translation = str.maketrans({"ı": "i", "ş": "s", "ğ": "g", "ü": "u", "ö": "o", "ç": "c"})
    return value.translate(translation).replace(" ", "").replace("-", "")


@bot.command(name="banka")
async def banka(ctx):
    wallet = get_balance(0, ctx.author.id)
    bank = get_bank_balance(ctx.author.id)
    embed = discord.Embed(title="Paklava Bankası", color=discord.Color.blurple())
    embed.add_field(name="Cüzdan", value=f"{balance_label(ctx.author.id, wallet)} paklava", inline=True)
    embed.add_field(name="Banka", value=f"{bank:,} paklava".replace(",", "."), inline=True)
    embed.add_field(name="Toplam servet", value=f"{balance_label(ctx.author.id, wallet + bank)} paklava", inline=False)
    embed.set_footer(text="B!yatır <miktar> · B!çek <miktar>")
    await ctx.send(embed=embed)


@bot.command(name="yatır", aliases=["yatir", "bankayatir"])
async def yatir(ctx, miktar: int = None):
    if ctx.author.id == OWNER_ID:
        await ctx.send("Kurucu hesabı sınırsız bakiyeli olduğu için banka işlemi kullanmıyor.")
        return
    if miktar is None or miktar <= 0:
        await ctx.send("Kullanım: `B!yatır <miktar>`")
        return
    wallet = get_balance(0, ctx.author.id)
    if miktar > wallet:
        await ctx.send(f"Cüzdanında yeterli paklava yok. Bakiyen: **{balance_label(ctx.author.id, wallet)}**")
        return
    add_balance(0, ctx.author.id, -miktar)
    new_bank = change_bank_balance(ctx.author.id, miktar)
    await ctx.send(f"Bankaya **{miktar:,}** paklava yatırdın. Banka bakiyen: **{new_bank:,}**".replace(",", "."))


@bot.command(name="çek", aliases=["cek", "bankacek"])
async def cek(ctx, miktar: int = None):
    if ctx.author.id == OWNER_ID:
        await ctx.send("Kurucu hesabı sınırsız bakiyeli olduğu için banka işlemi kullanmıyor.")
        return
    if miktar is None or miktar <= 0:
        await ctx.send("Kullanım: `B!çek <miktar>`")
        return
    bank = get_bank_balance(ctx.author.id)
    if miktar > bank:
        await ctx.send(f"Bankada yeterli paklava yok. Banka bakiyen: **{bank:,}**".replace(",", "."))
        return
    new_bank = change_bank_balance(ctx.author.id, -miktar)
    add_balance(0, ctx.author.id, miktar)
    await ctx.send(f"Bankadan **{miktar:,}** paklava çektin. Kalan banka bakiyen: **{new_bank:,}**".replace(",", "."))


@bot.command(name="haftalık", aliases=["haftalik", "weekly"])
async def haftalik(ctx):
    remaining = fun_cooldown_remaining(ctx.author.id, "weekly_economy", 24 * 3600)
    if remaining:
        days, rem = divmod(remaining, 86400)
        hours = rem // 3600
        await ctx.send(f"Haftalık ödülünü zaten aldın. **{days} gün {hours} saat** sonra tekrar gel.")
        return
    reward = random.randint(1800, 2800)
    add_balance(0, ctx.author.id, reward)
    set_fun_cooldown(ctx.author.id, "weekly_economy")
    await ctx.send(f"{ctx.author.mention} haftalık ödülün: **+{reward:,} paklava**".replace(",", "."))


@bot.command(name="meslekler")
async def meslekler(ctx):
    lines = [
        f"**{JOBS[key]['ad']}** — çalışma başına {JOBS[key]['min']}-{JOBS[key]['max']} paklava"
        for key in JOBS if key not in ("asci", "ciftci", "muhendis", "yazilimci")
    ]
    await ctx.send(embed=discord.Embed(title="Meslek Panosu", description="\n".join(lines) + "\n\nMeslek seçmek için `B!meslek <isim>` yaz. Meslek değişimi 1 saatte bir yapılabilir.", color=discord.Color.teal()))


@bot.command(name="meslek")
async def meslek(ctx, *, meslek_adi: str = None):
    if not meslek_adi:
        cursor.execute("SELECT job_key,changed_at FROM economy_jobs WHERE user_id=?", (ctx.author.id,))
        row = cursor.fetchone()
        if row and row[0] in JOBS:
            await ctx.send(f"Mesleğin: **{JOBS[row[0]]['ad']}**. Değiştirmek için `B!meslek <isim>` yaz.")
        else:
            await ctx.send("Henüz mesleğin yok. `B!meslekler` ile seçim yap.")
        return
    key = normalize_economy_key(meslek_adi)
    if key not in JOBS:
        await ctx.send("Bu meslek bulunamadı. `B!meslekler` komutuna bak.")
        return
    cursor.execute("SELECT job_key,changed_at FROM economy_jobs WHERE user_id=?", (ctx.author.id,))
    row = cursor.fetchone()
    if row and time.time() - float(row[1]) < 3600:
        remaining = int(3600 - (time.time() - float(row[1])))
        await ctx.send(f"Mesleğini değiştirmek için **{remaining // 3600} saat {(remaining % 3600) // 60} dakika** beklemelisin.")
        return
    cursor.execute(
        "INSERT INTO economy_jobs (user_id,job_key,changed_at) VALUES (?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET job_key=excluded.job_key,changed_at=excluded.changed_at",
        (ctx.author.id, key, time.time()),
    )
    db.commit()
    await ctx.send(f"Yeni mesleğin **{JOBS[key]['ad']}**. Çalışmak için `B!çalış` yaz.")


@bot.command(name="çalış", aliases=["calis", "work"])
async def calis(ctx):
    cursor.execute("SELECT job_key FROM economy_jobs WHERE user_id=?", (ctx.author.id,))
    row = cursor.fetchone()
    if not row or row[0] not in JOBS:
        await ctx.send("Önce bir meslek seçmelisin. `B!meslekler` komutuna bak.")
        return
    remaining = fun_cooldown_remaining(ctx.author.id, "job_work", 10 * 60)
    if remaining:
        await ctx.send(f"Tekrar çalışmak için **{remaining // 60} dakika {remaining % 60} saniye** bekle.")
        return
    job = JOBS[row[0]]
    reward = random.randint(job["min"], job["max"])
    add_balance(0, ctx.author.id, reward)
    set_fun_cooldown(ctx.author.id, "job_work")
    await ctx.send(f"{ctx.author.mention} **{job['ad']}** olarak çalıştın ve **+{reward} paklava** kazandın.")


@bot.command(name="işletmeler", aliases=["isletmeler"])
async def isletmeler(ctx):
    lines = []
    for key in BUSINESS_DISPLAY_KEYS:
        item = BUSINESSES[key]
        lines.append(f"**{item['ad']}** — Kurulum: {item['fiyat']:,} paklava · 1 saatte bir gelir: {item['gelir']:,}".replace(",", ".") + f"\nKomut: `B!işletmekur {key}`")
    await ctx.send(embed=discord.Embed(title="Paklava İşletmeleri", description="\n\n".join(lines), color=discord.Color.gold()))


@bot.command(name="işletmekur", aliases=["isletmekur"])
async def isletmekur(ctx, *, isletme_adi: str = None):
    if not isletme_adi:
        await ctx.send("Kullanım: `B!işletmekur <işletme>` — `B!işletmeler` komutuna bak.")
        return
    key = normalize_economy_key(isletme_adi)
    if key not in BUSINESSES:
        await ctx.send("Bu işletme bulunamadı. `B!işletmeler` komutuna bak.")
        return
    cursor.execute("SELECT business_key FROM economy_businesses WHERE user_id=?", (ctx.author.id,))
    existing = cursor.fetchone()
    if existing:
        await ctx.send("Zaten bir işletmen var. Her kullanıcı bir işletme kurabilir.")
        return
    business = BUSINESSES[key]
    balance = get_balance(0, ctx.author.id)
    if balance < business["fiyat"]:
        await ctx.send(f"Kurulum için {business['fiyat']:,} paklava gerekiyor. Bakiyen: {balance_label(ctx.author.id, balance)}".replace(",", "."))
        return
    add_balance(0, ctx.author.id, -business["fiyat"])
    cursor.execute("INSERT INTO economy_businesses (user_id,business_key,purchased_at) VALUES (?,?,?)", (ctx.author.id, key, time.time()))
    db.commit()
    await ctx.send(f"Tebrikler! **{business['ad']}** işletmesini kurdun. Her 1 saatte bir `B!işletmetopla` ile **{business['gelir']:,} paklava** kazanabilirsin.".replace(",", "."))


@bot.command(name="işletme", aliases=["isletme"])
async def isletme(ctx):
    cursor.execute("SELECT business_key,purchased_at FROM economy_businesses WHERE user_id=?", (ctx.author.id,))
    row = cursor.fetchone()
    if not row or row[0] not in BUSINESSES:
        await ctx.send("Henüz işletmen yok. `B!işletmeler` ile bir işletme seçebilirsin.")
        return
    business = BUSINESSES[row[0]]
    remaining = fun_cooldown_remaining(ctx.author.id, "business_collect", 3600)
    status = "Gelir toplamaya hazır" if not remaining else f"Sonraki gelir için yaklaşık {remaining // 3600} saat {(remaining % 3600) // 60} dakika"
    embed = discord.Embed(title=business["ad"], description=f"1 saatlik gelir: **{business['gelir']:,} paklava**\nDurum: **{status}**".replace(",", "."), color=discord.Color.gold())
    await ctx.send(embed=embed)


@bot.command(name="işletmetopla", aliases=["isletmetopla", "işletmegelir", "isletmegelir"])
async def isletmetopla(ctx):
    cursor.execute("SELECT business_key FROM economy_businesses WHERE user_id=?", (ctx.author.id,))
    row = cursor.fetchone()
    if not row or row[0] not in BUSINESSES:
        await ctx.send("Gelir toplamak için önce `B!işletmekur` ile işletme kurmalısın.")
        return
    remaining = fun_cooldown_remaining(ctx.author.id, "business_collect", 3600)
    if remaining:
        await ctx.send(f"İşletme geliri için **{remaining // 3600} saat {(remaining % 3600) // 60} dakika** beklemelisin.")
        return
    business = BUSINESSES[row[0]]
    add_balance(0, ctx.author.id, business["gelir"])
    set_fun_cooldown(ctx.author.id, "business_collect")
    await ctx.send(f"**{business['ad']}** işletmenden **+{business['gelir']:,} paklava** topladın.".replace(",", "."))


@bot.command(name="bakiye", aliases=["cuzdan", "cüzdan"])
async def bakiye(ctx, oyuncu: discord.Member = None):
    hedef = oyuncu or ctx.author
    amount = get_balance(getattr(ctx.guild, "id", 0), hedef.id)
    embed = discord.Embed(title="Paklava Cuzdani", description=f"{hedef.mention} → **{balance_label(hedef.id, amount)}** paklava", color=discord.Color.orange())
    await ctx.send(embed=embed)


@bot.command(name="yazitura", aliases=["yazıtura"])
async def yazitura(ctx, secim: str = None):
    if secim is None:
        await ctx.send("Kullanim: `B!yazitura <yazi/tura>` — bakiye degismez.")
        return
    secim = secim.lower().strip()
    if secim not in ("yazi", "yazı", "tura"):
        await ctx.send("Secimin `yazi` veya `tura` olmali.")
        return
    secim = "yazi" if secim in ("yazi", "yazı") else "tura"
    sonuc = random.choice(["yazi", "tura"])
    await ctx.send(f"Para **{sonuc}** geldi. " + ("Dogru tahmin ettin." if secim == sonuc else "Bu sefer tahminin tutmadi."))


@bot.command(name="market")
async def market(ctx):
    embed = discord.Embed(title="Paklava Market", description="Satin almak icin: `B!satinal <esya_adi>`", color=discord.Color.gold())
    for key, item in MARKET_ITEMS.items():
        embed.add_field(name=f"{item['ad']} — {item['fiyat']} paklava", value=f"{item['aciklama']}\nKomut: `B!satinal {key}`", inline=False)
    await ctx.send(embed=embed)


@bot.command(name="satinal", aliases=["satınal"])
async def satinal(ctx, *, esya: str = None):
    if not esya:
        await ctx.send("Esya adi yaz. `B!market` komutuyla listeye bak.")
        return
    key = esya.lower().strip().replace(" ", "_")
    if key not in MARKET_ITEMS:
        await ctx.send("Boyle bir esya yok. `B!market` komutuna bak.")
        return
    if key in get_inventory(0, ctx.author.id):
        await ctx.send("Bu esyaya zaten sahipsin.")
        return
    item = MARKET_ITEMS[key]
    balance = get_balance(getattr(ctx.guild, "id", 0), ctx.author.id)
    if balance < item["fiyat"]:
        await ctx.send(f"Yeterli paklavan yok. Gereken: {item['fiyat']}. Bakiyen: {balance_label(ctx.author.id, balance)}")
        return
    add_balance(0, ctx.author.id, -item["fiyat"])
    cursor.execute("INSERT OR IGNORE INTO inventory (guild_id,user_id,item_key) VALUES (0,?,?)", (ctx.author.id, key))
    db.commit()
    await ctx.send(f"{item['ad']} satin alindi. Kalan bakiye: {balance_label(ctx.author.id, get_balance(0, ctx.author.id))}")


@bot.command(name="envanter")
async def envanter(ctx, oyuncu: discord.Member = None):
    target = oyuncu or ctx.author
    items = get_inventory(0, target.id)
    embed = discord.Embed(title=f"{target.display_name} envanteri", color=discord.Color.purple())
    if not items:
        embed.description = "Envanter bos. `B!market` ile esya alabilirsin."
    else:
        names = [MARKET_ITEMS[k]["ad"] for k in items if k in MARKET_ITEMS]
        bonus = get_duel_bonus(0, target.id)
        embed.description = "\n".join(names) + f"\n\nDuello kazanc bonusi: +%{int(bonus * 100)}"
    await ctx.send(embed=embed)


# -------------------- AKTIFLIK --------------------
def is_active(status):
    return status in (discord.Status.online, discord.Status.idle, discord.Status.dnd)


def start_tracking(guild_id, user_id):
    active_users.setdefault((guild_id, user_id), time.time())


def stop_tracking(guild_id, user_id):
    key = (guild_id, user_id)
    if key in active_users:
        start = active_users.pop(key)
        cursor.execute("INSERT INTO activity (guild_id,user_id,start_time,end_time) VALUES (?,?,?,?)", (guild_id, user_id, start, time.time()))
        db.commit()


@bot.event
async def on_presence_update(before, after):
    if after.bot or after.guild is None:
        return
    old, new = is_active(before.status), is_active(after.status)
    if not old and new:
        start_tracking(after.guild.id, after.id)
    elif old and not new:
        stop_tracking(after.guild.id, after.id)


def get_activity(guild_id, user_id, days):
    now, beginning = time.time(), time.time() - days * 86400
    total = 0
    cursor.execute("SELECT start_time,end_time FROM activity WHERE guild_id=? AND user_id=? AND end_time>=?", (guild_id, user_id, beginning))
    for start, end in cursor.fetchall():
        total += max(0, min(end, now) - max(start, beginning))
    if (guild_id, user_id) in active_users:
        total += max(0, now - max(active_users[(guild_id, user_id)], beginning))
    return total


def format_time(seconds):
    return f"{int(seconds // 3600)} saat {int((seconds % 3600) // 60)} dakika"


async def show_activity(destination, days):
    guild = getattr(destination, "guild", None)
    if guild is None:
        await destination.send("Bu komut sadece sunucularda kullanilabilir.")
        return
    results = []
    for member in guild.members:
        if not member.bot:
            seconds = get_activity(guild.id, member.id, days)
            if seconds > 0:
                results.append((member, seconds))
    results.sort(key=lambda item: item[1], reverse=True)
    embed = discord.Embed(title="Aktiflik Tablosu", description=f"Son {days} gun", color=discord.Color.blue())
    if not results:
        embed.description = f"Son {days} gun icin kayitli aktiflik bulunamadi."
    else:
        embed.add_field(name="En aktif uyeler", value="\n".join(f"**{i}.** {m.display_name} — {format_time(s)}" for i, (m, s) in enumerate(results[:15], 1)), inline=False)
    embed.set_footer(text="Discord cevrimici durumuna gore tahmini hesaplanir.")
    await destination.send(embed=embed)


class ActivityView(discord.ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=60)
        self.author_id = author_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Bu menu sadece komutu kullanana acik.", ephemeral=True)
            return False
        return True

    async def pick(self, interaction, days):
        await interaction.response.defer()
        await show_activity(interaction.channel, days)
        try:
            await interaction.message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    @discord.ui.button(label="7 Gun", style=discord.ButtonStyle.primary)
    async def d7(self, interaction, button):
        await self.pick(interaction, 7)

    @discord.ui.button(label="30 Gun", style=discord.ButtonStyle.primary)
    async def d30(self, interaction, button):
        await self.pick(interaction, 30)

    @discord.ui.button(label="60 Gun", style=discord.ButtonStyle.primary)
    async def d60(self, interaction, button):
        await self.pick(interaction, 60)

    @discord.ui.button(label="90 Gun", style=discord.ButtonStyle.primary)
    async def d90(self, interaction, button):
        await self.pick(interaction, 90)


@bot.command(name="aktiflik")
async def aktiflik(ctx):
    await ctx.send(embed=discord.Embed(title="Aktiflik Suresi", description="Bir aralik sec: 7, 30, 60 veya 90 gun.", color=discord.Color.blue()), view=ActivityView(ctx.author.id))


@bot.command(name="mod")
async def mod(ctx, *, secim: str = None):
    global current_mode
    if not secim:
        await ctx.send("Modlar: normal, komik, ciddi, korkutucu, tartismaci")
    elif secim.lower().strip() in mode_prompts:
        current_mode = secim.lower().strip()
        await ctx.send(f"Mod degistirildi: **{current_mode}**")
    else:
        await ctx.send("Gecerli modlar: normal, komik, ciddi, korkutucu, tartismaci")


@bot.command(name="soru")
async def soru(ctx, *, metin: str = None):
    if not metin:
        await ctx.send("Ornek: `B!soru Bugun sansli miyim`")
        return
    answers = ["He, bunu nereden bildin?", "Abov, ilgincmis.", "Bence tekrar dusun.", "Kaynak: Paklava Akademisi.", "Evet.", "Hayir gibi duruyor.", "Cok supheli."]
    embed = discord.Embed(title="Sihirli Paklava", color=discord.Color.purple())
    embed.add_field(name="Soru", value=metin[:1024], inline=False)
    embed.add_field(name="Cevap", value=random.choice(answers), inline=False)
    await ctx.send(embed=embed)


@bot.command(name="öneri", aliases=["oneri"])
async def oneri(ctx, *, metin: str = None):
    if not metin:
        await ctx.send("Ornek: `B!oneri yeni bir komut eklensin`")
        return
    embed = discord.Embed(title="Yeni Öneri", description=discord.utils.escape_mentions(metin)[:4000], color=discord.Color.gold())
    embed.add_field(name="Gönderen", value=f"{ctx.author} ({ctx.author.id})", inline=False)
    embed.add_field(name="Sunucu", value=ctx.guild.name if ctx.guild else "DM", inline=False)
    delivered = False
    if ctx.guild:
        cursor.execute("SELECT channel_id FROM suggestion_channels WHERE guild_id=?", (ctx.guild.id,))
        channel_row = cursor.fetchone()
        if channel_row:
            suggestion_channel = ctx.guild.get_channel(channel_row[0])
            if suggestion_channel:
                try:
                    posted = await suggestion_channel.send(embed=embed)
                    await posted.add_reaction("👍")
                    await posted.add_reaction("👎")
                    delivered = True
                except (discord.Forbidden, discord.HTTPException) as exc:
                    print(f"ÖNERİ KANALI HATASI: {exc}")
    try:
        owner = bot.get_user(OWNER_ID) or await bot.fetch_user(OWNER_ID)
        await owner.send(embed=embed)
        delivered = True
    except (discord.Forbidden, discord.HTTPException):
        pass
    await ctx.send("Önerin iletildi." if delivered else "Öneri iletilemedi. `B!önerikanal #kanal` ayarını kontrol et veya bot sahibine DM iznini aç.")


# -------------------- TAS KAGIT MAKAS --------------------
class TKMGameView(discord.ui.View):
    def __init__(self, p1, p2):
        super().__init__(timeout=120)
        self.p1, self.p2, self.choices = p1, p2, {}

    async def interaction_check(self, interaction):
        if interaction.user.id not in (self.p1.id, self.p2.id):
            await interaction.response.send_message("Bu oyunda degilsin.", ephemeral=True)
            return False
        return True

    async def choose(self, interaction, choice):
        if interaction.user.id in self.choices:
            await interaction.response.send_message("Zaten secim yaptin.", ephemeral=True)
            return
        self.choices[interaction.user.id] = choice
        if len(self.choices) < 2:
            await interaction.response.send_message("Secimin kaydedildi. Diger oyuncu bekleniyor.", ephemeral=True)
            return
        a, b = self.choices[self.p1.id], self.choices[self.p2.id]
        if a == b:
            result, winner = "Berabere", None
        elif (a, b) in (("Tas", "Makas"), ("Kagit", "Tas"), ("Makas", "Kagit")):
            result, winner = f"{self.p1.mention} kazandi", self.p1
        else:
            result, winner = f"{self.p2.mention} kazandi", self.p2
        if winner:
            reward = round(100 * (1 + get_duel_bonus(0, winner.id)))
            add_balance(0, winner.id, reward)
            result += f"\nOdul: **{reward} paklava**"
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=f"**Tas Kagit Makas Sonucu**\n{self.p1.mention}: **{a}**\n{self.p2.mention}: **{b}**\n\n{result}", view=self)
        self.stop()

    @discord.ui.button(label="Tas", style=discord.ButtonStyle.primary)
    async def rock(self, interaction, button):
        await self.choose(interaction, "Tas")

    @discord.ui.button(label="Kagit", style=discord.ButtonStyle.success)
    async def paper(self, interaction, button):
        await self.choose(interaction, "Kagit")

    @discord.ui.button(label="Makas", style=discord.ButtonStyle.danger)
    async def scissors(self, interaction, button):
        await self.choose(interaction, "Makas")


class TKMInviteView(discord.ui.View):
    def __init__(self, challenger, opponent):
        super().__init__(timeout=60)
        self.challenger, self.opponent = challenger, opponent

    @discord.ui.button(label="Kabul Et", style=discord.ButtonStyle.success)
    async def accept(self, interaction, button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("Bu daveti sadece etiketlenen oyuncu kabul edebilir.", ephemeral=True)
            return
        await interaction.response.edit_message(content=f"{self.challenger.mention} ve {self.opponent.mention}\nIkiniz de seciminizi yapin.", view=TKMGameView(self.challenger, self.opponent))
        self.stop()

    @discord.ui.button(label="Reddet", style=discord.ButtonStyle.danger)
    async def decline(self, interaction, button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("Bu daveti sadece etiketlenen oyuncu reddedebilir.", ephemeral=True)
            return
        await interaction.response.edit_message(content=f"{self.opponent.mention} daveti reddetti.", view=None)
        self.stop()


@bot.command(name="tkm")
async def tkm(ctx, oyuncu: discord.Member = None):
    if oyuncu is None or oyuncu.bot or oyuncu.id == ctx.author.id:
        await ctx.send("Bir oyuncuyu etiketle. Botlarla veya kendinle oynayamazsin.")
        return
    await ctx.send(f"{oyuncu.mention}\n{ctx.author.mention} seninle Tas Kagit Makas oynamak istiyor.", view=TKMInviteView(ctx.author, oyuncu))


# -------------------- 1VS1 DUELLO --------------------
DUEL_MAX_HP = 500
DUEL_ULTRA_COST = 100
active_duels = {}


def make_hp_bar(hp, max_hp=DUEL_MAX_HP):
    filled = max(0, min(10, round(hp / max_hp * 10)))
    return "🟩" * filled + "⬛" * (10 - filled)


class DuelGameView(discord.ui.View):
    def __init__(self, p1, p2, channel_id):
        super().__init__(timeout=300)
        self.channel_id, self.order, self.turn_index = channel_id, [p1.id, p2.id], 0
        self.players = {
            p1.id: {"member": p1, "hp": DUEL_MAX_HP, "power": 0, "shield": False},
            p2.id: {"member": p2, "hp": DUEL_MAX_HP, "power": 0, "shield": False},
        }

    @property
    def current_player_id(self):
        return self.order[self.turn_index]

    def other_id(self, user_id):
        return self.order[1] if user_id == self.order[0] else self.order[0]

    async def interaction_check(self, interaction):
        if interaction.user.id not in self.players:
            await interaction.response.send_message("Bu duelloda degilsin.", ephemeral=True)
            return False
        if interaction.user.id != self.current_player_id:
            await interaction.response.send_message("Sira sende degil.", ephemeral=True)
            return False
        return True

    def build_embed(self, log_line=""):
        embed = discord.Embed(title="1vs1 Duello", description=log_line or None, color=discord.Color.red())
        for uid in self.order:
            p = self.players[uid]
            value = f"Can: {p['hp']}/{DUEL_MAX_HP}\n{make_hp_bar(p['hp'])}\nGuc: {p['power']}/{DUEL_ULTRA_COST}"
            if p["shield"]:
                value += "\nSavunma kalkani aktif"
            embed.add_field(name=p["member"].display_name, value=value, inline=True)
        embed.set_footer(text=f"Sira: {self.players[self.current_player_id]['member'].display_name}")
        return embed

    async def end_duel(self, interaction, winner_id, loser_id, reason):
        active_duels.pop(self.channel_id, None)
        for item in self.children:
            item.disabled = True
        reward = round(random.randint(50, 150) * (1 + get_duel_bonus(0, winner_id)))
        add_balance(0, winner_id, reward)
        embed = discord.Embed(title="Duello Bitti", description=reason, color=discord.Color.gold())
        embed.add_field(name="Kazanan", value=self.players[winner_id]["member"].mention)
        embed.add_field(name="Odul", value=f"+{reward} paklava", inline=False)
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    async def resolve(self, interaction, action):
        aid = interaction.user.id
        tid = self.other_id(aid)
        actor, target, log = self.players[aid], self.players[tid], ""
        if action == "terlik":
            damage = random.randint(50, 99)
            if target["shield"]:
                damage = round(damage * 0.4)
                target["shield"] = False
            target["hp"] = max(0, target["hp"] - damage)
            log = f"{actor['member'].mention} {damage} hasar verdi."
        elif action == "topla":
            gain = random.randint(30, 60)
            actor["power"] = min(DUEL_ULTRA_COST, actor["power"] + gain)
            log = f"{actor['member'].mention} guc topladi (+{gain})."
        elif action == "ultra":
            if actor["power"] < DUEL_ULTRA_COST:
                log = "Ultra saldiri icin once yeterli guc toplamalısin."
            else:
                damage = random.randint(200, 300)
                if target["shield"]:
                    damage = round(damage * 0.4)
                    target["shield"] = False
                actor["power"] = 0
                target["hp"] = max(0, target["hp"] - damage)
                log = f"Ultra saldiri {damage} hasar verdi."
        elif action == "can":
            old = actor["hp"]
            actor["hp"] = min(DUEL_MAX_HP, actor["hp"] + random.randint(30, 100))
            log = f"{actor['member'].mention} {actor['hp'] - old} can yeniledi."
        elif action == "seddi":
            actor["shield"] = True
            log = f"{actor['member'].mention} savunma kalkani kurdu."
        elif action == "pes":
            await self.end_duel(interaction, tid, aid, f"{actor['member'].mention} pes etti.")
            return
        if target["hp"] <= 0:
            await self.end_duel(interaction, aid, tid, f"{log}\n{target['member'].mention} yenildi.")
            return
        self.turn_index = 1 - self.turn_index
        await interaction.response.edit_message(embed=self.build_embed(log), view=self)

    async def on_timeout(self):
        active_duels.pop(self.channel_id, None)
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="Anne Terligi", style=discord.ButtonStyle.danger, row=0)
    async def terlik(self, interaction, button):
        await self.resolve(interaction, "terlik")

    @discord.ui.button(label="Guc Topla", style=discord.ButtonStyle.secondary, row=0)
    async def topla(self, interaction, button):
        await self.resolve(interaction, "topla")

    @discord.ui.button(label="Ultra Saldiri", style=discord.ButtonStyle.danger, row=0)
    async def ultra(self, interaction, button):
        await self.resolve(interaction, "ultra")

    @discord.ui.button(label="Can Yenile", style=discord.ButtonStyle.success, row=1)
    async def can(self, interaction, button):
        await self.resolve(interaction, "can")

    @discord.ui.button(label="Savunma Kalkani", style=discord.ButtonStyle.primary, row=1)
    async def seddi(self, interaction, button):
        await self.resolve(interaction, "seddi")

    @discord.ui.button(label="Pes Et", style=discord.ButtonStyle.secondary, row=1)
    async def pes(self, interaction, button):
        await self.resolve(interaction, "pes")


class DuelInviteView(discord.ui.View):
    def __init__(self, challenger, opponent):
        super().__init__(timeout=30)
        self.challenger, self.opponent = challenger, opponent

    @discord.ui.button(label="Kabul Et", style=discord.ButtonStyle.success)
    async def accept(self, interaction, button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("Bu daveti sadece etiketlenen oyuncu kabul edebilir.", ephemeral=True)
            return
        if interaction.channel.id in active_duels:
            await interaction.response.send_message("Bu kanalda zaten bir duello var.", ephemeral=True)
            return
        game = DuelGameView(self.challenger, self.opponent, interaction.channel.id)
        active_duels[interaction.channel.id] = game
        await interaction.response.edit_message(content=None, embed=game.build_embed("Duello basladi."), view=game)
        self.stop()

    @discord.ui.button(label="Reddet", style=discord.ButtonStyle.danger)
    async def decline(self, interaction, button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("Bu daveti sadece etiketlenen oyuncu reddedebilir.", ephemeral=True)
            return
        await interaction.response.edit_message(content=f"{self.opponent.mention} daveti reddetti.", view=None)
        self.stop()


@bot.command(name="1vs1")
async def duel_command(ctx, oyuncu: discord.Member = None):
    if oyuncu is None or oyuncu.bot or oyuncu.id == ctx.author.id:
        await ctx.send("Bir oyuncuyu etiketle. Botlarla veya kendinle duello yapamazsin.")
        return
    if ctx.channel.id in active_duels:
        await ctx.send("Bu kanalda zaten aktif bir duello var.")
        return
    await ctx.send(f"{oyuncu.mention}\n{ctx.author.mention} seni duelloya davet ediyor.", view=DuelInviteView(ctx.author, oyuncu))


# -------------------- SHIP KOMUTU: HAREKETLI GIF --------------------
def make_ship_gif(name1, name2, percent):
    width, height = 720, 360
    frames = []
    try:
        font_big = ImageFont.truetype("DejaVuSans-Bold.ttf", 31)
        font_mid = ImageFont.truetype("DejaVuSans.ttf", 23)
        font_small = ImageFont.truetype("DejaVuSans-Bold.ttf", 28)
    except OSError:
        font_big = ImageFont.load_default()
        font_mid = ImageFont.load_default()
        font_small = ImageFont.load_default()

    def centered(draw, y, text, font, fill):
        box = draw.textbbox((0, 0), text, font=font)
        draw.text(((width - (box[2] - box[0])) / 2, y), text, font=font, fill=fill)

    for frame_index in range(20):
        pulse = (frame_index % 10) / 9
        scale = 1.0 + (0.12 * pulse if frame_index < 10 else 0.12 * (1 - pulse))
        image = Image.new("RGB", (width, height), (24, 18, 48))
        pixels = image.load()
        for y in range(height):
            for x in range(width):
                t = x / width
                pixels[x, y] = (int(35 + 40*t), int(18 + 8*(1-t)), int(75 + 50*(1-t)))
        draw = ImageDraw.Draw(image)
        # Decorative stars
        star_rng = random.Random(frame_index + percent)
        for _ in range(38):
            x, y = star_rng.randint(12, width-12), star_rng.randint(12, height-12)
            r = star_rng.choice((1, 2, 3))
            draw.ellipse((x-r, y-r, x+r, y+r), fill=(255, 205, 235))
        centered(draw, 28, "SHIP METER", font_big, (255, 220, 245))
        centered(draw, 92, name1[:22], font_mid, (255, 255, 255))
        centered(draw, 132, "♥", font_big, (255, 70 + int(40*pulse), 135))
        centered(draw, 185, name2[:22], font_mid, (255, 255, 255))
        # Progress bar
        left, top, right, bottom = 110, 245, 610, 277
        draw.rounded_rectangle((left, top, right, bottom), radius=15, fill=(75, 65, 100), outline=(255, 220, 245), width=2)
        fill_right = left + int((right-left) * percent / 100)
        if fill_right > left:
            draw.rounded_rectangle((left, top, fill_right, bottom), radius=15, fill=(255, 75, 150))
        centered(draw, 294, f"{percent}% UYUM", font_small, (255, 235, 248))
        frames.append(image)
    output = io.BytesIO()
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:], duration=90, loop=0, optimize=True)
    output.seek(0)
    return output


_ship_last_percent = {}


@bot.command(name="ship")
async def ship(ctx, kisi1: discord.Member = None, kisi2: discord.Member = None):
    if kisi1 is None and kisi2 is None:
        await ctx.send("Kullanim: `B!ship @kişi1 @kişi2`\nTek kişi yazarsan seni onunla eşleştiririm: `B!ship @kişi`")
        return
    if kisi1 is not None and kisi2 is None:
        kisi2 = kisi1
        kisi1 = ctx.author
    elif kisi1 is None and kisi2 is not None:
        kisi1 = ctx.author
    if kisi1.bot or kisi2.bot:
        await ctx.send("Botları ship listesine alamıyorum abi.")
        return
    # Her kullanimda ayni ikili icin onceki yuzdeden farkli sonuc ver.
    low, high = sorted((kisi1.id, kisi2.id))
    pair_key = (low, high)
    percent = random.randint(1, 100)
    if _ship_last_percent.get(pair_key) == percent:
        percent = (percent % 100) + 1
    _ship_last_percent[pair_key] = percent
    try:
        gif = make_ship_gif(kisi1.display_name, kisi2.display_name, percent)
        await ctx.send(
            content=f"💞 **{kisi1.display_name} + {kisi2.display_name}**\nUyum oranı: **%{percent}**",
            file=discord.File(gif, filename="ship.gif"),
        )
    except (discord.HTTPException, OSError, ValueError) as exc:
        print(f"SHIP GIF HATASI: {type(exc).__name__}: {exc}")
        await ctx.send("Ship GIF'i oluşturulamadı. Render loglarını kontrol et.")


@bot.command(name="eğlence", aliases=["eglence"])
async def eglence(ctx):
    embed = discord.Embed(title="Baklava1923 Eğlence Merkezi", description="Mini oyunlar, troll komutları ve Paklava ödülleri", color=discord.Color.magenta())
    embed.add_field(name="Oyunlar", value="`B!1vs1 @oyuncu` · `B!tkm @oyuncu` · `B!yazitura <yazi/tura>`\\n`B!zar [yüz]` · `B!sayıtahmin` · `B!tahmin <sayı>` · `B!refleks`", inline=False)
    embed.add_field(name="Troll ve etkileşim", value="`B!meme` · `B!şaka` · `B!tersle @oyuncu` · `B!roast @oyuncu` · `B!ego`\\n`B!ship @kişi1 @kişi2` · `B!evlen @üye` · `B!evlilik` · `B!boşan` · `B!ısmarla` · `B!çocukyap` · `B!çocuklar`\\n`B!uyum @oyuncu` · `B!kapışma @oyuncu` · `B!iltifat @oyuncu` · `B!kimdaha @oyuncu`\\n`B!doğruluk` · `B!cesaret` · `B!kader` · `B!bahane` · `B!itiraf <metin>` · `B!soru <metin>`", inline=False)
    embed.add_field(name="Paklava ödülleri", value="`B!günlük` · `B!soygun` · `B!hediye @oyuncu <miktar>` · `B!bakiye` · `B!market` · `B!envanter`\n`B!balık` · `B!balıklar` · `B!balıksat [tür] [adet]` · `B!olta` · `B!oltayükselt`", inline=False)
    await ctx.send(embed=embed)


@bot.command(name="zar")
async def zar(ctx, yuz: int = 6):
    if not 2 <= yuz <= 100:
        await ctx.send("Zar yüzü 2 ile 100 arasında olmalı. Örnek: `B!zar 20`")
        return
    await ctx.send(f"🎲 {ctx.author.mention} zar attı: **{random.randint(1, yuz)}** / {yuz}")


@bot.command(name="uyum")
async def uyum(ctx, oyuncu: discord.Member = None):
    if oyuncu is None:
        await ctx.send("Kullanım: `B!uyum @oyuncu`")
        return
    if oyuncu.bot:
        await ctx.send("Botlarla uyum ölçülmez, onların kalbi kodlardan oluşuyor.")
        return
    percent = 100 if oyuncu.id == ctx.author.id else (ctx.author.id * 31 + oyuncu.id * 17) % 101
    await ctx.send(f"🤝 {ctx.author.mention} ve {oyuncu.mention} arkadaşlık uyumu: **%{percent}**")


@bot.command(name="bahane")
async def bahane(ctx):
    lines = ["İnternetim tam kritik anda gitti", "Klavye kendi kendine bastı", "Kedim masaya çıktı", "Yanlışlıkla başka pencereye tıkladım", "Oyun beni kıskandı", "Planım fazla zekiceydi, kimse anlamadı", "Pingim uzaya çıktı"]
    await ctx.send(f"🗿 {ctx.author.mention} için hazır bahane: **{random.choice(lines)}**")


@bot.command(name="kapışma", aliases=["kapisma"])
async def kapisma(ctx, oyuncu: discord.Member = None):
    if oyuncu is None:
        await ctx.send("Kullanım: `B!kapışma @oyuncu`")
        return
    if oyuncu.id == ctx.author.id:
        await ctx.send("Kendi kendine kapışamazsın abi.")
        return
    kazanan = random.choice([ctx.author, oyuncu])
    await ctx.send(f"⚔️ {ctx.author.mention} ile {oyuncu.mention} kapıştı. Kazanan: **{kazanan.display_name}**")


@bot.command(name="itiraf")
async def itiraf(ctx, *, metin: str = None):
    if not metin:
        await ctx.send("Kullanım: `B!itiraf <itirafın>`")
        return
    # İtiraf komutu mevcut kanalda paylaşır; özel/anonim olduğu iddia edilmez.
    await ctx.send(f"💬 **İtiraf** — {ctx.author.mention}: {discord.utils.escape_mentions(metin)[:1500]}")


@bot.command(name="ekonomi")
async def ekonomi(ctx):
    embed = discord.Embed(title="Global Paklava Ekonomisi 3.7", color=discord.Color.gold())
    embed.description = "Bakiyen tüm sunucularda ortaktır. Cüzdanını, bankanı, mesleğini ve işletmeni yönet."
    embed.add_field(name="Bakiye ve banka", value="`B!bakiye [@oyuncu]` · `B!banka`\n`B!yatır <miktar>` · `B!çek <miktar>`", inline=False)
    embed.add_field(name="Kazanç", value="`B!günlük` · `B!haftalık` · `B!gorev`\n`B!meslekler` · `B!meslek <isim>` · `B!çalış`\n`B!balık` · `B!balıklar` · `B!balıksat [tür] [adet]` · `B!olta` · `B!oltayükselt`", inline=False)
    embed.add_field(name="İşletme", value="`B!işletmeler` · `B!işletmekur <isim>`\n`B!işletme` · `B!işletmetopla`", inline=False)
    embed.add_field(name="Market ve ticaret", value="`B!market` · `B!satinal <eşya>` · `B!envanter`\n`B!hediye @oyuncu <miktar>` · `B!ticaret @oyuncu <eşya>`\n`B!ekonomisiralamasi`", inline=False)
    await ctx.send(embed=embed)



# -------------------- 3.7: EVLILIK / KARI-KOCA SISTEMI --------------------
CHILD_COST = 1000          # Cocuk yapmanin paklava bedeli (teklifi yapan oder)
CHILD_LIMIT = 6            # Bir evlilikte en fazla cocuk sayisi
CHILD_COOLDOWN = 3600      # Ayni cift icin bekleme suresi (saniye)

CHILD_NAMES_GIRL = ["Elif", "Zeynep", "Defne", "Asya", "Ecrin", "Nehir", "Azra", "Meryem", "Duru", "Ela", "Paklava", "Baklavacık"]
CHILD_NAMES_BOY = ["Yusuf", "Eymen", "Mert", "Alparslan", "Kerem", "Ömer", "Atlas", "Poyraz", "Çınar", "Efe", "Fıstık", "Kadayıf"]

ISMARLA_ITEMS = {
    "cay": {"ad": "Çay", "emoji": "🍵", "fiyat": 25, "sevgi": (1, 2)},
    "kahve": {"ad": "Türk Kahvesi", "emoji": "☕", "fiyat": 60, "sevgi": (1, 2)},
    "paklava": {"ad": "Bir Tepsi Paklava", "emoji": "🍯", "fiyat": 250, "sevgi": (2, 3)},
    "kunefe": {"ad": "Künefe", "emoji": "🧀", "fiyat": 400, "sevgi": (2, 4)},
    "cicek": {"ad": "Çiçek Buketi", "emoji": "💐", "fiyat": 600, "sevgi": (3, 4)},
    "yemek": {"ad": "Akşam Yemeği", "emoji": "🍽️", "fiyat": 1000, "sevgi": (3, 5)},
    "tatil": {"ad": "Balayı Tatili", "emoji": "🏖️", "fiyat": 6000, "sevgi": (6, 10)},
}
ISMARLA_ALIASES = {
    "baklava": "paklava", "tepsi": "paklava", "cayi": "cay", "turkkahvesi": "kahve",
    "aksamyemegi": "yemek", "aksam": "yemek", "buket": "cicek", "balayi": "tatil",
}


def _fold_tr(text):
    """Turkce karakterleri ve bosluklari sadelestirip kucuk harfe cevirir (arama icin)."""
    text = str(text).replace("İ", "i").replace("I", "ı").lower()
    for src, dst in (("ç", "c"), ("ş", "s"), ("ğ", "g"), ("ı", "i"), ("ö", "o"), ("ü", "u")):
        text = text.replace(src, dst)
    return text.replace(" ", "")


def get_marriage(guild_id, user_id):
    cursor.execute(
        "SELECT partner_id,love_percent,married_at,role FROM marriages WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {"partner_id": int(row[0]), "love": int(row[1]), "married_at": row[2], "role": row[3] or ""}


def role_title(role):
    return {"koca": "Koca", "kari": "Karı"}.get(role, "Eş")


def role_emoji(role):
    return {"koca": "🤵", "kari": "👰"}.get(role, "💍")


def parent_title(role):
    return {"koca": "Baba", "kari": "Anne"}.get(role, "Ebeveyn")


def count_children(guild_id, user_a, user_b):
    cursor.execute(
        "SELECT COUNT(*) FROM children WHERE guild_id=? AND ((parent1_id=? AND parent2_id=?) OR (parent1_id=? AND parent2_id=?))",
        (guild_id, user_a, user_b, user_b, user_a),
    )
    return int(cursor.fetchone()[0])


def child_cooldown_key(user_a, user_b):
    return min(user_a, user_b), f"child:{max(user_a, user_b)}"


def make_marriage_gif(koca_name, kari_name, love_koca, love_kari):
    """Create an original looping wedding GIF using Pillow; no external GIF is required."""
    width, height = 640, 300
    frames = []
    try:
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 34)
        name_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 23)
        small_font = ImageFont.truetype("DejaVuSans.ttf", 19)
    except OSError:
        title_font = name_font = small_font = ImageFont.load_default()

    def centered(draw, y, text, font, fill):
        box = draw.textbbox((0, 0), text, font=font)
        draw.text(((width - (box[2] - box[0])) / 2, y), text, font=font, fill=fill)

    for frame_no in range(20):
        image = Image.new("RGB", (width, height), (31, 20, 47))
        draw = ImageDraw.Draw(image)
        # Original animated heart confetti.
        for i in range(13):
            x = (i * 53 + frame_no * (2 + i % 3)) % width
            y = (i * 37 + frame_no * (3 + i % 2)) % 285
            color = (255, 105 + (i * 7) % 100, 170 + (i * 5) % 70)
            draw.ellipse((x - 7, y - 5, x + 2, y + 4), fill=color)
            draw.ellipse((x - 1, y - 5, x + 8, y + 4), fill=color)
            draw.polygon([(x - 7, y), (x + 8, y), (x, y + 11)], fill=color)
        centered(draw, 20, "EVLENDILER!", title_font, (255, 230, 245))
        centered(draw, 78, f"KOCA: {koca_name[:20]}", name_font, (150, 210, 255))
        centered(draw, 112, f"KARI: {kari_name[:20]}", name_font, (255, 170, 210))
        centered(draw, 158, "KARŞILIKLI SEVGİ", small_font, (255, 190, 220))
        centered(draw, 190, f"Koca: %{love_koca}   |   Karı: %{love_kari}", small_font, (240, 225, 255))
        # Large pulsing heart, drawn from simple shapes.
        pulse = 1 + (frame_no % 5)
        cx, cy = 320, 265
        r = 8 + pulse
        draw.ellipse((cx - r - 12, cy - r, cx - 2, cy + r - 10), fill=(255, 70, 135))
        draw.ellipse((cx + 2, cy - r, cx + r + 12, cy + r - 10), fill=(255, 70, 135))
        draw.polygon([(cx - r - 12, cy - 6), (cx + r + 12, cy - 6), (cx, cy + r + 12)], fill=(255, 70, 135))
        frames.append(image)
    output = io.BytesIO()
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:], duration=110, loop=0, optimize=True)
    output.seek(0)
    return output


class MarriageProposalView(discord.ui.View):
    def __init__(self, proposer, target):
        super().__init__(timeout=180)
        self.proposer = proposer
        self.target = target

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.target.id:
            await interaction.response.send_message("Bu evlilik teklifini sadece teklif edilen kişi yanıtlayabilir.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Teklifi kabul et", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Bu işlem sadece sunucuda kullanılabilir.", ephemeral=True)
            return
        cursor.execute(
            "SELECT user_id FROM marriages WHERE guild_id=? AND user_id IN (?,?) LIMIT 1",
            (guild.id, self.proposer.id, self.target.id),
        )
        occupied = cursor.fetchone()
        cursor.execute(
            "SELECT user_id FROM marriages WHERE guild_id=? AND partner_id IN (?,?) LIMIT 1",
            (guild.id, self.proposer.id, self.target.id),
        )
        occupied_partner = cursor.fetchone()
        if occupied or occupied_partner:
            await interaction.response.edit_message(content="Bu iki kişiden en az biri zaten evli. Önce mevcut evlilik durumunu kontrol edin.", view=None)
            self.stop()
            return
        # Kimin koca, kimin karı olacağı rastgele belirlenir.
        proposer_role, target_role = random.choice([("koca", "kari"), ("kari", "koca")])
        love_a = random.randint(55, 100)  # teklif eden -> eşine
        love_b = random.randint(55, 100)  # teklif edilen -> eşine
        married_at = time.time()
        try:
            cursor.executemany(
                "INSERT INTO marriages (guild_id,user_id,partner_id,married_at,love_percent,role) VALUES (?,?,?,?,?,?)",
                [
                    (guild.id, self.proposer.id, self.target.id, married_at, love_a, proposer_role),
                    (guild.id, self.target.id, self.proposer.id, married_at, love_b, target_role),
                ],
            )
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            await interaction.response.edit_message(content="Evlilik kaydı çakıştı. İki kişinin evlilik durumunu yeniden kontrol edin.", view=None)
            self.stop()
            return
        if proposer_role == "koca":
            koca, kari, love_koca, love_kari = self.proposer, self.target, love_a, love_b
        else:
            koca, kari, love_koca, love_kari = self.target, self.proposer, love_b, love_a
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content=f"{self.proposer.mention} ve {self.target.mention} artık evli!", view=None)
        try:
            gif = make_marriage_gif(koca.display_name, kari.display_name, love_koca, love_kari)
            await interaction.followup.send(
                content=(f"**Karı-koca oldular!** 💍\n"
                         f"🤵 **Koca:** {koca.mention} — eşine **%{love_koca}** sevgi\n"
                         f"👰 **Karı:** {kari.mention} — eşine **%{love_kari}** sevgi\n"
                         "Rolleri kura belirledi. Birbirinize `B!ısmarla` ile paklavayla bir şey ısmarlayabilir, "
                         "`B!çocukyap` ile çocuk sahibi olabilirsiniz."),
                file=discord.File(gif, filename="baklava_evlilik.gif"),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.HTTPException, OSError, ValueError) as exc:
            print(f"EVLILIK GIF HATASI: {type(exc).__name__}: {exc}")
            await interaction.followup.send(
                f"Evlilik kaydedildi ama GIF oluşturulamadı. 🤵 Koca: {koca.mention} · 👰 Karı: {kari.mention}\n`B!evlilik` ile durumu görebilirsiniz.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        self.stop()

    @discord.ui.button(label="Reddet", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content=f"{self.target.mention} evlilik teklifini reddetti.", view=None)
        self.stop()


@bot.command(name="evlen")
@commands.guild_only()
async def evlen(ctx, oyuncu: discord.Member = None):
    if oyuncu is None:
        await ctx.send("Kullanım: `B!evlen @üye` — teklif butonla kabul edilirse evlilik kaydedilir. Koca/karı rolleri rastgele belirlenir.")
        return
    if oyuncu.bot or oyuncu.id == ctx.author.id:
        await ctx.send("Kendinle veya botla evlenemezsin.")
        return
    cursor.execute(
        "SELECT user_id FROM marriages WHERE guild_id=? AND user_id IN (?,?)",
        (ctx.guild.id, ctx.author.id, oyuncu.id),
    )
    rows = cursor.fetchall()
    cursor.execute(
        "SELECT user_id FROM marriages WHERE guild_id=? AND partner_id IN (?,?)",
        (ctx.guild.id, ctx.author.id, oyuncu.id),
    )
    rows += cursor.fetchall()
    if rows:
        await ctx.send("İkinizden biri zaten evli. Durumu `B!evlilik` ile kontrol edin.")
        return
    await ctx.send(
        f"{oyuncu.mention}, {ctx.author.mention} sana evlilik teklifi ediyor. Kabul etmek için aşağıdaki butona bas.",
        view=MarriageProposalView(ctx.author, oyuncu),
        allowed_mentions=discord.AllowedMentions(users=[oyuncu]),
    )


@bot.command(name="evlilik")
@commands.guild_only()
async def evlilik(ctx, oyuncu: discord.Member = None):
    member = oyuncu or ctx.author
    info = get_marriage(ctx.guild.id, member.id)
    if not info:
        await ctx.send(f"{member.mention} şu anda evli değil. Teklif için `B!evlen @üye` yaz.", allowed_mentions=discord.AllowedMentions.none())
        return
    partner_id = info["partner_id"]
    partner = ctx.guild.get_member(partner_id)
    reverse = get_marriage(ctx.guild.id, partner_id)
    partner_love = reverse["love"] if reverse else 0
    partner_role = reverse["role"] if reverse else ""
    partner_name = partner.mention if partner else f"<@{partner_id}>"
    embed = discord.Embed(title="Evlilik Durumu", description=f"{member.mention} ❤ {partner_name}", color=discord.Color.from_rgb(235, 105, 160))
    embed.add_field(name="Roller", value=f"{role_emoji(info['role'])} {member.mention} → **{role_title(info['role'])}**\n{role_emoji(partner_role)} {partner_name} → **{role_title(partner_role)}**", inline=False)
    embed.add_field(name=f"{member.display_name} → eşine", value=f"**%{info['love']}** sevgi", inline=True)
    embed.add_field(name="Eşi → ona", value=f"**%{partner_love}** sevgi", inline=True)
    embed.add_field(name="Çocuklar", value=f"**{count_children(ctx.guild.id, member.id, partner_id)}/{CHILD_LIMIT}** · `B!çocuklar`", inline=True)
    embed.add_field(name="Evlilik tarihi", value=discord.utils.format_dt(datetime.fromtimestamp(info["married_at"], timezone.utc), style="D"), inline=False)
    embed.set_footer(text="Eşine paklavayla bir şey ısmarlamak için B!ısmarla · Çocuk için B!çocukyap")
    await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="boşan", aliases=["bosan"])
@commands.guild_only()
async def bosan(ctx):
    info = get_marriage(ctx.guild.id, ctx.author.id)
    if not info:
        await ctx.send("Kayıtlı bir evliliğin görünmüyor.")
        return
    partner_id = info["partner_id"]
    cursor.execute("DELETE FROM marriages WHERE guild_id=? AND (user_id=? OR user_id=?)", (ctx.guild.id, ctx.author.id, partner_id))
    cursor.execute(
        "DELETE FROM children WHERE guild_id=? AND ((parent1_id=? AND parent2_id=?) OR (parent1_id=? AND parent2_id=?))",
        (ctx.guild.id, ctx.author.id, partner_id, partner_id, ctx.author.id),
    )
    db.commit()
    await ctx.send("Evlilik kaydı silindi. İki tarafın evlilik durumu ve ortak çocuk kayıtları da güncellendi.")


# -------------------- 3.7: ESINE PAKLAVAYLA ISMARLAMA --------------------
@bot.command(name="ısmarla", aliases=["ismarla", "ısmarlama", "ismarlama"])
@commands.guild_only()
async def ismarla(ctx, *, urun: str = None):
    """Eşine paklava harcayarak bir şey ısmarla; eşinin sana olan sevgisi artar."""
    if urun is None:
        lines = [
            f"{item['emoji']} **{item['ad']}** — {item['fiyat']:,} paklava · Komut: `B!ısmarla {key}`".replace(",", ".")
            for key, item in ISMARLA_ITEMS.items()
        ]
        embed = discord.Embed(title="Eşine Ismarla 💝", description="\n".join(lines), color=discord.Color.from_rgb(235, 105, 160))
        embed.set_footer(text="Sadece evliyken, eşine ısmarlayabilirsin. Ismarlanan kişinin sevgisi artar.")
        await ctx.send(embed=embed)
        return
    info = get_marriage(ctx.guild.id, ctx.author.id)
    if not info:
        await ctx.send("Ismarlamak için önce evli olman lazım. `B!evlen @üye` ile teklif edebilirsin.")
        return
    key = _fold_tr(urun)
    key = ISMARLA_ALIASES.get(key, key)
    item = ISMARLA_ITEMS.get(key)
    if item is None:
        await ctx.send("Böyle bir ürün yok. Menü için `B!ısmarla` yaz.")
        return
    balance = get_balance(0, ctx.author.id)
    if ctx.author.id != OWNER_ID and balance < item["fiyat"]:
        await ctx.send(f"Yeterli paklavan yok. Gereken: **{item['fiyat']:,}**. Bakiyen: **{balance_label(ctx.author.id, balance)}**".replace(",", "."))
        return
    if ctx.author.id != OWNER_ID:
        add_balance(0, ctx.author.id, -item["fiyat"])
    partner_id = info["partner_id"]
    gain = random.randint(*item["sevgi"])
    cursor.execute(
        "UPDATE marriages SET love_percent=MIN(100, love_percent+?) WHERE guild_id=? AND user_id=?",
        (gain, ctx.guild.id, partner_id),
    )
    db.commit()
    reverse = get_marriage(ctx.guild.id, partner_id)
    new_love = reverse["love"] if reverse else 0
    partner = ctx.guild.get_member(partner_id)
    partner_mention = partner.mention if partner else f"<@{partner_id}>"
    spouse_word = "karısına" if info["role"] == "koca" else "kocasına"
    await ctx.send(
        f"{role_emoji(info['role'])} {ctx.author.mention}, {spouse_word} ({partner_mention}) **{item['ad']}** {item['emoji']} ısmarladı ve "
        f"**{item['fiyat']:,} paklava** harcadı!\n".replace(",", ".")
        + f"💞 Eşinin sana olan sevgisi **+{gain}** arttı → **%{new_love}**",
        allowed_mentions=discord.AllowedMentions(users=[partner]) if partner else discord.AllowedMentions.none(),
    )


# -------------------- 3.7: COCUK YAPMA --------------------
class ChildRequestView(discord.ui.View):
    def __init__(self, initiator, partner, name):
        super().__init__(timeout=180)
        self.initiator = initiator
        self.partner = partner
        self.baby_name = name

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.partner.id:
            await interaction.response.send_message("Bu teklife sadece eşin yanıt verebilir.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Evet, çocuk yapalım", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Bu işlem sadece sunucuda kullanılabilir.", ephemeral=True)
            return
        info = get_marriage(guild.id, self.initiator.id)
        if not info or info["partner_id"] != self.partner.id:
            await interaction.response.edit_message(content="Artık bu kişiyle evli görünmüyorsunuz.", view=None)
            self.stop()
            return
        if count_children(guild.id, self.initiator.id, self.partner.id) >= CHILD_LIMIT:
            await interaction.response.edit_message(content=f"Bu evlilikte zaten en fazla çocuk sayısına (**{CHILD_LIMIT}**) ulaştınız.", view=None)
            self.stop()
            return
        cd_user, cd_action = child_cooldown_key(self.initiator.id, self.partner.id)
        if fun_cooldown_remaining(cd_user, cd_action, CHILD_COOLDOWN):
            await interaction.response.edit_message(content="Çocuk için biraz beklemeniz gerekiyor, yeni bir teklif yapın.", view=None)
            self.stop()
            return
        balance = get_balance(0, self.initiator.id)
        if self.initiator.id != OWNER_ID and balance < CHILD_COST:
            await interaction.response.edit_message(content=f"{self.initiator.mention} artık yeterli paklavaya sahip değil. Gereken: **{CHILD_COST:,}**".replace(",", "."), view=None)
            self.stop()
            return
        if self.initiator.id != OWNER_ID:
            add_balance(0, self.initiator.id, -CHILD_COST)
        gender = random.choice(["Kız", "Erkek"])
        name = self.baby_name or random.choice(CHILD_NAMES_GIRL if gender == "Kız" else CHILD_NAMES_BOY)
        cursor.execute(
            "INSERT INTO children (guild_id,parent1_id,parent2_id,name,gender,born_at) VALUES (?,?,?,?,?,?)",
            (guild.id, self.initiator.id, self.partner.id, name, gender, time.time()),
        )
        db.commit()
        set_fun_cooldown(cd_user, cd_action)
        total = count_children(guild.id, self.initiator.id, self.partner.id)
        partner_info = get_marriage(guild.id, self.partner.id)
        init_role = info["role"]
        part_role = partner_info["role"] if partner_info else ""
        emoji = "👧" if gender == "Kız" else "👦"
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=(f"🍼 **Tebrikler!** {self.initiator.mention} ve {self.partner.mention} bir **{gender.lower()} çocuk** sahibi oldu!\n"
                     f"{emoji} Bebeğin adı: **{name}**\n"
                     f"{role_emoji(init_role)} {parent_title(init_role)}: {self.initiator.mention} · {role_emoji(part_role)} {parent_title(part_role)}: {self.partner.mention}\n"
                     f"👨‍👩‍👧 Toplam çocuk: **{total}/{CHILD_LIMIT}** · Masraf: **{CHILD_COST:,} paklava** ({self.initiator.display_name} ödedi)").replace(",", "."),
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.stop()

    @discord.ui.button(label="Şimdi değil", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content=f"{self.partner.mention} şimdilik çocuk istemiyor. 😔", view=None, allowed_mentions=discord.AllowedMentions.none())
        self.stop()


@bot.command(name="çocukyap", aliases=["cocukyap", "çocuk", "cocuk", "bebekyap"])
@commands.guild_only()
async def cocukyap(ctx, *, isim: str = None):
    """Eşinle çocuk yap. Eşin kabul ederse çocuk doğar. İstersen isim verebilirsin."""
    info = get_marriage(ctx.guild.id, ctx.author.id)
    if not info:
        await ctx.send("Çocuk yapmak için önce evli olman lazım. `B!evlen @üye` ile teklif edebilirsin.")
        return
    partner = ctx.guild.get_member(info["partner_id"])
    if partner is None:
        await ctx.send("Eşin sunucuda görünmüyor. Çocuk için eşinin sunucuda olması lazım.")
        return
    if count_children(ctx.guild.id, ctx.author.id, partner.id) >= CHILD_LIMIT:
        await ctx.send(f"Bu evlilikte zaten en fazla çocuk sayısına (**{CHILD_LIMIT}**) ulaştınız.")
        return
    cd_user, cd_action = child_cooldown_key(ctx.author.id, partner.id)
    remaining = fun_cooldown_remaining(cd_user, cd_action, CHILD_COOLDOWN)
    if remaining:
        await ctx.send(f"Biraz dinlenin. Yaklaşık **{remaining // 60 + 1} dakika** sonra tekrar deneyebilirsiniz.")
        return
    balance = get_balance(0, ctx.author.id)
    if ctx.author.id != OWNER_ID and balance < CHILD_COST:
        await ctx.send(f"Çocuk masrafı **{CHILD_COST:,} paklava**. Bakiyen: **{balance_label(ctx.author.id, balance)}**".replace(",", "."))
        return
    name = None
    if isim:
        name = discord.utils.escape_markdown(discord.utils.escape_mentions(" ".join(isim.split())))[:20].strip() or None
    await ctx.send(
        f"{partner.mention}, {ctx.author.mention} seninle çocuk sahibi olmak istiyor! 🍼\n"
        f"Masraf: **{CHILD_COST:,} paklava** (teklif eden öder)".replace(",", ".")
        + (f"\nBebeğin adı: **{name}**" if name else ""),
        view=ChildRequestView(ctx.author, partner, name),
        allowed_mentions=discord.AllowedMentions(users=[partner]),
    )


@bot.command(name="çocuklar", aliases=["cocuklar", "çocuklarım", "cocuklarim"])
@commands.guild_only()
async def cocuklar(ctx, oyuncu: discord.Member = None):
    member = oyuncu or ctx.author
    cursor.execute(
        "SELECT name,gender,born_at,parent1_id,parent2_id FROM children WHERE guild_id=? AND (parent1_id=? OR parent2_id=?) ORDER BY born_at",
        (ctx.guild.id, member.id, member.id),
    )
    rows = cursor.fetchall()
    if not rows:
        await ctx.send(f"{member.display_name} henüz çocuk sahibi değil. `B!çocukyap` ile eşinle çocuk yapabilirsin.")
        return
    lines = []
    for name, gender, born_at, _p1, _p2 in rows:
        emoji = "👧" if gender == "Kız" else "👦"
        lines.append(f"{emoji} **{name}** — {gender} · {discord.utils.format_dt(datetime.fromtimestamp(born_at, timezone.utc), style='D')}")
    embed = discord.Embed(title=f"{member.display_name} ailesi 👨‍👩‍👧", description="\n".join(lines), color=discord.Color.from_rgb(255, 190, 120))
    embed.set_footer(text=f"{len(rows)}/{CHILD_LIMIT} çocuk")
    await ctx.send(embed=embed)


# -------------------- 3.7: GELISMIS BALIK TUTMA EKONOMISI --------------------
# unlock: bu baligin oltayla yakalanabilecegi en dusuk olta seviyesi
FISH_TYPES = {
    'hamsi': {"name": 'Hamsi', "min": 20, "max": 35, "weight": 30, "unlock": 0},
    'istavrit': {"name": 'İstavrit', "min": 20, "max": 35, "weight": 28, "unlock": 0},
    'sazan': {"name": 'Sazan', "min": 20, "max": 35, "weight": 26, "unlock": 1},
    'alabalik': {"name": 'Alabalık', "min": 25, "max": 40, "weight": 24, "unlock": 1},
    'levrek': {"name": 'Levrek', "min": 25, "max": 40, "weight": 22, "unlock": 1},
    'turna': {"name": 'Turna', "min": 30, "max": 50, "weight": 21, "unlock": 2},
    'somon': {"name": 'Somon', "min": 35, "max": 60, "weight": 20, "unlock": 2},
    'orkinos': {"name": 'Orkinos', "min": 35, "max": 60, "weight": 19, "unlock": 2},
    'kilicbaligi': {"name": 'Kılıç balığı', "min": 40, "max": 65, "weight": 18, "unlock": 3},
    'mercan': {"name": 'Kırmızı mercan', "min": 45, "max": 75, "weight": 17, "unlock": 3},
    'inci_balik': {"name": 'İnci balığı', "min": 50, "max": 80, "weight": 16, "unlock": 3},
    'ejderha_baligi': {"name": 'Ejderha balığı', "min": 55, "max": 90, "weight": 16, "unlock": 4},
    'altin_balik': {"name": 'Altın balık', "min": 60, "max": 100, "weight": 15, "unlock": 4},
    'efsanevi_leviathan': {"name": 'Efsanevi Leviathan', "min": 65, "max": 105, "weight": 14, "unlock": 4},
    'fish_15': {"name": 'Barbun', "min": 75, "max": 125, "weight": 14, "unlock": 5},
    'fish_16': {"name": 'Tekir', "min": 80, "max": 130, "weight": 13, "unlock": 5},
    'fish_17': {"name": 'Kırlangıç', "min": 90, "max": 150, "weight": 13, "unlock": 5},
    'fish_18': {"name": 'İskorpit', "min": 100, "max": 165, "weight": 12, "unlock": 6},
    'fish_19': {"name": 'Eşkina', "min": 110, "max": 180, "weight": 12, "unlock": 6},
    'fish_20': {"name": 'Mırmır', "min": 120, "max": 200, "weight": 12, "unlock": 6},
    'fish_21': {"name": 'Turna', "min": 135, "max": 225, "weight": 11, "unlock": 7},
    'fish_22': {"name": 'Sudak', "min": 145, "max": 240, "weight": 11, "unlock": 7},
    'fish_23': {"name": 'Yayın balığı', "min": 160, "max": 265, "weight": 11, "unlock": 7},
    'fish_24': {"name": 'Tatlı su levreği', "min": 180, "max": 295, "weight": 10, "unlock": 8},
    'fish_25': {"name": 'Kadife balığı', "min": 200, "max": 330, "weight": 10, "unlock": 8},
    'fish_26': {"name": 'Karabalık', "min": 220, "max": 365, "weight": 10, "unlock": 8},
    'fish_27': {"name": 'Dere alası', "min": 240, "max": 395, "weight": 9, "unlock": 9},
    'fish_28': {"name": 'Gökkuşağı alabalığı', "min": 265, "max": 435, "weight": 9, "unlock": 9},
    'fish_29': {"name": 'Bıyıklı balık', "min": 295, "max": 485, "weight": 9, "unlock": 9},
    'fish_30': {"name": 'Siraz', "min": 325, "max": 535, "weight": 9, "unlock": 10},
    'fish_31': {"name": 'Somon', "min": 360, "max": 595, "weight": 9, "unlock": 10},
    'fish_32': {"name": 'Palamut', "min": 400, "max": 660, "weight": 8, "unlock": 10},
    'fish_33': {"name": 'Lüfer', "min": 440, "max": 725, "weight": 8, "unlock": 11},
    'fish_34': {"name": 'Torik', "min": 485, "max": 800, "weight": 8, "unlock": 11},
    'fish_35': {"name": 'Uskumru', "min": 535, "max": 885, "weight": 8, "unlock": 11},
    'fish_36': {"name": 'Kolyoz', "min": 595, "max": 980, "weight": 8, "unlock": 12},
    'fish_37': {"name": 'Orkinos', "min": 655, "max": 1080, "weight": 8, "unlock": 12},
    'fish_38': {"name": 'Akya', "min": 725, "max": 1195, "weight": 7, "unlock": 12},
    'fish_39': {"name": 'Lambuka', "min": 800, "max": 1320, "weight": 7, "unlock": 13},
    'fish_40': {"name": 'Kılıç balığı', "min": 885, "max": 1460, "weight": 7, "unlock": 13},
    'fish_41': {"name": 'Kırmızı mercan', "min": 975, "max": 1610, "weight": 7, "unlock": 13},
    'fish_42': {"name": 'Orfoz', "min": 1080, "max": 1780, "weight": 7, "unlock": 14},
    'fish_43': {"name": 'Lagos', "min": 1195, "max": 1970, "weight": 7, "unlock": 14},
    'fish_44': {"name": 'Sinarit', "min": 1320, "max": 2180, "weight": 7, "unlock": 14},
    'fish_45': {"name": 'Fangri', "min": 1455, "max": 2400, "weight": 6, "unlock": 15},
    'fish_46': {"name": 'Trança', "min": 1610, "max": 2655, "weight": 6, "unlock": 15},
    'fish_47': {"name": 'Lipsoz', "min": 1780, "max": 2935, "weight": 6, "unlock": 15},
    'fish_48': {"name": 'Hani balığı', "min": 1965, "max": 3240, "weight": 6, "unlock": 16},
    'fish_49': {"name": 'İsparoz', "min": 2170, "max": 3580, "weight": 6, "unlock": 16},
    'fish_50': {"name": 'Sarıağız', "min": 2400, "max": 3960, "weight": 6, "unlock": 16},
    'fish_51': {"name": 'İnci balığı', "min": 2650, "max": 4370, "weight": 6, "unlock": 17},
    'fish_52': {"name": 'Melek balığı', "min": 2930, "max": 4835, "weight": 6, "unlock": 17},
    'fish_53': {"name": 'Kelebek balığı', "min": 3235, "max": 5340, "weight": 6, "unlock": 17},
    'fish_54': {"name": 'Papağan balığı', "min": 3575, "max": 5900, "weight": 6, "unlock": 18},
    'fish_55': {"name": 'Aslan balığı', "min": 3950, "max": 6520, "weight": 5, "unlock": 18},
    'fish_56': {"name": 'Cerrah balığı', "min": 4365, "max": 7200, "weight": 5, "unlock": 18},
    'fish_57': {"name": 'Balon balığı', "min": 4825, "max": 7960, "weight": 5, "unlock": 19},
    'fish_58': {"name": 'Kutu balığı', "min": 5330, "max": 8795, "weight": 5, "unlock": 19},
    'fish_59': {"name": 'Mandarin balığı', "min": 5890, "max": 9720, "weight": 5, "unlock": 19},
    'fish_60': {"name": 'Diskus', "min": 6510, "max": 10740, "weight": 5, "unlock": 20},
    'fish_61': {"name": 'Müren', "min": 7195, "max": 11870, "weight": 5, "unlock": 20},
    'fish_62': {"name": 'Manta vatozu', "min": 7950, "max": 13120, "weight": 5, "unlock": 20},
    'fish_63': {"name": 'Vatoz', "min": 8785, "max": 14495, "weight": 5, "unlock": 21},
    'fish_64': {"name": 'Çekiç başlı köpekbalığı', "min": 9705, "max": 16015, "weight": 5, "unlock": 21},
    'fish_65': {"name": 'Kaplan köpekbalığı', "min": 10725, "max": 17695, "weight": 5, "unlock": 21},
    'fish_66': {"name": 'Büyük beyaz köpekbalığı', "min": 11855, "max": 19560, "weight": 5, "unlock": 22},
    'fish_67': {"name": 'Balina köpekbalığı', "min": 13095, "max": 21605, "weight": 5, "unlock": 22},
    'fish_68': {"name": 'Çekiç balığı', "min": 14475, "max": 23885, "weight": 5, "unlock": 22},
    'fish_69': {"name": 'Testere balığı', "min": 15990, "max": 26385, "weight": 4, "unlock": 23},
    'fish_70': {"name": 'Elektrikli vatoz', "min": 17670, "max": 29155, "weight": 4, "unlock": 23},
    'fish_71': {"name": 'Fener balığı', "min": 19525, "max": 32215, "weight": 4, "unlock": 23},
    'fish_72': {"name": 'Kürek balığı', "min": 21575, "max": 35600, "weight": 4, "unlock": 24},
    'fish_73': {"name": 'Kılıç kuyruk', "min": 23845, "max": 39345, "weight": 4, "unlock": 24},
    'fish_74': {"name": 'Denizatı', "min": 26345, "max": 43470, "weight": 4, "unlock": 24},
    'fish_75': {"name": 'Deniz ejderi', "min": 29115, "max": 48040, "weight": 4, "unlock": 25},
    'fish_76': {"name": 'Uçan balık', "min": 32170, "max": 53080, "weight": 4, "unlock": 25},
    'fish_77': {"name": 'Ay balığı', "min": 35545, "max": 58650, "weight": 4, "unlock": 26},
    'fish_78': {"name": 'Küçük ton balığı', "min": 39280, "max": 64810, "weight": 4, "unlock": 26},
    'fish_79': {"name": 'Kara kılıç balığı', "min": 43405, "max": 71620, "weight": 4, "unlock": 26},
    'fish_80': {"name": 'Derin deniz levreği', "min": 47960, "max": 79135, "weight": 4, "unlock": 27},
    'fish_81': {"name": 'Kristal balık', "min": 52995, "max": 87440, "weight": 4, "unlock": 27},
    'fish_82': {"name": 'Safir balığı', "min": 58560, "max": 96625, "weight": 4, "unlock": 27},
    'fish_83': {"name": 'Yakut balığı', "min": 64710, "max": 106770, "weight": 4, "unlock": 28},
    'fish_84': {"name": 'Zümrüt balığı', "min": 71505, "max": 117985, "weight": 4, "unlock": 28},
    'fish_85': {"name": 'Elmas yüzgeç', "min": 79015, "max": 130375, "weight": 4, "unlock": 28},
    'fish_86': {"name": 'Altın pullu balık', "min": 87310, "max": 144060, "weight": 4, "unlock": 29},
    'fish_87': {"name": 'Gümüş kuyruk', "min": 96480, "max": 159190, "weight": 4, "unlock": 29},
    'fish_88': {"name": 'Platin balık', "min": 106610, "max": 175905, "weight": 4, "unlock": 29},
    'fish_89': {"name": 'Opal balığı', "min": 117800, "max": 194370, "weight": 4, "unlock": 30},
    'fish_90': {"name": 'Ametist balığı', "min": 130170, "max": 214780, "weight": 4, "unlock": 30},
    'fish_91': {"name": 'Alev balığı', "min": 143840, "max": 237335, "weight": 4, "unlock": 30},
    'fish_92': {"name": 'Buz balığı', "min": 158940, "max": 262250, "weight": 3, "unlock": 31},
    'fish_93': {"name": 'Fırtına balığı', "min": 175630, "max": 289790, "weight": 3, "unlock": 31},
    'fish_94': {"name": 'Yıldırım balığı', "min": 194075, "max": 320225, "weight": 3, "unlock": 31},
    'fish_95': {"name": 'Gölge balığı', "min": 214450, "max": 353840, "weight": 3, "unlock": 32},
    'fish_96': {"name": 'Işık balığı', "min": 236970, "max": 391000, "weight": 3, "unlock": 32},
    'fish_97': {"name": 'Ay ışığı balığı', "min": 261850, "max": 432050, "weight": 3, "unlock": 32},
    'fish_98': {"name": 'Güneş balığı', "min": 289345, "max": 477420, "weight": 3, "unlock": 33},
    'fish_99': {"name": 'Kuyruklu yıldız balığı', "min": 319725, "max": 527545, "weight": 3, "unlock": 33},
    'fish_100': {"name": 'Kutup yıldızı balığı', "min": 353295, "max": 582935, "weight": 3, "unlock": 33},
    'fish_101': {"name": 'Hayalet balık', "min": 390390, "max": 644145, "weight": 3, "unlock": 34},
    'fish_102': {"name": 'Ruh balığı', "min": 431385, "max": 711785, "weight": 3, "unlock": 34},
    'fish_103': {"name": 'Zaman balığı', "min": 476680, "max": 786520, "weight": 3, "unlock": 34},
    'fish_104': {"name": 'Boyut balığı', "min": 526730, "max": 869105, "weight": 3, "unlock": 35},
    'fish_105': {"name": 'Rüya balığı', "min": 582035, "max": 960360, "weight": 3, "unlock": 35},
    'fish_106': {"name": 'Kâbus balığı', "min": 643150, "max": 1061200, "weight": 3, "unlock": 35},
    'fish_107': {"name": 'Sis balığı', "min": 710680, "max": 1172620, "weight": 3, "unlock": 36},
    'fish_108': {"name": 'Girdap balığı', "min": 785300, "max": 1295745, "weight": 3, "unlock": 36},
    'fish_109': {"name": 'Kasırga balığı', "min": 867760, "max": 1431805, "weight": 3, "unlock": 36},
    'fish_110': {"name": 'Meteor balığı', "min": 958870, "max": 1582135, "weight": 3, "unlock": 37},
    'fish_111': {"name": 'Lav balığı', "min": 1059555, "max": 1748265, "weight": 3, "unlock": 37},
    'fish_112': {"name": 'Magma balığı', "min": 1170805, "max": 1931830, "weight": 3, "unlock": 37},
    'fish_113': {"name": 'Buzul ejder balığı', "min": 1293740, "max": 2134670, "weight": 3, "unlock": 38},
    'fish_114': {"name": 'Alev ejder balığı', "min": 1429585, "max": 2358815, "weight": 3, "unlock": 38},
    'fish_115': {"name": 'Deniz ejderhası', "min": 1579690, "max": 2606490, "weight": 3, "unlock": 38},
    'fish_116': {"name": 'Gökkuşağı ejderi', "min": 1745560, "max": 2880175, "weight": 3, "unlock": 39},
    'fish_117': {"name": 'Karanlık ejder balığı', "min": 1928840, "max": 3182585, "weight": 3, "unlock": 39},
    'fish_118': {"name": 'Kristal ejder balığı', "min": 2131370, "max": 3516760, "weight": 3, "unlock": 39},
    'fish_119': {"name": 'Kadim ejder balığı', "min": 2355165, "max": 3886020, "weight": 3, "unlock": 40},
    'fish_120': {"name": 'Gökyüzü balığı', "min": 2602455, "max": 4294050, "weight": 3, "unlock": 40},
    'fish_121': {"name": 'Poseidon balığı', "min": 2875715, "max": 4744930, "weight": 3, "unlock": 40},
    'fish_122': {"name": 'Atlantis balığı', "min": 3177665, "max": 5243145, "weight": 3, "unlock": 41},
    'fish_123': {"name": 'Kayıp şehir balığı', "min": 3511320, "max": 5793680, "weight": 3, "unlock": 41},
    'fish_124': {"name": 'Deniz kralı balığı', "min": 3880010, "max": 6402015, "weight": 3, "unlock": 41},
    'fish_125': {"name": 'Derinlik hükümdarı', "min": 4287410, "max": 7074225, "weight": 3, "unlock": 42},
    'fish_126': {"name": 'Okyanus devi', "min": 4737590, "max": 7817025, "weight": 3, "unlock": 42},
    'fish_127': {"name": 'Abis balığı', "min": 5235035, "max": 8637810, "weight": 3, "unlock": 42},
    'fish_128': {"name": 'Leviathan yavrusu', "min": 5784715, "max": 9544780, "weight": 3, "unlock": 43},
    'fish_129': {"name": 'Leviathan', "min": 6392110, "max": 10546980, "weight": 3, "unlock": 43},
    'fish_130': {"name": 'Kadim Leviathan', "min": 7063280, "max": 11654410, "weight": 3, "unlock": 43},
    'fish_131': {"name": 'Baklava balığı', "min": 7804925, "max": 12878125, "weight": 3, "unlock": 44},
    'fish_132': {"name": 'Altın taç balığı', "min": 8624440, "max": 14230325, "weight": 3, "unlock": 44},
    'fish_133': {"name": 'Kraliyet balığı', "min": 9530005, "max": 15724510, "weight": 2, "unlock": 44},
    'fish_134': {"name": 'Milyoner balığı', "min": 10530655, "max": 17375580, "weight": 2, "unlock": 45},
    'fish_135': {"name": 'Şans balığı', "min": 11636375, "max": 19200020, "weight": 2, "unlock": 45},
    'fish_136': {"name": 'Bereket balığı', "min": 12858195, "max": 21216020, "weight": 2, "unlock": 45},
    'fish_137': {"name": 'Efsane balığı', "min": 14208305, "max": 23443705, "weight": 2, "unlock": 46},
    'fish_138': {"name": 'Mitoloji balığı', "min": 15700180, "max": 25905295, "weight": 2, "unlock": 46},
    'fish_139': {"name": 'Anka balığı', "min": 17348695, "max": 28625345, "weight": 2, "unlock": 46},
    'fish_140': {"name": 'Anka denizatı', "min": 19170310, "max": 31631010, "weight": 2, "unlock": 47},
    'fish_141': {"name": 'Kozmik balık', "min": 21183195, "max": 34952270, "weight": 2, "unlock": 47},
    'fish_142': {"name": 'Galaksi balığı', "min": 23407430, "max": 38622260, "weight": 2, "unlock": 47},
    'fish_143': {"name": 'Nebula balığı', "min": 25865210, "max": 42677595, "weight": 2, "unlock": 48},
    'fish_144': {"name": 'Süpernova balığı', "min": 28581055, "max": 47158740, "weight": 2, "unlock": 48},
    'fish_145': {"name": 'Kara delik balığı', "min": 31582065, "max": 52110405, "weight": 2, "unlock": 48},
    'fish_146': {"name": 'Evren balığı', "min": 34898185, "max": 57582005, "weight": 2, "unlock": 49},
    'fish_147': {"name": 'Sonsuzluk balığı', "min": 38562490, "max": 63628110, "weight": 2, "unlock": 49},
    'fish_148': {"name": 'Zamanın efendisi', "min": 42611555, "max": 70309065, "weight": 2, "unlock": 49},
    'fish_149': {"name": 'Boyutlar arası balık', "min": 47085765, "max": 77691510, "weight": 2, "unlock": 50},
    'fish_150': {"name": 'Tanrısal balık', "min": 52029775, "max": 85849130, "weight": 2, "unlock": 50},
}

MAX_ROD_LEVEL = 50
ROD_UPGRADE_PRICES = [0, 1500, 3500, 7000, 13000, 22000]


def rod_upgrade_price(level):
    """Ilk bes fiyat sabit, sonraki seviyeler kademeli olarak pahalanir."""
    if level < len(ROD_UPGRADE_PRICES):
        return ROD_UPGRADE_PRICES[level]
    return int(round(ROD_UPGRADE_PRICES[-1] * (1.22 ** (level - 5)) / 100) * 100)


def fishing_weights(rod_level):
    """Olta seviyesi arttikca yeni turler acilir ve ust seviye baliklar agirlik kazanir."""
    available = []
    weights = []
    for key, fish in FISH_TYPES.items():
        unlock = fish["unlock"]
        if rod_level < unlock:
            continue
        # Her balik kendi acilis seviyesinden sonra guclenir. Eski yaygin baliklar
        # ust seviyelerde geri planda kalir; nadir baliklar belirgin sekilde one cikar.
        progress = max(0, rod_level - unlock)
        weight = fish["weight"] * (1 + progress / 7) ** 1.35
        if unlock == 0:
            weight *= max(0.035, 1 - rod_level / 55)
        available.append(key)
        weights.append(max(0.05, weight))
    return available, weights


@bot.command(name="balık", aliases=["balik", "baliktut"])
async def balik_tut(ctx):
    remaining = fun_cooldown_remaining(ctx.author.id, "fishing", 10)
    if remaining:
        await ctx.send(f"Yeni bir olta atmak için **{remaining} saniye** bekle.")
        return
    set_fun_cooldown(ctx.author.id, "fishing")
    cursor.execute("SELECT level FROM fishing_rods WHERE user_id=?", (ctx.author.id,))
    rod_row = cursor.fetchone()
    rod_level = max(0, min(MAX_ROD_LEVEL, int(rod_row[0]))) if rod_row else 0
    fish_keys, weights = fishing_weights(rod_level)
    fish_key = random.choices(fish_keys, weights=weights, k=1)[0]
    # Yuksek seviyede daha cok balik tutma sansi da artar
    quantity_weights = [max(8, 62 - rod_level * 1.05), 25, 9 + rod_level * 0.35, 4 + rod_level * 0.7]
    quantity = random.choices([1, 2, 3, 4], weights=quantity_weights, k=1)[0]
    cursor.execute(
        "INSERT INTO fish_inventory (user_id,fish_key,quantity) VALUES (?,?,?) "
        "ON CONFLICT(user_id,fish_key) DO UPDATE SET quantity=quantity+excluded.quantity",
        (ctx.author.id, fish_key, quantity),
    )
    db.commit()
    fish = FISH_TYPES[fish_key]
    price = (fish["min"] + fish["max"]) // 2
    await ctx.send(
        f"{ctx.author.mention} seviye **{rod_level}** oltanla **{quantity} adet {fish['name']}** tuttun\n"
        f"tahmini satis degeri **{price * quantity:,} paklava**\n"
        f"`B!balıksat` ile satabilirsin"
        .replace(",", ".")
    )


@bot.command(name="olta")
async def olta(ctx):
    cursor.execute("SELECT level FROM fishing_rods WHERE user_id=?", (ctx.author.id,))
    row = cursor.fetchone()
    level = int(row[0]) if row else 0
    if level >= MAX_ROD_LEVEL:
        next_text = "en yuksek olta seviyesindesin"
    else:
        next_text = f"sonraki seviye ucreti **{rod_upgrade_price(level + 1):,} paklava** `B!oltayükselt` veya toplu olarak `B!oltayükselt 10`".replace(",", ".")
    unlocked = [fish["name"] for fish in FISH_TYPES.values() if fish["unlock"] <= level]
    embed = discord.Embed(
        title="Balikci oltasi",
        description=(f"mevcut seviye **{level}/{MAX_ROD_LEVEL}**\n{next_text}\n\n"
                     f"acik balik turu **{len(unlocked)}/{len(FISH_TYPES)}**\n"
                     f"yakalanabilenler: {', '.join(unlocked)}\n\n"
                     "olta seviyesi yukseldikce yeni balik turleri acilir ve nadir balik sansi ciddi sekilde artar"),
        color=discord.Color.teal(),
    )
    await ctx.send(embed=embed)


@bot.command(name="oltayükselt", aliases=["oltayukselt"])
async def olta_yukselt(ctx, miktar: int = 1):
    if miktar <= 0:
        await ctx.send("yukselme miktari en az 1 olmali ornek `B!oltayükselt 40`")
        return
    cursor.execute("SELECT level FROM fishing_rods WHERE user_id=?", (ctx.author.id,))
    row = cursor.fetchone()
    current_level = int(row[0]) if row else 0
    target_level = min(MAX_ROD_LEVEL, current_level + miktar)
    actual_increase = target_level - current_level
    if actual_increase <= 0:
        await ctx.send(f"oltan zaten en yuksek seviye {MAX_ROD_LEVEL}")
        return
    total_price = sum(rod_upgrade_price(level) for level in range(current_level + 1, target_level + 1))
    balance = get_balance(0, ctx.author.id)
    if ctx.author.id != OWNER_ID and balance < total_price:
        await ctx.send(
            f"bu toplu yukselme icin **{total_price:,} paklava** gerekiyor\n"
            f"bakiyen **{balance:,} paklava**\n"
            f"en fazla karsilayabilecegin miktari yaz veya daha fazla paklava biriktir"
            .replace(",", ".")
        )
        return
    if ctx.author.id != OWNER_ID:
        add_balance(0, ctx.author.id, -total_price)
    cursor.execute(
        "INSERT INTO fishing_rods (user_id,level) VALUES (?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET level=excluded.level",
        (ctx.author.id, target_level),
    )
    db.commit()
    newly_unlocked = [fish["name"] for fish in FISH_TYPES.values() if current_level < fish["unlock"] <= target_level]
    unlock_text = "\nyeni acilan baliklar **" + ", ".join(yeni for yeni in newly_unlocked) + "**" if newly_unlocked else ""
    await ctx.send(
        f"olta toplu olarak **{actual_increase} seviye** yukseltildi\n"
        f"eski seviye **{current_level}** yeni seviye **{target_level}/{MAX_ROD_LEVEL}**\n"
        f"harcanan **{total_price:,} paklava**".replace(",", ".") + unlock_text
    )


@bot.command(name="balıklar", aliases=["baliklar", "balikenvanter"])
async def baliklar(ctx):
    cursor.execute("SELECT fish_key,quantity FROM fish_inventory WHERE user_id=? AND quantity>0 ORDER BY quantity DESC", (ctx.author.id,))
    rows = cursor.fetchall()
    if not rows:
        await ctx.send("Balık çantan boş. `B!balık` ile balık tutabilirsin.")
        return
    lines = []
    estimated = 0
    for key, quantity in rows:
        fish = FISH_TYPES.get(key)
        if not fish:
            continue
        avg = (fish["min"] + fish["max"]) // 2
        estimated += avg * quantity
        lines.append(f"**{fish['name']}** × {quantity} — yaklaşık {avg * quantity} paklava")
    embed = discord.Embed(title="Balık Çantası", description="\n".join(lines), color=discord.Color.teal())
    embed.set_footer(text=f"Tahmini toplam değer: {estimated} paklava · Satmak için B!balıksat")
    await ctx.send(embed=embed)


@bot.command(name="balıksat", aliases=["baliksat", "baliksatmak"])
async def balik_sat(ctx, *, secim: str = None):
    fish_key = None
    requested_quantity = None
    if secim:
        parts = secim.strip().split()
        if parts and parts[-1].isdigit():
            requested_quantity = int(parts.pop())
        if parts:
            fish_key = normalize_economy_key(" ".join(parts))
            aliases = {normalize_economy_key(v["name"]): k for k, v in FISH_TYPES.items()}
            fish_key = aliases.get(fish_key, fish_key)
            if fish_key not in FISH_TYPES:
                await ctx.send("Bu balık türü bulunamadı. `B!balıklar` komutuna bak.")
                return
        if requested_quantity is not None and fish_key is None:
            await ctx.send("Adetli satış için balık türünü de yaz. Örnek: `B!balıksat levrek 2`.")
            return
        if requested_quantity is not None and requested_quantity <= 0:
            await ctx.send("Satış adedi 1 veya daha büyük olmalı.")
            return
    cursor.execute("SELECT fish_key,quantity FROM fish_inventory WHERE user_id=? AND quantity>0", (ctx.author.id,))
    inventory_rows = cursor.fetchall()
    if fish_key:
        inventory_rows = [row for row in inventory_rows if row[0] == fish_key]
    if not inventory_rows:
        await ctx.send("Satılacak balığın yok. `B!balık` ile balık tut.")
        return
    sold_lines = []
    total = 0
    for key, owned in inventory_rows:
        quantity = min(owned, requested_quantity) if requested_quantity is not None else owned
        fish = FISH_TYPES[key]
        # Per-item price is randomized within that fish's price range.
        unit_price = random.randint(fish["min"], fish["max"])
        value = unit_price * quantity
        total += value
        left = owned - quantity
        if left:
            cursor.execute("UPDATE fish_inventory SET quantity=? WHERE user_id=? AND fish_key=?", (left, ctx.author.id, key))
        else:
            cursor.execute("DELETE FROM fish_inventory WHERE user_id=? AND fish_key=?", (ctx.author.id, key))
        sold_lines.append(f"{fish['name']} × {quantity}: {value} paklava")
    db.commit()
    add_balance(0, ctx.author.id, total)
    await ctx.send(f"Balık satışı tamamlandı!\n" + "\n".join(sold_lines[:8]) + f"\n**Toplam: +{total} paklava** · Güncel bakiye: **{balance_label(ctx.author.id, get_balance(0, ctx.author.id))}**")


# -------------------- 3.7: EK EGLENCE --------------------
TRUTH_PROMPTS = [
    "En çok güldüğün oyun anısı neydi?", "Bir günlüğüne hangi oyunun karakteri olmak isterdin?",
    "Sunucuda en komik bulduğun şey ne?", "Şimdiye kadar yaptığın en saçma hata neydi?",
    "Bir yetenek anında öğrenebilsen bu ne olurdu?", "En sevdiğin çocukluk oyunu hangisiydi?",
]
DARE_PROMPTS = [
    "Bir sonraki mesajını sadece üç kelimeyle yaz.", "10 saniye içinde aklına gelen ilk oyunu söyle.",
    "Kendine komik bir oyun lakabı bul.", "Bir sonraki mesajında her kelimeyi büyük harfle başlat.",
    "Sohbete sevdiğin bir oyunu tek cümlede tanıt.", "Bir dakika boyunca sadece kısa cümlelerle konuş.",
]


@bot.command(name="doğruluk", aliases=["dogruluk", "truth"])
async def dogruluk(ctx):
    await ctx.send(f"**Doğruluk sorusu:** {random.choice(TRUTH_PROMPTS)}")


@bot.command(name="cesaret", aliases=["dare"])
async def cesaret(ctx):
    await ctx.send(f"**Cesaret görevi:** {random.choice(DARE_PROMPTS)}")


@bot.command(name="kader")
async def kader(ctx):
    predictions = [
        "Bugün şans senden yana; ama pingine fazla güvenme.",
        "Yakında sunucuda beklenmedik bir kahkaha kopacak.",
        "Bir oyunda son saniyede güzel bir geri dönüş yapacaksın.",
        "Bugünün görevi: gereksiz yere panik yapmamak.",
        "Bir arkadaşın senden oyun tavsiyesi isteyecek.",
        "Bugün en büyük düşmanın yükleme ekranı olabilir.",
    ]
    await ctx.send(f"{ctx.author.mention} için Baklava kaderi: **{random.choice(predictions)}**")


@bot.command(name="yapayzeka", aliases=["ai"])
async def yapayzeka(ctx):
    embed = discord.Embed(title="Yapay Zeka Komutları", color=discord.Color.blurple())
    embed.description = "Beni etiketle veya bir mesajıma yanıt vererek sohbet et. Mesaja PNG, JPG ya da WEBP görseli ekleyebilirsin."
    embed.add_field(name="Komutlar", value="`B!mod normal`\n`B!mod komik`\n`B!mod ciddi`\n`B!mod korkutucu`\n`B!mod tartışmacı`\n`B!öneri <mesaj>`", inline=False)
    await ctx.send(embed=embed)


# -------------------- 3.6.3: KULLANICI VE HIZ ARACLARI --------------------
def format_uptime(seconds):
    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days} gün")
    if hours or days:
        parts.append(f"{hours} saat")
    if minutes or hours or days:
        parts.append(f"{minutes} dakika")
    parts.append(f"{seconds} saniye")
    return " ".join(parts)


@bot.command(name="davet", aliases=["invite", "botdavet"])
async def davet_command(ctx):
    """Botu başka bir sunucuya eklemek için temel izinli davet bağlantısı verir."""
    if bot.user is None:
        await ctx.send("Bot henüz hazır değil, biraz sonra tekrar dene.")
        return
    permissions = discord.Permissions(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        embed_links=True,
        attach_files=True,
        add_reactions=True,
    )
    invite_url = discord.utils.oauth_url(bot.user.id, permissions=permissions)
    embed = discord.Embed(
        title="Baklava1923 Bot davet bağlantısı",
        description=f"[Botu sunucuna ekle]({invite_url})\n\nBu bağlantı temel sohbet ve yardım izinlerini ister. Yönetim özellikleri için sunucuda ayrıca gerekli izinleri ayarlamalısın.",
        color=discord.Color.from_rgb(239, 177, 83),
    )
    await ctx.send(embed=embed)


@bot.command(name="ping", aliases=["gecikme", "latency"])
@commands.cooldown(2, 5, commands.BucketType.user)
async def ping_command(ctx):
    """Bot gecikmesini ve çalışma süresini gösterir."""
    latency_ms = round(bot.latency * 1000) if bot.latency is not None else -1
    embed = discord.Embed(
        title="Baklava1923 bağlantı testi",
        description="Botun bağlantı ve çalışma bilgileri",
        color=discord.Color.from_rgb(83, 190, 150),
    )
    embed.add_field(name="Gateway gecikmesi", value=(f"`{latency_ms} ms`" if latency_ms >= 0 else "`ölçülemiyor`"), inline=True)
    embed.add_field(name="Çalışma süresi", value=f"`{format_uptime(time.time() - BOT_PROCESS_START)}`", inline=True)
    embed.add_field(name="Durum", value="`çevrimiçi`" if bot.is_ready() else "`hazırlanıyor`", inline=True)
    embed.set_footer(text=f"Baklava1923 Bot {BOT_VERSION}")
    await ctx.reply(embed=embed, mention_author=False)


HELP_CATEGORIES = {
    "Başlangıç": ("Temel kullanım", "`!` · `B!` · `b!` önekleri kullanılabilir.\n`B!yardım` kategorili yardım menüsü\n`B!ping` gecikme ve çalışma süresi\n`B!botdurum` çalışma durumu\n`/durum` ayrıntılı bot durumu\n`B!sunucubilgi` sunucu istatistikleri"),
    "Kullanıcı araçları": ("Avatar, profil, kanal ve sunucu kartları", "`B!avatar [@üye]` profil fotoğrafını gösterir\n`B!banner [@üye]` profil afişini gösterir\n`B!kullanıcıbilgisi [@üye]` üye bilgi kartı\n`B!kanalbilgisi [#kanal]` kanal bilgileri\n`B!rolbilgisi <rol>` rol bilgileri\n`B!sunucukartı` görselli sunucu kartı\n`B!emojiler` sunucunun emojileri\n`B!hesapla <işlem>` güvenli matematik hesaplama\n`B!tarih` tarih ve saat bilgisi\n`B!davet` temel izinlerle davet bağlantısı\n`B!ping` gecikme ve çalışma süresi\n`B!hesapyasi [@üye]` hesap yaşını gösterir\n`B!izinler [@üye]` bu kanaldaki izinleri gösterir\n`B!uyeara <isim>` isimle üye arar"),
    "Yapay zeka": ("Sohbet ve görsel", "Botu etiketleyerek veya botun mesajına yanıt vererek sohbet et.\n`B!botsohbet @bot` sunucuda botlarla kontrollü sohbet (Yönetici).\n`B!botsohbet kapat` sohbeti kapatır.\n`B!mod normal`\n`B!mod komik`\n`B!mod ciddi`\n`B!mod korkutucu`\n`B!mod tartışmacı`\nGörseli mesajına ekleyip botu etiketleyebilirsin."),
    "Eğlence ve oyunlar": ("Mini oyunlar, GIF ve simülasyon", "`B!eğlence`\n`B!fakemesaj @oyuncu <mesaj>` avatarli mesaj önizlemesi\n`B!ship @kişi1 @kişi2`\n`B!evlen @üye` · `B!evlilik` · `B!boşan` · `B!ısmarla` · `B!çocukyap` · `B!çocuklar`\n`B!doğruluk` · `B!cesaret` · `B!kader`\n`B!gayradar24`\n`B!tkm @oyuncu`\n`B!1vs1 @oyuncu`\n`B!zar [yüz]` · `B!yazitura yazı/tura`\n`B!sayıtahmin` · `B!tahmin <sayı>`\n`B!refleks` · `B!kapışma @oyuncu`\n`B!hype [@oyuncu]` · `B!zafer [@oyuncu]`\n`B!şaka` · `B!meme` · `B!soru <metin>`\n`B!tersle` · `B!roast` · `B!iltifat` · `B!uyum @oyuncu`\n`B!karar a | b | c` aralarından seçim yapar · `B!sekizbilir <soru>`\n`B!zarkapis @üye` zar düellosu · `B!çarkıfelek` · `B!espri`\n`B!tavsiye` · `B!motivasyon` · `B!hikaye` · `B!atasozu` · `B!bilgi` · `B!muhabbet`\n`B!slogan` · `B!kahve [@üye]` · `B!cay [@üye]` · `B!selam [@üye]` · `B!dans [@üye]`\n`B!kucakla @üye` · `B!pat [@üye]` · `B!ruhhali [@üye]` · `B!burc <burç>` · `B!puanla <şey>`\n`B!iq [@üye]` · `B!balon` · `B!oyunoner` · `B!renk` · `B!şanslısayı`\n`B!günaydın` · `B!iyigeceler` · `B!hesapla <ifade>` · `B!tersyazi <metin>`\n`B!kelimesay <metin>` · `B!saat` · `B!kacgun <gün> <ay> <yıl>`\n`B!kisilik [@üye]` · `B!enerji [@üye]` · `B!rastgelesayi <min> <max>` · `B!ortalama <sayılar>`\n`B!tarih` · `B!ozur [@üye]` · `B!tebrik [@üye]`"),
    "Ekonomi": ("Paklava, banka, meslek, işletme ve ticaret", "`B!ekonomi`\n`B!bakiye [@oyuncu]` · `B!banka`\n`B!yatır <miktar>` · `B!çek <miktar>`\n`B!günlük` · `B!haftalık` · `B!gorev` · `B!balık` · `B!balıklar` · `B!balıksat` · `B!olta` · `B!oltayükselt`\n`B!meslekler` · `B!meslek <isim>` · `B!çalış`\n`B!işletmeler` · `B!işletmekur <isim>`\n`B!işletme` · `B!işletmetopla`\n`B!market` · `B!satinal <eşya>` · `B!envanter [@oyuncu]`\n`B!hediye @oyuncu <miktar>`\n`B!ticaret @oyuncu <eşya_anahtarı>`\n`B!ekonomisiralamasi`\n`B!bahis <miktar> <kırmızı|siyah|yeşil>` · `B!hazineavi` · `B!dilen`\n`B!balıkbilgi <tür>` · `B!balikliste [sayfa]` · `B!balikzirvesi`\n`B!kazi` kazı kazan (1 saat) · `B!maas` (6 saat) · `B!kutu` 200 paklavaya kutu aç\n`B!hirsiz @üye` (1 saat) · `B!bakiyekarsilastir @üye`"),
    "Profil ve seviye": ("XP, seviye, başarımlar", "`B!profil [@oyuncu]`\n`B!seviye [@oyuncu]`\n`B!liderlik`\n`B!basarim [@oyuncu]`\n`B!gorev`\nYöneticiler: `B!seviyerol <seviye> @rol`"),
    "Sunucu yönetimi": ("Kurulum ve kanallar", "`B!sunucu` yönetim paneli\n`B!sunucuoluştur` şablon menüsü\n`B!sunucukur` temel kanallar\n`B!kanaloluştur <isim>`\n`B!rololuştur <isim>`\n`B!oto-rol @rol`\n`B!gelengiden #kanal`\n`B!kilit` · `B!kilitac`\n`B!ayarlar` · `B!yedekle` · `B!yedekler` · `B!gerial <id>`\n`B!kanalkonu <metin>` · `B!kanalyenidenadlandir <isim>` · `B!kanalsira <pozisyon>`\n`B!kanalsil #kanal onayla` · `B!kanalgizle` · `B!kanalgoster` · `B!nsfw <aç|kapat>`\n`B!kategoriolustur <isim>` · `B!kanallistesi` · `B!kanalbul <isim>` · `B!kanalbilgi [#kanal]`\n`B!rolrenk @rol #hex` · `B!rolyenidenadlandir @rol <isim>` · `B!rolsil @rol onayla`\n`B!hoist @rol <aç|kapat>` · `B!mentionable @rol <aç|kapat>` · `B!rolunuyeleri @rol`\n`B!bosroller` · `B!rollistesi` · `B!rolbul <isim>` · `B!rolbilgi @rol`\n`B!sunucuadi <isim>` · `B!sunucuresmi` · `B!sunucuid` · `B!emojiler`\n`B!seskanallar` · `B!uyesayilari`\n`B!sunucuyas` · `B!kanalsayisi` · `B!sesbilgi [#ses-kanalı]`"),
    "Güvenlik ve moderasyon": ("Koruma ve kayıt", "`B!sunucukoruma` · `B!raidkoruma`\n`B!doğrulama #kanal @üye-rol [@bekleyen-rol]`\n`B!ban @üye <sebep>` · `B!kick @üye <sebep>` · `B!temizle <1-100>`\n`B!sunuculog #kanal`\n`B!uyar @üye <sebep>`\n`B!uyarilar @üye`\n`B!uyarisil @üye`\n`B!sustur @üye <dakika> <sebep>`\n`B!susturac @üye`\n`B!unban <id> [sebep]` · `B!yasaklilar` · `B!davetler` · `B!davetsil <kod>`\n`B!mesajlar @üye <1-100>` · `B!mesajsil <mesaj_id>` · `B!mesajinfo <mesaj_id>`\n`B!sabitle <mesaj_id>` · `B!sabitikaldir <mesaj_id>`\n`B!sesat @üye` · `B!sesgetir @üye #ses-kanalı` · `B!sesgizle @üye <aç|kapat>`\n`B!nick @üye <isim|sıfırla>` · `B!yazmaizni @üye <aç|kapat|sıfırla>` · `B!gorusizni @üye <aç|kapat|sıfırla>`\n`B!susturulanlar` · `B!yenikatilan [gün]` · `B!rolsuzuyeler` · `B!adminler` · `B!botyetkisi`\n`B!kufur <aç|kapat>` bu sunucuda botun küfürünü açar veya kapatır"),
    "Topluluk ve etkinlik": ("Bilet, öneri, çekiliş, oylama", "`B!öneri <metin>`\n`B!önerikanal #kanal`\n`B!bilet [konu]` · `B!biletkapat`\n`B!cekilis <dakika> <ödül>`\n`B!turnuva <dakika> <başlık>`\n`B!oylama <dakika> <soru> | <seçenek 1> | <seçenek 2>`\n`B!hatirlat <dakika> <metin>`\n`B!duyuru #kanal <metin>`"),
    "Aktiflik ve araçlar": ("Ek yardımcılar", "`B!aktiflik` 7/30/60/90 günlük tablo\n`B!itiraf <metin>`\n`B!bahane`\n`B!yapayzeka`\n`B!öneri <metin>`"),
    "Ultimate araçlar": ("AFK, özel komutlar, rol ve kayıt araçları", "`B!afk [sebep]` AFK durumunu aç/kapat\n`B!özelkomut-ekle <isim> | <cevap>`\n`B!özelkomut-sil <isim>` · `B!özelkomutlar`\n`B!rolver @üye @rol` · `B!rolal @üye @rol`\n`B!yavasmod <0-21600 saniye>`\n`B!duyuruyap <başlık> | <metin>`\n`B!sunucistatistik` ayrıntılı sunucu bilgisi\n`B!yetkili-notu @üye <not>` · `B!notlar @üye`\n`B!doğumgünü <gg aa>` · `B!doğumgünüm`"),
    "Gelişmiş filtre": ("Sunucuya özel kelime filtresi", "`B!kelimeengelle <kelime>` filtreye ekler\n`B!kelimeizinver <kelime>` filtreden kaldırır\n`B!kelimefiltrelistesi` kayıtlı kelimeleri gösterir"),
    "MEGA sistemler": ("Yeni ekonomi, kulüp, oyun ve yönetim merkezi", "Yeni sistemlerin tamamı: `B!mega-yardim`\nGünlük seri: `B!günlükbonus` · `B!haftalıkbonus`\nTransfer ve itibar: `B!bağış @üye <miktar>` · `B!itibarver @üye`\nProfil: `B!biyografi <yazı>` · `B!profilunvanı <unvan>` · `B!benkimim` · `B!rozetlerim`\nKoleksiyon: `B!koleksiyonmağazası` · `B!koleksiyonal <kod>` · `B!koleksiyonsat <kod>` · `B!tarifler` · `B!üret <tarif>`\nPazar: `B!pazaraekle <kod> <adet> <fiyat>` · `B!pazar` · `B!pazardan-al <ilan> <adet>` · `B!pazarilaniptal <ilan>`\nKulüpler: `B!kulüpkur` · `B!kulüpler` · `B!kulübe-gir <kod>` · `B!kulüpbilgi` · `B!kulüpkatkı <miktar>`\nSunucu: `B!hoşgeldinkanal #kanal` · `B!modlogayarla #kanal` · `B!antispamayar aç` · `B!davetfiltresi aç` · `B!güvenlikdurumu`\nOyun: `B!matematikoyunu` · `B!bilgiyarışması` · `B!kelimekarıştır` · `B!bulmaca`\nEtkinlik: `B!etkinlikbaşlat <YYYY-AA-GG> <SS:DD> <başlık>` · `B!etkinlikler` · `B!etkinlikkatıl <id>`\nHedef ve görev: `B!hedefoluştur <hedef> <ödül> <başlık>` · `B!hedefim` · `B!hedefkatkı <id> <sayı>` · `B!görevödül`\nİstatistik: `B!toplulukistatistik` · `B!mesajistatistik` · `B!veriözeti`"),
    "Metin ve dil araçları": ("Büyük/küçük harf, morse, base64, sayı sistemleri", "`B!buyuk` · `B!kucuk` · `B!baslik` <metin> harf dönüşümleri\n`B!morse <metin>` · `B!morsecoz <kod>` · `B!rot13 <metin>`\n`B!base64 <metin>` · `B!base64coz <kod>` · `B!harfsay <metin>` · `B!palindrom <metin>`\n`B!alfabe` · `B!bosluksil <metin>` · `B!emojiyaz <metin>` · `B!kelimeayir <metin>`\n`B!tekrarla <1-10> <metin>` · `B!sifre [uzunluk]` (DM ile gönderir) · `B!rakamtoplam <sayı>`\n`B!asal <sayı>` · `B!fibonacci <n>` · `B!faktoriyel <n>` · `B!romen <sayı>`\n`B!ikilik <sayı>` · `B!onluk <ikilik>`"),
    "Rastgele üretim": ("İsim, yemek, hayvan, kart ve zar", "`B!rastgeleisim` · `B!rastgelekelime` · `B!rastgelehayvan` · `B!rastgeleyemek`\n`B!icecekoner` · `B!rastgeleyer` · `B!muzikturu` · `B!hobioner` · `B!turoner` · `B!sehironer`\n`B!rastgeleemoji` · `B!kartcek` · `B!zarat [adet] [yüz]`"),
    "Oyun ve bulmaca": ("Quiz, adam asmaca, karışık kelime, matematik", "`B!quiz` · `B!cevap <cevap>` · `B!ipucu` · `B!atla` · `B!quizliderlik`\n`B!karisik` karışık kelime · `B!matematik` işlem sorusu\n`B!adamasmaca` · `B!harf <harf>` adam asmaca tahmini"),
    "Zaman ve hatırlatma": ("Geri sayım, pomodoro, tarih ve yaş", "`B!geridayim <saniye>` · `B!pomodoro` · `B!mola <dakika>`\n`B!tarihfark <gg.aa.yyyy> <gg.aa.yyyy>` · `B!yas <gg.aa.yyyy>`\n`B!yilbasi` · `B!haftasonu` · `B!zaman <saniye>`"),
    "Sosyal istatistik": ("Mesaj sayısı, en aktifler, üyelik süresi", "`B!mesajsayim [@üye]` · `B!enaktif` · `B!uyelikyas [@üye]` · `B!hediyeoner`"),
    "Kişisel notlar": ("Kendi notlarını kaydet ve listele", "`B!notal <metin>` · `B!notlarim` · `B!notsil <numara>`"),
    "Otomatik sistemler": ("Bağlantı ve büyük harf filtresi, tepki rolleri, yıldız panosu", "`B!otomod [link|caps] [aç|kapat]` bağlantı ve büyük harf filtresi (yetkililer muaf)\n`B!tepkirol <mesaj_id> <emoji> @rol` · `B!tepkirolsil <mesaj_id> <emoji>` · `B!tepkirolliste`\n`B!yildizpano #kanal <adet>` · `B!yildizpanokapat` (⭐ tepkisiyle mesajı panoya taşır)\nMesaj düzenleme ve silmeler sunucu log kanalına otomatik yazılır (`B!sunuculog`)."),
}


def _banner_font(size, bold=True):
    names = ["DejaVuSans-BoldOblique.ttf", "DejaVuSans-Bold.ttf"] if bold else ["DejaVuSans.ttf"]
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _lerp_color(a, b, k):
    return tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))


def _smoke_mask(rng, count, alpha_scale, width, height):
    from PIL import ImageFilter

    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    for _ in range(count):
        r = rng.randint(28, 90)
        cx, cy = rng.randint(0, width), rng.randint(0, height)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=rng.randint(70, 150))
    mask = mask.filter(ImageFilter.GaussianBlur(22))
    return mask.point(lambda v: int(v * alpha_scale))


def make_bot_banner_gif():
    """Yardım menüsünün üstündeki Baklava1923-Bot afişi: kırmızı duman, kıvılcım ve metalik yazı."""
    import math
    from PIL import ImageFilter

    width, height = 720, 190
    title = "Baklava1923-Bot"
    rng = random.Random(1923)

    # Yazı, kenarlara sığana kadar fontu küçültür.
    size = 64
    font = _banner_font(size)
    while size > 24:
        left, top, right, bottom = font.getbbox(title)
        if right - left <= width - 80:
            break
        size -= 2
        font = _banner_font(size)
    left, top, right, bottom = font.getbbox(title)
    text_x = (width - (right - left)) // 2 - left
    text_y = (height - (bottom - top)) // 2 - top - 8

    # Sabit arka plan: siyah zemin, kırmızı merkez parıltısı ve kırmızı/gri duman.
    background = Image.new("RGB", (width, height), (4, 4, 6))
    glow = Image.new("L", (width, height), 0)
    ImageDraw.Draw(glow).ellipse((-120, -40, width + 120, height + 40), fill=95)
    glow = glow.filter(ImageFilter.GaussianBlur(60))
    background = Image.composite(Image.new("RGB", (width, height), (150, 16, 16)), background, glow)
    background = Image.composite(
        Image.new("RGB", (width, height), (120, 20, 20)), background,
        _smoke_mask(rng, 38, 0.55, width, height),
    )
    background = Image.composite(
        Image.new("RGB", (width, height), (70, 70, 76)), background,
        _smoke_mask(rng, 30, 0.3, width, height),
    )

    # Yazının katmanları: içi metalik, dış hattı koyu, altında kırmızı parıltı.
    text_mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(text_mask).text((text_x, text_y), title, font=font, fill=255)
    outline_mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(outline_mask).text(
        (text_x, text_y), title, font=font, fill=255, stroke_width=3, stroke_fill=255
    )
    glow_base = text_mask.filter(ImageFilter.GaussianBlur(7))
    metal = Image.new("RGB", (width, height))
    metal_draw = ImageDraw.Draw(metal)
    for y in range(height):
        t = y / (height - 1)
        if t < 0.5:
            color = _lerp_color((236, 240, 247), (120, 128, 146), t * 2)
        else:
            color = _lerp_color((120, 128, 146), (190, 198, 214), (t - 0.5) * 2)
        metal_draw.line((0, y, width, y), fill=color)

    sparks = [
        {
            "x": rng.uniform(0, width), "y": rng.uniform(0, height), "r": rng.uniform(1.3, 2.6),
            "speed": rng.choice([1, 2]), "amp": rng.uniform(4, 12), "ph": rng.uniform(0, 2 * math.pi),
        }
        for _ in range(26)
    ]

    red = Image.new("RGB", (width, height), (255, 26, 26))
    white = Image.new("RGB", (width, height), (255, 255, 255))
    version_font = _banner_font(14, bold=False)
    version_text = f"DISCORD BOT  •  VERSION {BOT_VERSION}"

    frames = []
    total = 24
    for index in range(total):
        phase = index / total
        pulse = 0.8 + 0.2 * math.sin(2 * math.pi * phase)
        frame = background.copy()

        # Yukarı süzülen kıvılcımlar.
        draw = ImageDraw.Draw(frame)
        for sp in sparks:
            y = (sp["y"] - sp["speed"] * height * phase) % height
            x = sp["x"] + sp["amp"] * math.sin(2 * math.pi * phase + sp["ph"])
            r = sp["r"]
            draw.ellipse((x - r, y - r, x + r, y + r), fill=(255, 106, 42))

        # Nabız atan kırmızı parıltı.
        scaled_glow = glow_base.point(lambda v, p=pulse: min(255, int(v * 1.1 * p)))
        frame = Image.composite(red, frame, scaled_glow)

        # Dış hat ve kayan metalik parıltı bandı.
        frame.paste((26, 29, 38), mask=outline_mask)
        band_x = -200 + (width + 400) * phase
        band = Image.new("L", (width, height), 0)
        ImageDraw.Draw(band).polygon(
            [(band_x, 0), (band_x + 70, 0), (band_x + 10, height), (band_x - 60, height)], fill=255
        )
        band = band.filter(ImageFilter.GaussianBlur(10))
        frame.paste(Image.composite(white, metal, band), mask=text_mask)

        ImageDraw.Draw(frame).text(
            (width - 14, height - 12), version_text, font=version_font, fill=(200, 200, 210), anchor="rs"
        )
        frames.append(frame)

    output = io.BytesIO()
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:], duration=85, loop=0, optimize=True)
    output.seek(0)
    return output


# Her yardım kategorisi için ayrı renk paleti ve ayrı hareketli GIF üretilir.
# GIF'ler dosyaya yazılmaz; doğrudan Discord mesajına eklenir.
HELP_CATEGORY_THEMES = {
    "Genel": ((20, 27, 48), (239, 177, 83), "HOME"),
    "Başlangıç": ((22, 39, 62), (96, 190, 225), "START"),
    "Kullanıcı araçları": ((30, 34, 68), (151, 135, 238), "PROFILE"),
    "Yapay zeka": ((32, 24, 67), (155, 112, 245), "AI CORE"),
    "Eğlence ve oyunlar": ((21, 39, 65), (75, 190, 225), "ARCADE"),
    "Ekonomi": ((39, 34, 24), (238, 184, 74), "PAKLAVA"),
    "Profil ve seviye": ((31, 32, 55), (205, 173, 255), "LEVEL UP"),
    "Sunucu yönetimi": ((20, 47, 48), (76, 199, 164), "SERVER"),
    "Güvenlik ve moderasyon": ((52, 25, 33), (235, 102, 112), "SHIELD"),
    "Topluluk ve etkinlik": ((52, 28, 48), (244, 133, 173), "EVENT"),
    "Aktiflik ve araçlar": ((23, 40, 58), (89, 177, 225), "ACTIVITY"),
    "Ultimate araçlar": ((38, 29, 55), (184, 139, 235), "ULTIMATE"),
    "Gelişmiş filtre": ((25, 46, 38), (115, 207, 139), "FILTER"),
    "MEGA sistemler": ((48, 29, 34), (248, 145, 83), "MEGA"),
    "Metin ve dil araçları": ((20, 36, 44), (110, 210, 230), "TEXT"),
    "Rastgele üretim": ((46, 30, 20), (245, 160, 90), "DICE"),
    "Oyun ve bulmaca": ((30, 24, 56), (150, 120, 245), "QUIZ"),
    "Zaman ve hatırlatma": ((20, 44, 36), (96, 220, 170), "TIME"),
    "Sosyal istatistik": ((42, 24, 40), (240, 120, 200), "STATS"),
    "Kişisel notlar": ((38, 38, 22), (225, 215, 110), "NOTE"),
    "Otomatik sistemler": ((24, 30, 46), (120, 170, 240), 'AUTO'),
}


def make_help_category_gif(category):
    """Seçilen kategoriye özel, 20 karelik animasyonlu yardım görseli üretir."""
    import math

    background, accent, motif = HELP_CATEGORY_THEMES.get(category, HELP_CATEGORY_THEMES["Genel"])
    width, height = 720, 220
    frames = []
    try:
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 34)
        small_font = ImageFont.truetype("DejaVuSans.ttf", 16)
        motif_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)
    except OSError:
        title_font = small_font = motif_font = ImageFont.load_default()

    from PIL import ImageFilter
    base_img = Image.new("RGB", (width, height), (5, 5, 7))
    glow_mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(glow_mask).ellipse((380, -60, 860, 280), fill=110)
    glow_mask = glow_mask.filter(ImageFilter.GaussianBlur(60))
    base_img = Image.composite(Image.new("RGB", (width, height), accent), base_img, glow_mask)
    smoke = _smoke_mask(random.Random(category), 30, 0.45, width, height)
    base_img = Image.composite(Image.new("RGB", (width, height), tuple(c // 2 for c in accent)), base_img, smoke)
    for frame_no in range(20):
        image = base_img.copy()
        draw = ImageDraw.Draw(image)

        # Arka plandaki hareketli ışık çizgileri
        shift = (frame_no * 9) % 84
        for x in range(-180, width + 180, 84):
            draw.line((x + shift, 0, x - 95 + shift, height), fill=tuple(min(255, c + 9) for c in background), width=2)

        # Ana panel ve alt ışık şeridi
        panel = tuple(min(255, int(c * 1.22)) for c in background)
        draw.rounded_rectangle((20, 18, 700, 202), radius=25, outline=accent, width=2)
        draw.rounded_rectangle((42, 168, 500, 176), radius=4, fill=tuple(max(0, c - 9) for c in accent))
        progress = int(458 * ((frame_no % 10) / 9))
        if progress:
            draw.rounded_rectangle((42, 168, 42 + progress, 176), radius=4, fill=accent)

        # Sol tarafta kategori başlığı
        draw.text((46, 43), "BAKLAVA1923  /  YARDIM MERKEZİ", font=small_font, fill=(190, 202, 220))
        title = category.upper()
        title_box = draw.textbbox((0, 0), title, font=title_font)
        title_width = title_box[2] - title_box[0]
        fitted_font = title_font
        if title_width > 455:
            try:
                fitted_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 28)
            except OSError:
                fitted_font = title_font
            title_box = draw.textbbox((0, 0), title, font=fitted_font)
            title_width = title_box[2] - title_box[0]
        draw.text((45, 83), title, font=fitted_font, fill=(246, 247, 250))
        draw.text((47, 135), "KOMUTLAR  •  KISA YOLLAR  •  ÖZELLİKLER", font=small_font, fill=accent)

        # Sağ tarafta kategoriye özel sembol ve hareketli halka
        cx, cy = 590, 109
        pulse = int(5 + 7 * (0.5 + 0.5 * math.sin(frame_no * math.pi / 5)))
        for radius in (45 + pulse, 62 + pulse, 79 + pulse):
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=tuple(max(0, c - 12) for c in accent), width=2)
        draw.ellipse((cx - 39, cy - 39, cx + 39, cy + 39), fill=accent)
        motif_box = draw.textbbox((0, 0), motif, font=motif_font)
        motif_w = motif_box[2] - motif_box[0]
        motif_h = motif_box[3] - motif_box[1]
        draw.text((cx - motif_w / 2, cy - motif_h / 2 - 3), motif, font=motif_font, fill=(20, 24, 36))

        # Kayan küçük parıltılar
        for j in range(8):
            px = 525 + ((j * 37 + frame_no * (j % 3 + 1) * 3) % 145)
            py = 28 + ((j * 23 + frame_no * 5) % 158)
            radius = 2 + (j % 2)
            draw.ellipse((px - radius, py - radius, px + radius, py + radius), fill=accent)

        frames.append(image)

    output = io.BytesIO()
    frames[0].save(
        output, format="GIF", save_all=True, append_images=frames[1:],
        duration=95, loop=0, optimize=True, disposal=2,
    )
    output.seek(0)
    category_index = list(HELP_CATEGORIES).index(category) + 1 if category in HELP_CATEGORIES else 0
    return output, f"baklava_yardim_{category_index:02d}.gif"


HELP_CATEGORY_ICONS = {
    "Başlangıç": "🏠",
    "Kullanıcı araçları": "👤",
    "Yapay zeka": "🧠",
    "Eğlence ve oyunlar": "🎮",
    "Ekonomi": "💰",
    "Profil ve seviye": "🏆",
    "Sunucu yönetimi": "🛠️",
    "Güvenlik ve moderasyon": "🛡️",
    "Topluluk ve etkinlik": "🎉",
    "Aktiflik ve araçlar": "📊",
    "Ultimate araçlar": "⚙️",
    "Gelişmiş filtre": "🔎",
    "MEGA sistemler": "🚀",
    "Metin ve dil araçları": "🔤",
    "Rastgele üretim": "🎲",
    "Oyun ve bulmaca": "🧩",
    "Zaman ve hatırlatma": "⏳",
    "Sosyal istatistik": "📈",
    "Kişisel notlar": "📝",
    "Otomatik sistemler": "🤖",
}


def _help_text_chunks(text, limit=950):
    """Discord embed alan sınırlarını aşmadan metni satır satır böler."""
    chunks = []
    current = []
    current_length = 0
    for line in text.splitlines():
        line_length = len(line) + (1 if current else 0)
        if current and current_length + line_length > limit:
            chunks.append("\n".join(current))
            current = [line]
            current_length = len(line)
        else:
            current.append(line)
            current_length += line_length
    if current:
        chunks.append("\n".join(current))
    return chunks or ["Bu kategoride henüz komut bulunmuyor."]


def make_help_embed(category="Genel", image_filename=None):
    theme = HELP_CATEGORY_THEMES.get(category, HELP_CATEGORY_THEMES["Genel"])
    accent = discord.Color.from_rgb(*theme[1])
    if category == "Genel":
        embed = discord.Embed(
            title="🍯 Baklava1923 Bot • Yardım Merkezi",
            description=(
                "Baklava1923 komut rehberine hoş geldin.\n"
                "Kategoriyi seçerek o bölüme özel animasyonu ve komut listesini açabilirsin."
            ),
            color=accent,
        )
        embed.add_field(
            name="📌 Hızlı başlangıç",
            value=(
                "`B!yardım`  — bu menüyü açar\n"
                "`B!ping`  — gecikme ve çalışma süresini gösterir\n"
                "`B!botdurum`  — botun durumunu gösterir\n"
                "`B!sunucubilgi`  — sunucu bilgilerini gösterir\n"
                "`!` ve `b!` önekleri de kullanılabilir"
            ),
            inline=False,
        )
        left = ["**OYUN VE TOPLULUK**"]
        right = ["**SUNUCU VE ARAÇLAR**"]
        for name, (summary, _) in HELP_CATEGORIES.items():
            line = f"{HELP_CATEGORY_ICONS.get(name, '📁')} **{name}** — {summary}"
            if name in {
                "Başlangıç", "Kullanıcı araçları", "Yapay zeka", "Eğlence ve oyunlar", "Ekonomi",
                "Profil ve seviye", "Topluluk ve etkinlik", "Aktiflik ve araçlar",
                "MEGA sistemler",
            }:
                left.append(line)
            else:
                right.append(line)
        embed.add_field(name="Kategoriler · 1", value="\n".join(left), inline=True)
        embed.add_field(name="Kategoriler · 2", value="\n".join(right), inline=True)
        embed.set_footer(text=f"Açılır menüden kategori seç • Baklava1923 Bot {BOT_VERSION}")
        if image_filename:
            embed.set_image(url=f"attachment://{image_filename}")
        return embed

    if category not in HELP_CATEGORIES:
        category = "Başlangıç"
    summary, commands_text = HELP_CATEGORIES[category]
    icon = HELP_CATEGORY_ICONS.get(category, "📁")
    embed = discord.Embed(
        title=f"{icon}  {category}",
        description=f"*{summary}*\n\nKomutları aşağıdan seçip kullanabilirsin.",
        color=accent,
    )
    chunks = _help_text_chunks(commands_text)
    for index, chunk in enumerate(chunks, start=1):
        field_title = "Komutlar" if len(chunks) == 1 else f"Komutlar · {index}/{len(chunks)}"
        embed.add_field(name=field_title, value=chunk, inline=False)
    embed.set_footer(text=f"B!yardım ile ana sayfaya dön • Komutlar B!, ! veya b! ile çalışır • {BOT_VERSION}")
    if image_filename:
        embed.set_image(url=f"attachment://{image_filename}")
    return embed


class HelpCategorySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="Ana Sayfa",
                value="Genel",
                description="Yardım merkezinin sade özeti",
                emoji="🏠",
                default=True,
            )
        ]
        options.extend(
            discord.SelectOption(
                label=name,
                value=name,
                description=summary[:100],
                emoji=HELP_CATEGORY_ICONS.get(name, "📁"),
            )
            for name, (summary, _) in HELP_CATEGORIES.items()
        )
        super().__init__(
            placeholder="Bir yardım kategorisi seç",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if self.view is None:
            await interaction.response.send_message("Bu yardım menüsü artık etkin değil.", ephemeral=True)
            return
        if interaction.user.id != self.view.author_id and not getattr(
            getattr(interaction.user, "guild_permissions", None), "administrator", False
        ):
            await interaction.response.send_message(
                "Bu yardım menüsünü yalnızca başlatan kişi kullanabilir.", ephemeral=True
            )
            return
        selected_category = self.values[0]
        for option in self.options:
            option.default = option.value == selected_category

        if selected_category == "Genel":
            gif_buffer = make_bot_banner_gif()
            filename = "baklava1923_bot.gif"
        else:
            gif_buffer, filename = make_help_category_gif(selected_category)

        embed = make_help_embed(selected_category, image_filename=filename)
        await interaction.response.edit_message(
            embed=embed,
            view=self.view,
            attachments=[discord.File(gif_buffer, filename=filename)],
        )


class HelpCategoryView(discord.ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.add_item(HelpCategorySelect())


@bot.command(name="yardim", aliases=["help", "yardım", "31", "yardim31", "yardım31"])
async def yardim_command(ctx):
    # Ana animasyon menünün üzerinde görünür; her kategori seçildiğinde farklı GIF açılır.
    banner = make_bot_banner_gif()
    await ctx.send(file=discord.File(banner, filename="baklava1923_bot.gif"))
    await ctx.send(embed=make_help_embed(), view=HelpCategoryView(ctx.author.id))



# -------------------- 3.7: MODERASYON KOMUTLARI --------------------
@bot.command(name="ban", aliases=["yasakla"])
@commands.guild_only()
@commands.has_guild_permissions(ban_members=True)
@commands.bot_has_guild_permissions(ban_members=True)
async def ban_member(ctx, oyuncu: discord.Member = None, *, sebep: str = "Sebep belirtilmedi"):
    if oyuncu is None:
        await ctx.send("Kullanım: `B!ban @üye <sebep>`")
        return
    if oyuncu.id == ctx.author.id or oyuncu.id == ctx.guild.owner_id:
        await ctx.send("Kendini veya sunucu sahibini yasaklayamazsın.")
        return
    if ctx.author.id != ctx.guild.owner_id and oyuncu.top_role >= ctx.author.top_role:
        await ctx.send("Kendi rolüne eşit veya daha yüksek roldeki üyeyi yasaklayamazsın.")
        return
    if ctx.guild.me and oyuncu.top_role >= ctx.guild.me.top_role:
        await ctx.send("Botun rolü hedef üyenin rolünden yüksek olmalı.")
        return
    try:
        await ctx.guild.ban(oyuncu, reason=f"{ctx.author}: {sebep[:400]}", delete_message_seconds=0)
        await ctx.send(f"{oyuncu} sunucudan yasaklandı. Sebep: {discord.utils.escape_mentions(sebep)[:400]}")
        await send_security_log(ctx.guild, f"BAN: {oyuncu} ({oyuncu.id}) — yetkili {ctx.author} — sebep {sebep[:400]}")
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Yasaklama başarısız oldu: {type(exc).__name__}. Bot izinlerini ve rol sırasını kontrol et.")


@bot.command(name="kick", aliases=["at"])
@commands.guild_only()
@commands.has_guild_permissions(kick_members=True)
@commands.bot_has_guild_permissions(kick_members=True)
async def kick_member(ctx, oyuncu: discord.Member = None, *, sebep: str = "Sebep belirtilmedi"):
    if oyuncu is None:
        await ctx.send("Kullanım: `B!kick @üye <sebep>`")
        return
    if oyuncu.id == ctx.author.id or oyuncu.id == ctx.guild.owner_id:
        await ctx.send("Kendini veya sunucu sahibini sunucudan atamazsın.")
        return
    if ctx.author.id != ctx.guild.owner_id and oyuncu.top_role >= ctx.author.top_role:
        await ctx.send("Kendi rolüne eşit veya daha yüksek roldeki üyeyi atamazsın.")
        return
    if ctx.guild.me and oyuncu.top_role >= ctx.guild.me.top_role:
        await ctx.send("Botun rolü hedef üyenin rolünden yüksek olmalı.")
        return
    try:
        await oyuncu.kick(reason=f"{ctx.author}: {sebep[:400]}")
        await ctx.send(f"{oyuncu} sunucudan atıldı. Sebep: {discord.utils.escape_mentions(sebep)[:400]}")
        await send_security_log(ctx.guild, f"KICK: {oyuncu} ({oyuncu.id}) — yetkili {ctx.author} — sebep {sebep[:400]}")
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Atma başarısız oldu: {type(exc).__name__}. Bot izinlerini ve rol sırasını kontrol et.")


@bot.command(name="temizle", aliases=["clear", "purge", "mesajtemizle"])
@commands.guild_only()
@commands.has_guild_permissions(manage_messages=True)
@commands.bot_has_guild_permissions(manage_messages=True, read_message_history=True)
async def temizle(ctx, miktar: int = None):
    if miktar is None or not 1 <= miktar <= 100:
        await ctx.send("Kullanım: `B!temizle <1-100>`")
        return
    try:
        deleted = await ctx.channel.purge(limit=miktar + 1, reason=f"{ctx.author} tarafından mesaj temizliği")
        removed = max(0, len(deleted) - (1 if ctx.message.id in {m.id for m in deleted} else 0))
        await ctx.send(f"{removed} mesaj temizlendi.", delete_after=5)
        await send_security_log(ctx.guild, f"Mesaj temizliği: #{ctx.channel} kanalında {removed} mesajı {ctx.author} sildi.")
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Mesajlar silinemedi: {type(exc).__name__}. İzinleri kontrol et.")


# -------------------- 3.7: DOGRULAMA VE ANTI-RAID --------------------
class VerificationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Doğrulamayı tamamla", style=discord.ButtonStyle.success, custom_id="baklava1923:verify:v1")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message("Bu buton sadece sunucuda kullanılabilir.", ephemeral=True)
            return
        cursor.execute("SELECT channel_id,verified_role_id,unverified_role_id FROM verification_settings WHERE guild_id=?", (guild.id,))
        row = cursor.fetchone()
        if not row:
            await interaction.response.send_message("Bu sunucuda doğrulama sistemi ayarlanmamış.", ephemeral=True)
            return
        channel_id, verified_role_id, unverified_role_id = row
        if interaction.channel_id != channel_id:
            await interaction.response.send_message("Doğrulamayı ayarlanan doğrulama kanalında yapmalısın.", ephemeral=True)
            return
        verified_role = guild.get_role(verified_role_id)
        if verified_role is None:
            await interaction.response.send_message("Doğrulanmış üye rolü bulunamadı. Yöneticiden sistemi yenilemesini iste.", ephemeral=True)
            return
        if verified_role in member.roles:
            await interaction.response.send_message("Zaten doğrulanmışsın.", ephemeral=True)
            return
        try:
            await member.add_roles(verified_role, reason="Baklava1923 doğrulama butonu")
            unverified_role = guild.get_role(unverified_role_id) if unverified_role_id else None
            if unverified_role and unverified_role in member.roles:
                await member.remove_roles(unverified_role, reason="Üye doğrulamayı tamamladı")
            await interaction.response.send_message("Doğrulama tamamlandı. Sunucuya hoş geldin.", ephemeral=True)
            await send_security_log(guild, f"Üye doğrulandı: {member} ({member.id})")
        except (discord.Forbidden, discord.HTTPException) as exc:
            await interaction.response.send_message("Rol verilemedi. Botun Rolleri Yönet iznini ve rol sırasını kontrol edin.", ephemeral=True)
            print(f"DOGRULAMA HATASI: {type(exc).__name__}: {exc}")


@bot.command(name="doğrulama", aliases=["dogrulama", "verifysetup"])
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True, manage_roles=True)
async def dogrulama(ctx, kanal: discord.TextChannel = None, dogrulanmis_rol: discord.Role = None, bekleyen_rol: discord.Role = None):
    if kanal is None or dogrulanmis_rol is None:
        cursor.execute("SELECT channel_id,verified_role_id,unverified_role_id FROM verification_settings WHERE guild_id=?", (ctx.guild.id,))
        row = cursor.fetchone()
        if row:
            pending = f"<@&{row[2]}>" if row[2] else "Ayarlanmamış"
            await ctx.send(f"Doğrulama açık: <#{row[0]}> kanalında <@&{row[1]}> rolü veriliyor. Bekleyen rol: {pending}.")
        else:
            await ctx.send("Kullanım: `B!doğrulama #kanal @doğrulanmış-rol [@bekleyen-rol]`\nÖnce bekleyen rolün kanallara erişimini kapat, doğrulanmış rolün erişimini aç.")
        return
    me = ctx.guild.me
    if me is None or not me.guild_permissions.manage_roles:
        await ctx.send("Botta Rolleri Yönet izni olmalı.")
        return
    if dogrulanmis_rol >= me.top_role or (bekleyen_rol and bekleyen_rol >= me.top_role):
        await ctx.send("Seçilen roller botun en üst rolünün altında olmalı.")
        return
    if bekleyen_rol and bekleyen_rol.id == dogrulanmis_rol.id:
        await ctx.send("Bekleyen rol ile doğrulanmış rol aynı olamaz.")
        return
    cursor.execute(
        "INSERT INTO verification_settings (guild_id,channel_id,verified_role_id,unverified_role_id) VALUES (?,?,?,?) "
        "ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id,verified_role_id=excluded.verified_role_id,unverified_role_id=excluded.unverified_role_id",
        (ctx.guild.id, kanal.id, dogrulanmis_rol.id, bekleyen_rol.id if bekleyen_rol else None),
    )
    db.commit()
    embed = discord.Embed(
        title="Sunucu Doğrulaması",
        description="Sunucuya erişim için aşağıdaki düğmeye basarak doğrulamayı tamamla.\nDoğrulama tamamlandığında üye rolün verilir.",
        color=discord.Color.green(),
    )
    await kanal.send(embed=embed, view=VerificationView())
    await ctx.send(f"Doğrulama paneli {kanal.mention} kanalına gönderildi. Yeni üyeler için bekleyen rol: {bekleyen_rol.mention if bekleyen_rol else 'kullanılmıyor'}.")
    await send_security_log(ctx.guild, f"Doğrulama ayarlandı: kanal=#{kanal}, üye rolü={dogrulanmis_rol}, bekleyen rol={bekleyen_rol or 'yok'}")


@bot.command(name="raidkoruma", aliases=["antiraid", "raid"])
@commands.guild_only()
@commands.has_guild_permissions(administrator=True)
async def raidkoruma(ctx, islem: str = None, miktar: int = None):
    cfg = ensure_raid_settings(ctx.guild.id)
    if islem is None or islem.lower() in {"durum", "status"}:
        active = "AKTİF" if cfg["active_until"] > time.time() else "pasif"
        embed = discord.Embed(title="Anti-Raid Koruması", color=discord.Color.red())
        embed.description = (
            f"Koruma: {'Açık' if cfg['enabled'] else 'Kapalı'}\nRaid modu: **{active}**\n"
            f"Eşik: {cfg['threshold']} katılım / {cfg['window_seconds']} saniye\n"
            f"Raid modu süresi: {cfg['mode_minutes']} dakika\n\n"
            "Raid algılanınca güvenlik logu ve bot sahibine uyarı gönderilir. Doğrulama sistemi ayarlıysa yeni üyeler bekleyen rolde tutulur; bot otomatik toplu ban/kick atmaz.\n"
            "`B!raidkoruma aç/kapat` · `B!raidkoruma eşik 8` · `B!raidkoruma pencere 20` · `B!raidkoruma süre 10` · `B!raidkoruma sıfırla`"
        )
        await ctx.send(embed=embed)
        return
    op = islem.lower().strip()
    if op in {"aç", "ac", "on"}:
        cursor.execute("UPDATE raid_settings SET enabled=1 WHERE guild_id=?", (ctx.guild.id,))
        db.commit()
        await ctx.send("Anti-raid koruması açıldı.")
    elif op in {"kapat", "off"}:
        cursor.execute("UPDATE raid_settings SET enabled=0,active_until=0 WHERE guild_id=?", (ctx.guild.id,))
        db.commit()
        await ctx.send("Anti-raid koruması kapatıldı.")
    elif op in {"eşik", "esik"}:
        if miktar is None or not 4 <= miktar <= 30:
            await ctx.send("Eşik 4 ile 30 arasında olmalı.")
            return
        cursor.execute("UPDATE raid_settings SET threshold=? WHERE guild_id=?", (miktar, ctx.guild.id))
        db.commit()
        await ctx.send(f"Raid algılama eşiği {miktar} katılım olarak ayarlandı.")
    elif op == "pencere":
        if miktar is None or not 5 <= miktar <= 60:
            await ctx.send("Pencere 5 ile 60 saniye arasında olmalı.")
            return
        cursor.execute("UPDATE raid_settings SET window_seconds=? WHERE guild_id=?", (miktar, ctx.guild.id))
        db.commit()
        await ctx.send(f"Raid algılama penceresi {miktar} saniye olarak ayarlandı.")
    elif op in {"süre", "sure"}:
        if miktar is None or not 1 <= miktar <= 60:
            await ctx.send("Raid modu 1 ile 60 dakika arasında olmalı.")
            return
        cursor.execute("UPDATE raid_settings SET mode_minutes=? WHERE guild_id=?", (miktar, ctx.guild.id))
        db.commit()
        await ctx.send(f"Raid modu süresi {miktar} dakika olarak ayarlandı.")
    elif op in {"sıfırla", "sifirla", "normale"}:
        cursor.execute("UPDATE raid_settings SET active_until=0 WHERE guild_id=?", (ctx.guild.id,))
        db.commit()
        await ctx.send("Raid modu normale döndürüldü.")
    else:
        await ctx.send("Kullanım: `B!raidkoruma [durum/aç/kapat/eşik/pencere/süre/sıfırla] [sayı]`")


# -------------------- SUNUCU YONETIMI --------------------
def admin_check(ctx):
    return ctx.author.guild_permissions.administrator or ctx.author.id == OWNER_ID


class ServerMenuView(discord.ui.View):
    def __init__(self, guild_id, owner_id):
        super().__init__(timeout=120)
        self.guild_id, self.owner_id = guild_id, owner_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Bu menu sadece komutu kullanan yoneticiye acik.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Sunucu Bilgisi", style=discord.ButtonStyle.primary)
    async def info(self, interaction, button):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Bu menu sunucuda kullanilir.", ephemeral=True)
            return
        embed = discord.Embed(title=guild.name, color=discord.Color.blurple())
        embed.add_field(name="Uye sayisi", value=str(guild.member_count or 0))
        embed.add_field(name="Kanal sayisi", value=str(len(guild.channels)))
        embed.add_field(name="Rol sayisi", value=str(len(guild.roles)))
        embed.add_field(name="Olusturulma", value=discord.utils.format_dt(guild.created_at, style="D"), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Guvenlik Durumu", style=discord.ButtonStyle.danger)
    async def security(self, interaction, button):
        s = ensure_guild_settings(interaction.guild_id)
        await interaction.response.send_message(
            f"Spam korumasi: {'Acik' if s['protection_enabled'] else 'Kapali'}\n"
            f"Esik: {s['spam_limit']} mesaj / {s['spam_window']} saniye\n"
            f"Sureli susturma: {s['timeout_minutes']} dakika\n"
            f"Kayit kanali: <#{s['log_channel_id']}>" if s["log_channel_id"] else
            f"Spam korumasi: {'Acik' if s['protection_enabled'] else 'Kapali'}\nEsik: {s['spam_limit']} mesaj / {s['spam_window']} saniye\nSureli susturma: {s['timeout_minutes']} dakika\nKayit kanali ayarlanmamis.",
            ephemeral=True,
        )

    @discord.ui.button(label="Temel Kanallari Kur", style=discord.ButtonStyle.success)
    async def setup(self, interaction, button):
        if not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message("Kanal yonetme iznin olmali.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        made = await create_basic_channels(interaction.guild)
        await interaction.followup.send(f"Kurulum tamamlandi. Olusturulan kanal sayisi: {made}. Var olan kanallar atlandi.", ephemeral=True)

    @discord.ui.button(label="Yardim Komutlari", style=discord.ButtonStyle.secondary)
    async def commands_list(self, interaction, button):
        await interaction.response.send_message(
            "`B!sunucubilgi` sunucu bilgisi\n`B!sunucuoluştur` tam sunucu şablonu\n`B!sunucukur` temel kanal kurulumu\n`!gelengiden #kanal` GIF ile giriş/çıkış bildirimi\n"
            "`B!kilit` / `B!kilitac` kanal kilidi\n`B!kanaloluştur <isim>`\n"
            "`B!rololuştur <isim>`\n`B!oto-rol @rol`\n`B!sunuculog #kanal`\n"
            "`B!sunucukoruma` guvenlik paneli",
            ephemeral=True,
        )


async def create_basic_channels(guild):
    """Var olanları silmeden eksik temel kanalları ekler."""
    created = 0
    defaults = [
        ("genel", discord.ChannelType.text),
        ("duyurular", discord.ChannelType.text),
        ("sohbet", discord.ChannelType.text),
        ("sesli-sohbet", discord.ChannelType.voice),
    ]
    existing = {(c.name.lower(), c.type) for c in guild.channels}
    for name, kind in defaults:
        if (name, kind) in existing:
            continue
        try:
            if kind == discord.ChannelType.voice:
                await guild.create_voice_channel(name, reason="Baklava1923 Bot 3.6 temel kurulum")
            else:
                await guild.create_text_channel(name, reason="Baklava1923 Bot 3.6 temel kurulum")
            created += 1
        except (discord.Forbidden, discord.HTTPException):
            break
    return created


SERVER_LAYOUT = [
    ("📌・BİLGİ MERKEZİ", ["👋・hoş-geldin", "📢・duyurular", "📜・kurallar", "📋・sunucu-bilgisi", "🎭・rol-seçim"]),
    ("💬・TOPLULUK", ["💬・genel-sohbet", "😂・meme", "📸・medya", "🤖・bot-komutları", "💡・öneriler"]),
    ("🎮・OYUN ALANI", ["🎮・oyun-sohbeti", "🏆・başarılar", "🔎・oyuncu-ara", "🎲・etkinlikler"]),
    ("🎫・DESTEK", ["🎫・destek-talebi", "❓・yardım"]),
    ("🔊・SES ODALARI", ["🔊・Genel Ses", "🎵・Müzik", "🎮・Oyun Odası", "💤・AFK"]),
    ("🛡️・YÖNETİM", ["🛡️・yönetim-sohbeti", "🧾・mod-log", "🚨・güvenlik-log", "📨・şikayetler"]),
]
SERVER_ROLES = [
    ("『👑』KURUCU", 0xF1C40F), ("『⚜️』YÖNETİM KURULU", 0xE67E22),
    ("『🛡️』BAŞ MODERATÖR", 0xE74C3C), ("『🔨』MODERATÖR", 0xC0392B),
    ("『🎫』DESTEK EKİBİ", 0x1ABC9C), ("『🤖』BOT", 0x95A5A6),
    ("『💎』ELMAS VIP", 0x3498DB), ("『👑』ALTIN VIP", 0xF39C12),
    ("『🔥』EFSANE", 0xE74C3C), ("『🌟』YILDIZ", 0x9B59B6),
    ("『🎨』İÇERİK ÜRETİCİ", 0xE91E63), ("『🎥』YAYINCI", 0x9146FF),
    ("『🏆』ŞAMPİYON", 0xD4AC0D), ("『🎮』OYUNCU", 0x2ECC71),
    ("『💬』AKTİF ÜYE", 0x16A085), ("『🎉』ETKİNLİKÇİ", 0xFF7043),
    ("『🤝』DOST", 0x5DADE2), ("『🌱』YENİ GELEN", 0xAAB7B8),
    ("『🔇』SUSTURULMUŞ", 0x7F8C8D), ("『🔔』DUYURU BİLDİRİMİ", 0xF7DC6F),
    ("『🎵』MÜZİK", 0xAF7AC5), ("『🧪』TEST EKİBİ", 0x48C9B0),
]


class ServerResetConfirmView(discord.ui.View):
    def __init__(self, guild_id, author_id):
        super().__init__(timeout=45)
        self.guild_id = guild_id
        self.author_id = author_id

    async def interaction_check(self, interaction):
        if interaction.guild_id != self.guild_id:
            await interaction.response.send_message("Bu onay baska bir sunucuya ait.", ephemeral=True)
            return False
        if interaction.user.id != self.author_id and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Bu islemi sadece komutu baslatan yonetici onaylayabilir.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Sunucuyu Sifirla ve Kur", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Bu islem sadece sunucuda kullanilir.", ephemeral=True)
            return
        me = guild.me
        if me is None or not me.guild_permissions.manage_channels or not me.guild_permissions.manage_roles:
            await interaction.response.send_message("Botta Kanallari Yonet ve Rolleri Yonet izinleri olmali.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        for child in self.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=self)
        except (discord.Forbidden, discord.HTTPException):
            pass

        deleted_channels = 0
        failed_channels = 0
        # Kategoriler de dahil mevcut tum kanallari sil.
        for channel in list(guild.channels):
            try:
                await channel.delete(reason="Baklava1923 Bot: kullanici onayli sunucu sifirlama")
                deleted_channels += 1
            except (discord.Forbidden, discord.HTTPException):
                failed_channels += 1

        deleted_roles = 0
        failed_roles = 0
        # @everyone, entegrasyon/bot tarafindan yonetilen ve botun hiyerarsisindeki roller atlanir.
        for role in sorted(list(guild.roles), key=lambda item: item.position, reverse=True):
            if role.is_default() or role.managed or role >= me.top_role:
                continue
            try:
                await role.delete(reason="Baklava1923 Bot: kullanici onayli sunucu sifirlama")
                deleted_roles += 1
            except (discord.Forbidden, discord.HTTPException):
                failed_roles += 1

        role_created = 0
        role_map = {}
        # Roller icin temel Discord izinleri. Yalnizca guvenilen yonetim rollerine
        # yonetim yetkisi verilir; VIP ve normal uye rolleri yonetici olmaz.
        member_permissions = discord.Permissions(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            add_reactions=True,
            embed_links=True,
            attach_files=True,
            use_application_commands=True,
            connect=True,
            speak=True,
        )
        role_permissions = {
            "『👑』KURUCU": discord.Permissions(administrator=True),
            "『⚜️』YÖNETİM KURULU": discord.Permissions(
                manage_channels=True, manage_roles=True, manage_messages=True,
                view_audit_log=True, kick_members=True, moderate_members=True,
                view_channel=True, send_messages=True, read_message_history=True,
            ),
            "『🛡️』BAŞ MODERATÖR": discord.Permissions(
                manage_messages=True, moderate_members=True, view_audit_log=True,
                view_channel=True, send_messages=True, read_message_history=True,
            ),
            "『🔨』MODERATÖR": discord.Permissions(
                manage_messages=True, moderate_members=True,
                view_channel=True, send_messages=True, read_message_history=True,
            ),
            "『🎫』DESTEK EKİBİ": discord.Permissions(
                manage_messages=True, read_message_history=True,
                view_channel=True, send_messages=True,
            ),
            "『🤖』BOT": discord.Permissions(view_audit_log=True),
            "『💎』ELMAS VIP": member_permissions,
            "『👑』ALTIN VIP": member_permissions,
            "『🔥』EFSANE": member_permissions,
            "『🌟』YILDIZ": member_permissions,
            "『🎨』İÇERİK ÜRETİCİ": member_permissions,
            "『🎥』YAYINCI": member_permissions,
            "『🏆』ŞAMPİYON": member_permissions,
            "『🎮』OYUNCU": member_permissions,
            "『💬』AKTİF ÜYE": member_permissions,
            "『🎉』ETKİNLİKÇİ": member_permissions,
            "『🤝』DOST": member_permissions,
            "『🌱』YENİ GELEN": member_permissions,
            "『🔇』SUSTURULMUŞ": discord.Permissions(view_channel=True, read_message_history=True),
            "『🔔』DUYURU BİLDİRİMİ": discord.Permissions(view_channel=True, read_message_history=True),
            "『🎵』MÜZİK": member_permissions,
            "『🧪』TEST EKİBİ": member_permissions,
        }
        for name, color in SERVER_ROLES:
            try:
                role = await guild.create_role(
                    name=name,
                    colour=discord.Colour(color),
                    permissions=role_permissions.get(name, discord.Permissions.none()),
                    reason="Baklava1923 Bot: sifirlama sonrasi rol sablonu",
                )
                role_map[name] = role
                role_created += 1
            except (discord.Forbidden, discord.HTTPException):
                continue

        category_created = 0
        channel_created = 0
        for category_name, channel_names in SERVER_LAYOUT:
            try:
                category = await guild.create_category(category_name, reason="Baklava1923 Bot: sifirlama sonrasi sunucu sablonu")
                category_created += 1
            except (discord.Forbidden, discord.HTTPException):
                continue
            for channel_name in channel_names:
                kind = discord.ChannelType.voice if any(x in channel_name for x in ("Ses", "Müzik", "Oyun Odası", "AFK")) else discord.ChannelType.text
                try:
                    if kind == discord.ChannelType.voice:
                        await guild.create_voice_channel(channel_name, category=category, reason="Baklava1923 Bot: sifirlama sonrasi sunucu sablonu")
                    else:
                        await guild.create_text_channel(channel_name, category=category, reason="Baklava1923 Bot: sifirlama sonrasi sunucu sablonu")
                    channel_created += 1
                except (discord.Forbidden, discord.HTTPException):
                    continue

        await interaction.followup.send(
            "**Sunucu sifirlama ve kurulum islemi bitti**\n"
            f"Silinen kanallar/kategoriler: **{deleted_channels}**\n"
            f"Silinen roller: **{deleted_roles}**\n"
            f"Olusturulan kategoriler: **{category_created}**\n"
            f"Olusturulan kanallar: **{channel_created}**\n"
            f"Olusturulan roller: **{role_created}**\n"
            f"Silinemeyen kanal: **{failed_channels}**, silinemeyen rol: **{failed_roles}**\n"
            "Not: @everyone ve bot/entegrasyon tarafindan yonetilen roller Discord kurallari nedeniyle silinemez. "
            "Kurucu rolu yonetici yetkisi tasir; bu rolu yalnizca guvendigin kisilere ver.",
            ephemeral=True,
        )
        self.stop()

    @discord.ui.button(label="Vazgec", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Sunucu sifirlama iptal edildi. Hicbir kanal veya rol silinmedi.", view=self)
        self.stop()


SERVER_TEMPLATES = {
    "topluluk": {
        "label": "Topluluk Sunucusu", "description": "Genel sohbet, duyuru, etkinlik ve destek",
        "roles": [("👑・Sunucu Lideri", 0xE74C3C, "leader"), ("🛡️・Yönetici Ekibi", 0xC0392B, "manager"), ("🔱・Baş Moderatör", 0xE67E22, "moderator"), ("🔨・Moderatör", 0xD35400, "moderator"), ("🆘・Destek Ekibi", 0x1ABC9C, "helper"), ("🎉・Etkinlik Ekibi", 0x9B59B6, "event"), ("💎・VIP", 0xF1C40F, "vip"), ("🌟・Üye", 0x3498DB, "member"), ("🌱・Yeni Üye", 0x95A5A6, "newmember")],
        "categories": {"📌 BİLGİLENDİRME": [("📢・duyurular", "text"), ("📜・kurallar", "text"), ("👋・hoş-geldin", "text")], "💬 TOPLULUK": [("💬・genel-sohbet", "text"), ("📸・medya", "text"), ("🎉・etkinlikler", "text"), ("🔊・Genel Ses", "voice")], "🛠️ DESTEK": [("🎫・destek", "text"), ("🤖・bot-komutlari", "text")]},
    },
    "oyun": {
        "label": "Oyun Sunucusu", "description": "Oyun sohbeti, ekip bulma ve ses odaları",
        "roles": [("👑・Oyun Lideri", 0xE74C3C, "manager"), ("🔱・Baş Moderatör", 0xE67E22, "moderator"), ("🔨・Moderatör", 0xD35400, "moderator"), ("🏆・Turnuva Ekibi", 0x9B59B6, "event"), ("🎥・Yayıncı", 0x9146FF, "creator"), ("💎・VIP Oyuncu", 0xF1C40F, "vip"), ("🎮・Oyuncu", 0x2ECC71, "member"), ("🌱・Yeni Oyuncu", 0x95A5A6, "newmember")],
        "categories": {"🎮 OYUN": [("🎮・oyun-sohbeti", "text"), ("👥・ekip-ara", "text"), ("📷・oyun-kareleri", "text")], "🔊 SES ODALARI": [("🔊・Lobi", "voice"), ("🎯・Takım-1", "voice"), ("🎯・Takım-2", "voice")], "📌 BİLGİLENDİRME": [("📢・duyurular", "text"), ("📜・kurallar", "text")]},
    },
    "countryballs": {
        "label": "Countryballs / İçerik Üretici", "description": "Video fikirleri, paylasim ve topluluk",
        "roles": [("👑・Kanal Sahibi", 0xE74C3C, "manager"), ("🔨・Moderatör", 0xD35400, "moderator"), ("🎬・Video Editörü", 0x9146FF, "creator"), ("🗺️・Haritacı", 0x2ECC71, "creator"), ("Senarist", 0x1ABC9C, "creator"), ("🌍・Countryballs Üreticisi", 0xE91E63, "vip"), ("🌟・Üye", 0x3498DB, "member"), ("🌱・Yeni Üye", 0x95A5A6, "newmember")],
        "categories": {"🌍 COUNTRYBALLS": [("🌍・countryballs-sohbet", "text"), ("💡・video-fikirleri", "text"), ("🎬・video-paylasim", "text"), ("🗺️・haritalar", "text")], "📢 KANAL": [("📢・yeni-videolar", "text"), ("📊・anketler", "text")], "🔊 SOHBET": [("🔊・Muhabbet", "voice")]},
    },
    "arkadas": {
        "label": "Arkadaş Grubu", "description": "Sade sohbet, meme ve sesli oda",
        "roles": [("👑・Grup Sahibi", 0xE74C3C, "manager"), ("🛡️・Grup Moderatörü", 0xE67E22, "moderator"), ("🎉・Etkinlikçi", 0x9B59B6, "event"), ("💎・VIP Dost", 0xF1C40F, "vip"), ("🤝・Arkadaş", 0x3498DB, "member"), ("🌱・Yeni Gelen", 0x95A5A6, "newmember")],
        "categories": {"💬 MUHABBET": [("💬・sohbet", "text"), ("😂・memeler", "text"), ("📷・fotoğraflar", "text")], "🎮 AKTİVİTE": [("🎮・oyunlar", "text"), ("📊・anket", "text"), ("🔊・Sesli Sohbet", "voice")]},
    },
    "minimal": {
        "label": "Minimal Sunucu", "description": "Az kanal, kolay kullanim",
        "roles": [("🔨・Moderatör", 0xE67E22, "moderator"), ("💎・VIP", 0xF1C40F, "vip"), ("🌟・Üye", 0x3498DB, "member"), ("🌱・Yeni Üye", 0x95A5A6, "newmember")],
        "categories": {"📌 GENEL": [("📢・duyurular", "text"), ("📜・kurallar", "text"), ("💬・sohbet", "text"), ("🤖・bot-komutlari", "text")], "🔊 SES": [("🔊・Genel", "voice")]},
    },
}

# Otuz yeni, birbirinden farkli sunucu şablonu
_EXTRA_TEMPLATES = [
    ("minecraft", "Minecraft", "Survival, SMP ve yapi toplulugu", "⛏️", ["survival", "yapi-paylasim", "pvp-arena", "mod-paketleri" ]),
    ("roblox", "Roblox", "Roblox oyunlari ve ekip bulma", "🟥", ["roblox-sohbet", "oyun-önerileri", "ekip-bul", "avatarlar"]),
    ("amongus", "Among Us", "Lobi, kod paylasimi ve oyun geceleri", "🚀", ["lobi-duyuru", "oda-kodlari", "oyuncu-ara", "komik-anlar"]),
    ("youtuber", "YouTuber / İçerik Üretici", "Video, Shorts ve yayin ekibi", "🎬", ["video-fikirleri", "yeni-videolar", "kapak-tasarim", "istatistikler"]),
    ("anime", "Anime Toplulugu", "Anime sohbeti, öneriler ve izleme listeleri", "🌸", ["anime-sohbet", "öneriler", "fan-art", "spoiler-alani"]),
    ("muzik", "Müzik Toplulugu", "Müzik önerileri ve sanatci sohbeti", "🎵", ["muzik-oner", "yeni-sarkilar", "sanatcilar", "paylasimlar"]),
    ("film", "Film ve Dizi", "Film önerileri ve bolum tartismalari", "🎥", ["film-önerileri", "dizi-sohbet", "incelemeler", "spoiler"]),
    ("teknoloji", "Teknoloji", "Donanim, yazilim ve teknoloji haberleri", "💻", ["teknoloji-sohbet", "donanim", "yazilim", "proje-paylasim"]),
    ("yazilim", "Yazilim Gelistirme", "Kodlama, projeler ve yardım kanallari", "⌨️", ["kodlama-sohbet", "yardım-iste", "projeler", "kaynaklar"]),
    ("tasarim", "Grafik Tasarim", "Logo, banner ve tasarim paylasimi", "🎨", ["tasarim-sohbet", "is-galerisi", "geri-bildirim", "kaynaklar"]),
    ("okul", "Okul Arkadaşlari", "Ders çalışma ve okul sohbeti", "📚", ["ders-sohbet", "ödev-yardım", "sinav-takvimi", "mola-sohbet"]),
    ("bilim", "Bilim ve Uzay", "Bilim, astronomi ve kesifler", "🔭", ["bilim-sohbet", "uzay", "kesifler", "sorular"]),
    ("tarih", "Tarih Toplulugu", "Tarihi olaylar, haritalar ve tartisma", "🏛️", ["tarih-sohbet", "haritalar", "tarihi-kisiler", "kaynaklar"]),
    ("harita", "Mapping / Haritacılik", "Harita projeleri ve senaryolar", "🗺️", ["harita-paylasim", "senaryo-fikirleri", "ulke-tartisma", "projeler"]),
    ("countryballsstudio", "Countryballs Studio", "Animasyon, senaryo ve ekip çalışmasi", "🌐", ["senaryo", "animasyon-yardım", "karakterler", "eserler"]),
    ("rp", "Roleplay", "Karakterler, hikaye ve rol yapma", "🎭", ["rol-baslangic", "karakter-kartlari", "hikaye", "rol-disinda"]),
    ("fantastik", "Fantastik Evren", "Fantastik hikayeler ve dunya kurma", "🐉", ["evren-lore", "karakterler", "haritalar", "hikaye-yazim"]),
    ("spor", "Spor Toplulugu", "Takımlar, mac sohbeti ve spor", "🏆", ["spor-sohbet", "mac-gunu", "tahmin-degil-yorum", "spor-klipleri"]),
    ("futbol", "Futbol", "Futbol sohbeti ve takim kanallari", "⚽", ["futbol-sohbet", "transferler", "mac-yorumlari", "taraftar-kosesi"]),
    ("espor", "E-Spor Takımi", "Takım organizasyonu ve antrenman", "🎯", ["takim-duyuru", "scrim-plan", "taktikler", "mac-analiz"]),
    ("toplanti", "Proje Ekibi", "Görevler, toplantilar ve proje takibi", "📋", ["proje-duyuru", "gorevler", "toplanti-notlari", "teslimler"]),
    ("destek", "Destek Merkezi", "Yardim masasi ve destek ekipleri", "🛟", ["destek-duyuru", "sik-sorulanlar", "yardım-iste", "cozumler"]),
    ("magaza", "Topluluk / Magaza", "Duyuru, urun destegi ve geri bildirim", "🛍️", ["magaza-duyuru", "urunler", "destek", "geri-bildirim"]),
    ("podcast", "Podcast", "Bolum planlama ve sesli sohbet", "🎙️", ["bolum-fikirleri", "kayit-plan", "bolum-paylasim", "dinleyici-yorum"]),
    ("fotograf", "Fotografcilik", "Fotograf paylasimi ve teknik yardım", "📷", ["fotograf-galerisi", "teknik-yardım", "duzenleme", "haftalik-tema"]),
    ("yazi", "Yazarlar", "Hikaye, siir ve yazi atolyeleri", "✍️", ["yazi-atolyesi", "hikayeler", "geri-bildirim", "yazma-gorevi"]),
    ("yemek", "Yemek Toplulugu", "Tarifler, yemek fotolari ve öneriler", "🍲", ["tarifler", "yemek-fotolari", "öneriler", "mutfak-sohbet"]),
    ("doga", "Doga ve Hayvanlar", "Doga gozlemleri ve hayvan sevgisi", "🌿", ["doga-fotolari", "hayvanlar", "gozlemler", "soru-cevap"]),
    ("etkinlik", "Etkinlik ve Turnuva", "Etkinlik takvimi ve katilim", "🎉", ["etkinlik-duyuru", "kayitlar", "takvim", "sonuclar"]),
    ("meme", "Meme / Mizah", "Meme, komik paylasim ve sohbet", "😂", ["meme-akisi", "komik-videolar", "absurt-fikirler", "sohbet"]),
]

_ROLE_PALETTE = [0xE74C3C, 0xE67E22, 0xF1C40F, 0x2ECC71, 0x1ABC9C, 0x3498DB, 0x9B59B6, 0xE91E63, 0x95A5A6]
for _idx, (_key, _name, _desc, _emoji, _channels) in enumerate(_EXTRA_TEMPLATES):
    _base = _key.replace("_", "-")
    SERVER_TEMPLATES[_key] = {
        "label": f"{_emoji} {_name}", "description": _desc,
        "roles": [
            (f"👑・{_name} Sahibi", _ROLE_PALETTE[_idx % len(_ROLE_PALETTE)], "leader"),
            ("🛡️・Yönetim Ekibi", 0xC0392B, "manager"),
            ("🔱・Baş Moderatör", 0xE67E22, "moderator"),
            ("🔨・Moderatör", 0xD35400, "moderator"),
            ("🆘・Destek Ekibi", 0x1ABC9C, "helper"),
            ("🎉・Etkinlik Ekibi", 0x9B59B6, "event"),
            ("🎨・İçerik Üreticisi", 0xE91E63, "creator"),
            ("💎・VIP", 0xF1C40F, "vip"),
            ("🌟・Üye", 0x3498DB, "member"),
            ("🌱・Yeni Üye", 0x95A5A6, "newmember"),
        ],
        "categories": {
            f"{_emoji}・{_name.upper()}": [(f"{_emoji}・{_channels[0]}", "text"), (f"💬・{_channels[1]}", "text"), (f"📸・{_channels[2]}", "text"), (f"📁・{_channels[3]}", "text")],
            "📢・DUYURU VE REHBER": [("📢・duyurular", "text"), ("📜・kurallar", "text"), ("👋・hoş-geldin", "text")],
            "🔊・SES ODALARI": [("🔊・Genel Sohbet", "voice"), ("🎙️・Ekip Odasi", "voice")],
            "🧰・DESTEK": [("🆘・yardım", "text"), ("🤖・bot-komutlari", "text")],
        },
    }


class ServerTemplateSelect(discord.ui.Select):
    def __init__(self, items, placeholder, row):
        options = [discord.SelectOption(label=data["label"][:100], value=key, description=data["description"][:100])
                   for key, data in items]
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options, row=row)

    async def callback(self, interaction: discord.Interaction):
        template_key = self.values[0]
        template = SERVER_TEMPLATES[template_key]
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Bu secim yalnizca sunucuda kullanilabilir.", ephemeral=True)
            return
        if not interaction.user.guild_permissions.manage_channels or not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("Sablon icin Kanal Yonetme ve Rol Yonetme izinlerin olmali.", ephemeral=True)
            return
        me = guild.me
        if me is None or not me.guild_permissions.manage_channels or not me.guild_permissions.manage_roles:
            await interaction.response.send_message("Botta Kanallari Yonet ve Rolleri Yonet izinleri eksik.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        created_categories = created_channels = created_roles = failed = 0
        permission_sets = {
            # Guvenli: hicbir sablon rolu Administrator veya Manage Guild alamaz.
            "leader": discord.Permissions(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True, manage_channels=True, manage_roles=True, moderate_members=True, kick_members=True, view_audit_log=True, manage_events=True),
            "manager": discord.Permissions(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True, manage_channels=True, moderate_members=True, manage_events=True, view_audit_log=True),
            "moderator": discord.Permissions(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True, moderate_members=True, manage_threads=True, manage_nicknames=True),
            "helper": discord.Permissions(view_channel=True, send_messages=True, read_message_history=True, embed_links=True, attach_files=True, manage_messages=True),
            "event": discord.Permissions(view_channel=True, send_messages=True, read_message_history=True, embed_links=True, attach_files=True, manage_events=True, add_reactions=True),
            "creator": discord.Permissions(view_channel=True, send_messages=True, read_message_history=True, embed_links=True, attach_files=True, add_reactions=True, use_external_emojis=True, use_external_stickers=True),
            "vip": discord.Permissions(view_channel=True, send_messages=True, read_message_history=True, embed_links=True, attach_files=True, add_reactions=True, connect=True, speak=True, use_application_commands=True, use_external_emojis=True),
            "member": discord.Permissions(view_channel=True, send_messages=True, read_message_history=True, add_reactions=True, embed_links=True, attach_files=True, connect=True, speak=True, use_application_commands=True),
            "newmember": discord.Permissions(view_channel=True, read_message_history=True, connect=True),
        }
        role_by_name = {role.name.casefold(): role for role in guild.roles}
        member_role = None
        for role_name, role_color, role_kind in template.get("roles", []):
            plain_role_name = role_name.split("・", 1)[-1].casefold()
            role = role_by_name.get(role_name.casefold()) or role_by_name.get(plain_role_name)
            if role is None:
                try:
                    role = await guild.create_role(name=role_name[:100], colour=discord.Colour(role_color), permissions=permission_sets[role_kind], reason=f"Baklava1923 Bot {template_key} sablon rolleri")
                    created_roles += 1
                    role_by_name[role_name.casefold()] = role
                except (discord.Forbidden, discord.HTTPException):
                    failed += 1
                    continue
            elif role.name != role_name:
                try:
                    await role.edit(name=role_name[:100], colour=discord.Colour(role_color), reason="Baklava1923 Bot: rol adini ve rengini duzenleme")
                    role_by_name.pop(role.name.casefold(), None)
                    role_by_name[role_name.casefold()] = role
                except (discord.Forbidden, discord.HTTPException):
                    pass
            if role_kind == "member" and member_role is None:
                member_role = role
        settings = ensure_guild_settings(guild.id)
        if not settings["autorole_id"] and member_role and guild.me and member_role < guild.me.top_role:
            set_guild_setting(guild.id, "autorole_id", member_role.id)
        existing_categories = {c.name.casefold(): c for c in guild.categories}
        for category_name, channels in template["categories"].items():
            category = existing_categories.get(category_name.casefold())
            if category is None:
                try:
                    category = await guild.create_category(category_name, reason="Baklava1923 Bot sunucu sablonu")
                    existing_categories[category_name.casefold()] = category
                    created_categories += 1
                except (discord.Forbidden, discord.HTTPException):
                    failed += 1
                    continue
            existing = {(c.name.casefold(), "text" if isinstance(c, discord.TextChannel) else "voice" if isinstance(c, discord.VoiceChannel) else str(c.type)) for c in category.channels}
            for channel_name, kind in channels:
                if (channel_name.casefold(), kind) in existing:
                    continue
                try:
                    if kind == "voice":
                        await guild.create_voice_channel(channel_name[:100], category=category, reason="Baklava1923 Bot sunucu sablonu")
                    else:
                        await guild.create_text_channel(channel_name[:100], category=category, reason="Baklava1923 Bot sunucu sablonu")
                    created_channels += 1
                except (discord.Forbidden, discord.HTTPException):
                    failed += 1
        await interaction.followup.send(f"**{template['label']}** sablonu uygulandi\nYeni roller: {created_roles}\nYeni kategoriler: {created_categories}\nYeni kanallar: {created_channels}\nBasarisiz islem: {failed}\nMevcut kanallar ve roller silinmedi. Yetkili roller Administrator yetkisi almaz.", ephemeral=True)


class ServerTemplateView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        entries = list(SERVER_TEMPLATES.items())
        self.add_item(ServerTemplateSelect(entries[:18], "Sablonlar 1 / 2 - ilk 18 sablon", 0))
        self.add_item(ServerTemplateSelect(entries[18:], "Sablonlar 2 / 2 - diger sablonlar", 1))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Bu menu yalnizca komutu baslatan kisi veya yonetici tarafindan kullanilabilir.", ephemeral=True)
            return False
        return True


@bot.command(name="sunucuoluştur", aliases=["sunucuolustur", "sunucu30"])
@commands.guild_only()
async def sunucu_olustur(ctx):
    if not admin_check(ctx) or not ctx.author.guild_permissions.manage_channels:
        await ctx.send("Bu komut için yönetici veya Kanal Yönetme izni gerekiyor.")
        return
    me = ctx.guild.me
    if me is None or not me.guild_permissions.manage_channels:
        await ctx.send("Botta Kanalları Yönet iznini açmalısın.")
        return
    embed = discord.Embed(
        title="Baklava1923 | 35 Sunucu Şablonu",
        description="35 şablondan birini iki açılır menüden seç. Renkli roller ve role uygun izinler eklenir. Yalnızca eksik kategori ve kanallar eklenir; mevcut kanallar/roller silinmez. Güvenlik için hiçbir şablon rolüne Administrator verilmez.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Mevcut şablon sayısı", value=f"{len(SERVER_TEMPLATES)} farklı şablon", inline=False)
    embed.add_field(name="Örnekler", value="Topluluk, Oyun, Countryballs, Minecraft, Roblox, Among Us, YouTuber, Anime, Teknoloji, Yazılım, Mapping, Roleplay, E-Spor ve daha fazlası", inline=False)
    await ctx.send(embed=embed, view=ServerTemplateView(ctx.author.id))


@bot.command(name="sunucu")
@commands.guild_only()
async def sunucu(ctx):
    if not admin_check(ctx):
        await ctx.send("Bu komut icin yonetici izni gerekiyor.")
        return
    embed = discord.Embed(title=f"Baklava1923 Bot {BOT_VERSION} | Sunucu Yonetimi", description="Asagidaki butonlardan yonetim islemi sec. Ayrintili komutlar icin `B!yardim` yaz.", color=discord.Color.blurple())
    embed.add_field(name="Yonetim", value="Sunucu bilgisi, temel kanal kurulumu ve komut listesi", inline=False)
    embed.add_field(name="Guvenlik", value="Spam korumasi, timeout ve guvenlik kayitlari", inline=False)
    await ctx.send(embed=embed, view=ServerMenuView(ctx.guild.id, ctx.author.id))


@bot.command(name="sunucukoruma")
@commands.guild_only()
@commands.has_guild_permissions(administrator=True)
async def sunucukoruma(ctx, islem: str = None, miktar: int = None):
    settings = ensure_guild_settings(ctx.guild.id)
    if islem is None:
        log_text = f"<#{settings['log_channel_id']}>" if settings["log_channel_id"] else "Ayarlanmamis"
        embed = discord.Embed(title="Baklava1923 Guvenlik Merkezi", color=discord.Color.red())
        embed.description = (
            f"Koruma: {'Acik' if settings['protection_enabled'] else 'Kapali'}\n"
            f"Spam esigi: {settings['spam_limit']} mesaj / {settings['spam_window']} saniye\n"
            f"Timeout: {settings['timeout_minutes']} dakika\n"
            f"Guvenlik logu: {log_text}\n\n"
            "Koruma; spam, tekrar eden mesajlar, asiri etiket ve Discord davetlerini denetler. Yeni hesap uyarisi ve 20 saniyede 8 katilimda raid alarmi da vardir. Yeni hesaplar filtreyi tetiklerse yetki/hiyerarsi uygunsa 10 dakika timeout alabilir.\n"
            "`B!sunucukoruma ac` / `B!sunucukoruma kapat`\n"
            "`B!sunucukoruma esik 6` (3-20 mesaj)\n"
            "`B!sunucukoruma sure 5` (1-1440 dakika)\n"
            "`B!sunucukoruma pencere 8` (3-30 saniye)\n"
            "`B!sunuculog #kanal`"
        )
        await ctx.send(embed=embed)
        return
    islem = islem.lower().strip()
    if islem in ("ac", "aç", "on"):
        set_guild_setting(ctx.guild.id, "protection_enabled", 1)
        await ctx.send("Guvenlik korumasi acildi.")
    elif islem in ("kapat", "off"):
        set_guild_setting(ctx.guild.id, "protection_enabled", 0)
        await ctx.send("Guvenlik korumasi kapatildi.")
    elif islem in ("esik", "eşik"):
        if miktar is None or not 3 <= miktar <= 20:
            await ctx.send("Esik 3-20 arasinda olmali. Ornek: `B!sunucukoruma esik 6`")
            return
        set_guild_setting(ctx.guild.id, "spam_limit", miktar)
        await ctx.send(f"Spam esigi {miktar} mesaj olarak ayarlandi.")
    elif islem in ("sure", "süre"):
        if miktar is None or not 1 <= miktar <= 1440:
            await ctx.send("Sure 1-1440 dakika arasinda olmali.")
            return
        set_guild_setting(ctx.guild.id, "timeout_minutes", miktar)
        await ctx.send(f"Tekrar eden spam icin timeout {miktar} dakika olarak ayarlandi.")
    elif islem in ("pencere", "window"):
        if miktar is None or not 3 <= miktar <= 30:
            await ctx.send("Pencere 3-30 saniye arasinda olmali. Ornek: `B!sunucukoruma pencere 8`")
            return
        set_guild_setting(ctx.guild.id, "spam_window", miktar)
        await ctx.send(f"Spam kontrol penceresi {miktar} saniye olarak ayarlandi.")
    else:
        await ctx.send("Kullanim: `B!sunucukoruma [ac/kapat/esik/sure/pencere] [miktar]`")


@bot.command(name="sunucubilgi")
@commands.guild_only()
async def sunucubilgi(ctx):
    g = ctx.guild
    embed = discord.Embed(title=g.name, color=discord.Color.blurple())
    embed.add_field(name="Uye", value=str(g.member_count or 0))
    embed.add_field(name="Kanal", value=str(len(g.channels)))
    embed.add_field(name="Rol", value=str(len(g.roles)))
    embed.add_field(name="Sahip", value=str(g.owner) if g.owner else "Bilinmiyor", inline=False)
    embed.add_field(name="Olusturulma", value=discord.utils.format_dt(g.created_at, style="F"), inline=False)
    await ctx.send(embed=embed)


@bot.command(name="sunucukur")
@commands.guild_only()
@commands.has_guild_permissions(administrator=True, manage_channels=True)
async def sunucukur(ctx):
    await ctx.send(f"Temel kanal kurulumu basladi. Olusturulan kanal: {await create_basic_channels(ctx.guild)}")


@bot.command(name="kanaloluştur", aliases=["kanalolustur"])
@commands.guild_only()
@commands.has_guild_permissions(manage_channels=True)
async def kanal_olustur(ctx, *, isim: str = None):
    if not isim:
        await ctx.send("Kullanim: `B!kanaloluştur kanal-adi`")
        return
    name = re.sub(r"[^a-zA-Z0-9ğüşöçıİĞÜŞÖÇ_-]", "-", isim.lower()).strip("-")[:90]
    if not name:
        await ctx.send("Gecerli bir kanal adi yaz.")
        return
    if discord.utils.get(ctx.guild.text_channels, name=name):
        await ctx.send("Bu isimde bir metin kanali zaten var.")
        return
    try:
        channel = await ctx.guild.create_text_channel(name, reason=f"{ctx.author} tarafindan olusturuldu")
        await ctx.send(f"Kanal olusturuldu: {channel.mention}")
    except discord.Forbidden:
        await ctx.send("Botta Kanallari Yonet izni yok.")
    except discord.HTTPException:
        await ctx.send("Kanal olusturulamadi.")


@bot.command(name="rololuştur", aliases=["rololustur"])
@commands.guild_only()
@commands.has_guild_permissions(manage_roles=True)
async def rol_olustur(ctx, *, isim: str = None):
    if not isim:
        await ctx.send("Kullanim: `B!rololuştur rol-adi`")
        return
    if discord.utils.get(ctx.guild.roles, name=isim):
        await ctx.send("Bu isimde bir rol zaten var.")
        return
    try:
        role = await ctx.guild.create_role(name=isim[:100], reason=f"{ctx.author} tarafindan olusturuldu")
        await ctx.send(f"Rol olusturuldu: **{role.name}**")
    except discord.Forbidden:
        await ctx.send("Botta Rolleri Yonet izni yok veya rol sirasi yetersiz.")
    except discord.HTTPException:
        await ctx.send("Rol olusturulamadi.")


@bot.command(name="oto-rol", aliases=["otorol"])
@commands.guild_only()
@commands.has_guild_permissions(administrator=True, manage_roles=True)
async def oto_rol(ctx, role: discord.Role = None):
    if role is None:
        set_guild_setting(ctx.guild.id, "autorole_id", 0)
        await ctx.send("Otomatik rol kapatildi.")
        return
    if role >= ctx.guild.me.top_role:
        await ctx.send("Botun en ust rolunden yuksek veya esit bir rol verilemez.")
        return
    set_guild_setting(ctx.guild.id, "autorole_id", role.id)
    await ctx.send(f"Yeni uyelere otomatik verilecek rol: {role.mention}")


@bot.command(name="sunuculog")
@commands.guild_only()
@commands.has_guild_permissions(administrator=True)
async def sunucu_log(ctx, kanal: discord.TextChannel = None):
    if kanal is None:
        set_guild_setting(ctx.guild.id, "log_channel_id", 0)
        await ctx.send("Guvenlik log kanali kaldirildi.")
        return
    set_guild_setting(ctx.guild.id, "log_channel_id", kanal.id)
    await ctx.send(f"Guvenlik kayitlari {kanal.mention} kanalina gonderilecek.")


# Channel lock records current @everyone send_messages setting and restores it on unlock.
@bot.command(name="kilit")
@commands.guild_only()
@commands.has_guild_permissions(manage_channels=True)
async def kilit(ctx, kanal: discord.TextChannel = None):
    channel = kanal or ctx.channel
    if not isinstance(channel, discord.TextChannel):
        await ctx.send("Bu komut metin kanalinda kullanilir.")
        return
    cursor.execute("SELECT channel_id FROM locked_channels WHERE channel_id=?", (channel.id,))
    if cursor.fetchone():
        await ctx.send("Bu kanal zaten kilitli gorunuyor.")
        return
    everyone = ctx.guild.default_role
    overwrite = channel.overwrites_for(everyone)
    previous = overwrite.send_messages
    cursor.execute("INSERT INTO locked_channels (channel_id,guild_id,role_id,previous_send_messages) VALUES (?,?,?,?)", (channel.id, ctx.guild.id, everyone.id, None if previous is None else int(previous)))
    db.commit()
    overwrite.send_messages = False
    try:
        await channel.set_permissions(everyone, overwrite=overwrite, reason=f"{ctx.author} kanali kilitledi")
        await ctx.send(f"{channel.mention} kilitlendi. Yonetici izinleri korunur.")
        await send_security_log(ctx.guild, f"Kanal kilitlendi: {channel.mention} — {ctx.author}")
    except (discord.Forbidden, discord.HTTPException):
        cursor.execute("DELETE FROM locked_channels WHERE channel_id=?", (channel.id,))
        db.commit()
        await ctx.send("Kanal kilitlenemedi. Bot izinlerini kontrol et.")


@bot.command(name="kilitac", aliases=["kilit-aç"])
@commands.guild_only()
@commands.has_guild_permissions(manage_channels=True)
async def kilit_ac(ctx, kanal: discord.TextChannel = None):
    channel = kanal or ctx.channel
    cursor.execute("SELECT previous_send_messages FROM locked_channels WHERE channel_id=? AND guild_id=?", (channel.id, ctx.guild.id))
    row = cursor.fetchone()
    if row is None:
        await ctx.send("Bu bot tarafindan kilitlenmis kanal kaydi bulunamadi.")
        return
    overwrite = channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = None if row[0] is None else bool(row[0])
    try:
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"{ctx.author} kilidi acti")
        cursor.execute("DELETE FROM locked_channels WHERE channel_id=?", (channel.id,))
        db.commit()
        await ctx.send(f"{channel.mention} kilidi acildi.")
        await send_security_log(ctx.guild, f"Kanal kilidi acildi: {channel.mention} — {ctx.author}")
    except (discord.Forbidden, discord.HTTPException):
        await ctx.send("Kanal kilidi acilamadi. Bot izinlerini kontrol et.")


@bot.command(name="gelengiden", aliases=["gelengidenkanali", "gelen-giden"])
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def gelen_giden(ctx, kanal: discord.TextChannel = None):
    """Üye giriş/çıkış GIF bildirim kanalını ayarlar."""
    if kanal is None:
        await ctx.send("Kullanım: `!gelengiden #kanal` — örnek: `!gelengiden #gelen-giden`")
        return
    me = ctx.guild.me
    if me is None or not kanal.permissions_for(me).send_messages or not kanal.permissions_for(me).embed_links:
        await ctx.send("Botun bu kanalda mesaj gönderme ve bağlantı yerleştirme izinleri olmalı.")
        return
    cursor.execute(
        "INSERT INTO join_leave_settings (guild_id, channel_id) VALUES (?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id",
        (ctx.guild.id, kanal.id),
    )
    db.commit()
    await ctx.send(f"Gelen-giden GIF bildirimleri artık {kanal.mention} kanalına gönderilecek.")


async def send_join_leave_notice(member, joined):
    cursor.execute("SELECT channel_id FROM join_leave_settings WHERE guild_id=?", (member.guild.id,))
    row = cursor.fetchone()
    if not row:
        return
    channel = member.guild.get_channel(row[0])
    if channel is None:
        return
    if joined:
        embed = discord.Embed(
            title="Sunucuya hoş geldin",
            description=f"{member.mention} aramıza katıldı\nSunucumuzda artık {member.guild.member_count} üyeyiz",
            color=discord.Color.green(),
        )
        embed.set_image(url="https://media.tenor.com/2roX3uxz_68AAAAC/welcome.gif")
    else:
        embed = discord.Embed(
            title="Bir üyemiz ayrıldı",
            description=f"**{member.display_name}** sunucudan ayrıldı",
            color=discord.Color.red(),
        )
        embed.set_image(url="https://media.tenor.com/6a2QqV3v3mAAAAAC/bye-goodbye.gif")
    try:
        await channel.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"Gelen-giden bildirimi gönderilemedi: {exc}")


@bot.event
async def on_member_join(member):
    now = time.time()
    guild_id = member.guild.id
    joins = join_bursts[guild_id]
    joins.append(now)
    account_age = discord.utils.utcnow() - member.created_at
    age_days = max(0, account_age.days)
    if age_days < 3:
        await send_security_log(member.guild, f"Guvenlik uyarisi: yeni acilmis hesap katildi ({age_days} gunluk): {member} ({member.id})")

    raid_cfg = ensure_raid_settings(guild_id)
    while joins and now - joins[0] > raid_cfg["window_seconds"]:
        joins.popleft()

    # Raid dalgasinda toplu ban/kick yerine gecici raid modu ve dogrulama uygulanir.
    if raid_cfg["enabled"] and len(joins) >= raid_cfg["threshold"]:
        active_until = now + raid_cfg["mode_minutes"] * 60
        cursor.execute("UPDATE raid_settings SET active_until=? WHERE guild_id=?", (active_until, guild_id))
        db.commit()
        if now - raid_cfg["last_alert_at"] >= 300:
            cursor.execute("UPDATE raid_settings SET last_alert_at=? WHERE guild_id=?", (now, guild_id))
            db.commit()
            alert = (
                f"ANTI-RAID UYARISI: {member.guild.name} sunucusuna {raid_cfg['window_seconds']} saniyede "
                f"{len(joins)} üye katıldı. Raid modu {raid_cfg['mode_minutes']} dakika etkinleştirildi. "
                "Toplu ban/kick uygulanmadı."
            )
            await send_security_log(member.guild, alert)
            await notify_owner(alert)

    # Dogrulama aciksa yeni uyeye bekleyen rolu ver; dogrulama tamamlaninca buton rolu degistirir.
    cursor.execute("SELECT unverified_role_id FROM verification_settings WHERE guild_id=?", (guild_id,))
    verification_row = cursor.fetchone()
    verification_configured = verification_row is not None
    pending_role = member.guild.get_role(verification_row[0]) if verification_row and verification_row[0] else None
    raid_cfg = ensure_raid_settings(guild_id)
    raid_active = raid_cfg["active_until"] > now
    if pending_role and member.guild.me and pending_role < member.guild.me.top_role:
        try:
            await member.add_roles(pending_role, reason="Baklava1923 doğrulama bekleyen rolü")
        except (discord.Forbidden, discord.HTTPException) as exc:
            await send_security_log(member.guild, f"Bekleyen rol verilemedi: {member.mention} — {exc}")
    elif raid_active:
        await send_security_log(member.guild, f"Raid modu sırasında yeni üye katıldı: {member} ({member.id}); doğrulama rolü ayarlı değil.")

    # Dogrulama sistemi ayarliysa eski otomatik uye rolunu verme; rol atlayarak gecis olmasin.
    settings = ensure_guild_settings(guild_id)
    role_id = settings["autorole_id"]
    if role_id and not verification_configured:
        role = member.guild.get_role(role_id)
        if role and member.guild.me and role < member.guild.me.top_role:
            try:
                await member.add_roles(role, reason="Baklava1923 Bot otomatik rol")
            except (discord.Forbidden, discord.HTTPException):
                await send_security_log(member.guild, f"Otomatik rol verilemedi: {member.mention}")
    await send_security_log(member.guild, f"Sunucuya yeni uye katildi: {member.mention} ({member.id}) | hesap yasi: {age_days} gun")
    await send_join_leave_notice(member, joined=True)


@bot.event
async def on_member_remove(member):
    await send_join_leave_notice(member, joined=False)


def is_version_question(text):
    """Sürüm kelimesini ekleriyle ve cümlenin herhangi bir yerinde yakalar."""
    text = (text or "").lower()
    sade = str.maketrans({
        "ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u",
    })
    text = text.translate(sade)
    kelimeler = re.findall(r"[a-z0-9.]+", text)

    # Örnek: sürüm, sürümün, sürümümüz, sürümündesin,
    # "sürümün kaç gevşek" gibi devamında başka kelimeler olsa da yakalanır.
    kokler = ("surum", "versiyon", "version", "build", "release")
    return any(kelime.startswith(kokler) for kelime in kelimeler)


async def handle_bot_to_bot_message(message):
    """Yalnızca seçilmiş botla, durdurulana kadar ping/yanıt üzerinden konuşur."""
    if message.guild is None or bot.user is None or not message.content.strip():
        return

    cursor.execute(
        "SELECT peer_bot_id, message_limit, messages_sent FROM bot_chat_settings WHERE guild_id=? AND channel_id=? AND enabled=1",
        (message.guild.id, message.channel.id),
    )
    row = cursor.fetchone()
    if not row or int(row[0]) != message.author.id or message.author.id == bot.user.id:
        return

    # Botun yalnızca doğrudan etiketlendiği veya kendi mesajına yanıt aldığı turu işler.
    addressed = bot.user in message.mentions
    if not addressed and message.reference and message.reference.message_id:
        try:
            referenced = await message.channel.fetch_message(message.reference.message_id)
            addressed = referenced.author.id == bot.user.id
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            addressed = False
    if not addressed:
        return

    key = (message.guild.id, message.channel.id)
    message_limit = int(row[1] or 20)
    messages_sent = int(row[2] or 0)
    if messages_sent >= message_limit:
        cursor.execute(
            "DELETE FROM bot_chat_settings WHERE guild_id=? AND channel_id=?",
            key,
        )
        db.commit()
        await message.channel.send(f"Bot sohbeti {message_limit} yanıt sınırına ulaştı ve otomatik durduruldu.")
        return

    clean = re.sub(rf"<@!?{bot.user.id}>", "", message.content).strip()
    clean = discord.utils.escape_mentions(clean)[:1800]
    if not clean:
        clean = "Selam, nasılsın?"

    history = list(bot_chat_histories[key])
    prompt = (
        f"Başka bir Discord botu olan {message.author.display_name} ile sohbet ediyorsun. "
        "Türkçe, kısa ve doğal cevap ver. Bunun iki bot arasında devam eden bir sohbet olduğunu bil. "
        "Konuşmayı doğal biçimde sürdür, karşı botun son mesajına cevap ver ve gerektiğinde ona soru sor. "
        f"Karşı botun mesajı: {clean}"
    )
    try:
        # Botlar arasi sohbetin kanali hizla doldurmamasi icin her yanit arasinda bekle.
        await asyncio.sleep(5)
        async with message.channel.typing():
            clean_lang = bool(message.guild) and not profanity_allowed(message.guild.id)
            answer = await bot.loop.run_in_executor(
                AI_EXECUTOR, functools.partial(get_ai_response, prompt, [], history, clean_language=clean_lang)
            )
        if not answer or answer.startswith("Yapay zeka ayarli degil") or answer.startswith("Yapay zeka API hatasi"):
            await message.channel.send("Bot sohbeti için yapay zekâ bağlantısı kullanılamıyor. GROQ_API_KEY ayarını kontrol et.")
            return
        answer = discord.utils.escape_mentions(answer[:1700])
        bot_chat_histories[key].append({"role": "user", "content": f"{message.author.display_name}: {clean}"[:2200]})
        bot_chat_histories[key].append({"role": "assistant", "content": answer[:2200]})
        cursor.execute(
            "UPDATE bot_chat_settings SET messages_sent=messages_sent+1 WHERE guild_id=? AND channel_id=? AND enabled=1",
            key,
        )
        db.commit()
        cursor.execute(
            "SELECT message_limit, messages_sent FROM bot_chat_settings WHERE guild_id=? AND channel_id=? AND enabled=1",
            key,
        )
        progress = cursor.fetchone()
        await message.channel.send(
            f"{message.author.mention} {answer}",
            allowed_mentions=discord.AllowedMentions(users=[message.author]),
        )
        if progress and progress[1] >= progress[0]:
            cursor.execute("DELETE FROM bot_chat_settings WHERE guild_id=? AND channel_id=?", key)
            db.commit()
            await message.channel.send(f"Sohbet belirlenen {progress[0]} yanıt sınırına ulaştı ve otomatik durduruldu.")
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"BOT SOHBETI MESAJ HATASI: {type(exc).__name__}: {exc}")


@bot.event
async def on_message(message):
    # Webhook ile üretilen [SAHTE] mesajlar XP/AI sistemine dahil edilmez.
    if message.webhook_id is not None:
        return
    if message.author.bot:
        if bot.user is not None and message.author.id != bot.user.id:
            await handle_bot_to_bot_message(message)
        return

    # AFK durumu: kullanıcı geri yazınca kapat, etiketlenince sebebini bildir.
    if message.guild:
        cursor.execute("SELECT reason FROM afk_status WHERE guild_id=? AND user_id=?", (message.guild.id, message.author.id))
        own_afk = cursor.fetchone()
        if own_afk and not message.content.lstrip().lower().startswith(("!", "b!")):
            cursor.execute("DELETE FROM afk_status WHERE guild_id=? AND user_id=?", (message.guild.id, message.author.id))
            db.commit()
            try:
                await message.channel.send(f"Hoş geldin {message.author.mention} AFK durumun kapatıldı", delete_after=6)
            except (discord.Forbidden, discord.HTTPException):
                pass
        for mentioned in message.mentions[:5]:
            if mentioned.id == message.author.id or mentioned.bot:
                continue
            cursor.execute("SELECT reason,since FROM afk_status WHERE guild_id=? AND user_id=?", (message.guild.id, mentioned.id))
            afk_row = cursor.fetchone()
            if afk_row:
                reason, since = afk_row
                elapsed = max(0, int((time.time() - float(since)) // 60))
                try:
                    await message.channel.send(f"{mentioned.display_name} şu anda AFK · {reason} · {elapsed} dakika önce", delete_after=8)
                except (discord.Forbidden, discord.HTTPException):
                    pass
    # Once prefix komutlarini calistir.
    await bot.process_commands(message)
    if message.guild and message.content:
        custom_match = re.match(r"^\s*(?:b!|!)?([a-z0-9_-]{2,24})\s*$", message.content, re.IGNORECASE)
        custom_name = custom_match.group(1).lower() if custom_match else None
        if custom_name:
            cursor.execute("SELECT response FROM custom_commands WHERE guild_id=? AND command_name=?", (message.guild.id, custom_name))
            custom_row = cursor.fetchone()
            if custom_row:
                await message.channel.send(custom_row[0][:1900], allowed_mentions=discord.AllowedMentions.none())

    # Gercek sohbet mesajlarindan XP ve gorev ilerlemesi; komutlar XP kazandirmasin.
    if message.guild and message.content.strip() and not message.content.lstrip().lower().startswith(("!", "b!")):
        await process_member_activity(message)

    if message.guild and message.content.strip():
        member = message.author
        settings = ensure_guild_settings(message.guild.id)
        # Sunucu yöneticilerinin tanımladığı kelime filtresi.
        trusted = getattr(member, "guild_permissions", discord.Permissions.none())
        if not (trusted.administrator or trusted.manage_messages):
            cursor.execute("SELECT word FROM filtered_words WHERE guild_id=?", (message.guild.id,))
            blocked_words = [row[0] for row in cursor.fetchall()]
            lowered_content = message.content.casefold()
            matched_word = next((word for word in blocked_words if word.casefold() in lowered_content), None)
            if matched_word:
                try:
                    await message.delete()
                    await message.channel.send(f"{member.mention} mesajın sunucunun kelime filtresine takıldı.", delete_after=7)
                    await send_security_log(message.guild, f"Kelime filtresi: {member} ({member.id}) · eşleşme={matched_word[:60]}")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return
        # Skip moderators and users whose moderation permissions indicate trusted staff.
        trusted = getattr(member, "guild_permissions", discord.Permissions.none())
        if settings["protection_enabled"] and not (trusted.administrator or trusted.manage_messages or trusted.moderate_members):
            content = message.content
            invite_pattern = re.compile(r"(?:https?://)?(?:www\.)?(?:discord\.gg|discord(?:app)?\.com/invite)/[\w-]+", re.IGNORECASE)
            invite_found = bool(invite_pattern.search(content))
            too_many_mentions = message.mention_everyone or len(message.mentions) >= 6 or len(message.role_mentions) >= 4
            # Delete obvious invite advertising and mass-mention messages if possible.
            if invite_found or too_many_mentions:
                try:
                    await message.delete()
                    reason = "Discord davet baglantisi" if invite_found else "Asiri etiket"
                    await message.channel.send(f"{member.mention} mesaj guvenlik filtresine takildi ({reason}).", delete_after=8)
                    await send_security_log(message.guild, f"Guvenlik filtresi: {reason}; uye={member} ({member.id}); kanal=#{message.channel}")
                    # Hesabi 3 gunden yeni olan ve reklam/mention filtresine takilan uye icin kisa timeout.
                    if (discord.utils.utcnow() - member.created_at).days < 3:
                        me = message.guild.me
                        if me and me.guild_permissions.moderate_members and member.top_role < me.top_role:
                            await member.timeout(discord.utils.utcnow() + timedelta(minutes=10), reason="Yeni hesap guvenlik filtresi: davet veya asiri etiket")
                            await send_security_log(message.guild, f"Yeni hesap icin 10 dakika timeout uygulandi: {member} ({member.id})")
                except (discord.Forbidden, discord.HTTPException) as exc:
                    await send_security_log(message.guild, f"Guvenlik filtresi islemi tamamlanamadi: {member} ({member.id}) — {exc}")
                return

            key = (message.guild.id, member.id)
            now = time.time()
            queue = spam_messages[key]
            queue.append(now)
            while queue and now - queue[0] > settings["spam_window"]:
                queue.popleft()
            if len(queue) >= settings["spam_limit"]:
                if key not in spam_warned:
                    spam_warned.add(key)
                    try:
                        await message.channel.send(f"{member.mention} yavasla biraz, spam filtresi seni izliyor.", delete_after=8)
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                    await send_security_log(message.guild, f"Spam uyarisi: {member} ({member.id})")
                else:
                    me = message.guild.me
                    if me and me.guild_permissions.moderate_members and member.top_role < me.top_role:
                        try:
                            await member.timeout(discord.utils.utcnow() + timedelta(minutes=settings["timeout_minutes"]), reason="Tekrarlanan spam")
                            await send_security_log(message.guild, f"Spam timeout: {member} — {settings['timeout_minutes']} dakika")
                            await notify_owner(f"Baklava1923 Bot {BOT_VERSION} spam islemi\nSunucu: {message.guild.name}\nUye: {member} ({member.id})\nSure: {settings['timeout_minutes']} dakika")
                        except (discord.Forbidden, discord.HTTPException) as exc:
                            await send_security_log(message.guild, f"Timeout uygulanamadi: {member} — {exc}")
                    else:
                        await send_security_log(message.guild, f"Spam tespit edildi ancak timeout uygulanamadi: {member}")
                    spam_warned.discard(key)
                    queue.clear()

    # Komut mesajlarini AI sohbetine yollama.
    lowered = message.content.lstrip().lower()
    if lowered.startswith(("!", "b!")):
        return
    if bot.user is None:
        return
    # Baklavai acik kanalda etiketsiz her mesaja cevap verir.
    if message.guild and is_baklavai_channel(message.guild.id, message.channel.id):
        await handle_baklavai_message(message)
        return
    mentioned = bot.user in message.mentions
    replied = False
    if message.reference and message.reference.message_id:
        try:
            ref = await message.channel.fetch_message(message.reference.message_id)
            replied = ref.author.id == bot.user.id
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
    if not (mentioned or replied):
        return

    images, errors = await read_image_attachments(message)
    clean = re.sub(rf"<@!?{bot.user.id}>", "", message.content).strip()
    if not clean and images:
        clean = "Bu gorseli incele. Icindekileri Turkce ve anlasilir sekilde acikla."
    elif not clean:
        clean = "Bana kisa bir selam ver."
    if is_version_question(clean):
        await message.reply(
            f"Ben Baklava1923-Bot {BOT_VERSION} sürümüyüm.",
            mention_author=False
        )
        return

    try:
        async with message.channel.typing():
            history_key = (message.guild.id if message.guild else 0, message.channel.id, message.author.id)
            history = list(ai_histories[history_key])
            clean_lang = bool(message.guild) and not profanity_allowed(message.guild.id)
            answer = await bot.loop.run_in_executor(
                AI_EXECUTOR,
                functools.partial(get_ai_response, clean, images, history, clean_language=clean_lang),
            )
            ai_histories[history_key].append({"role": "user", "content": clean[:3000]})
            ai_histories[history_key].append({"role": "assistant", "content": (answer or "")[:3000]})
        if errors:
            answer += "\n\nNot: " + "; ".join(errors)
        await message.reply((answer or "Su an cevap olusturamadim.")[:1900], mention_author=False)
    except (discord.HTTPException, discord.Forbidden) as exc:
        print(f"Discord mesaj gonderme hatasi: {exc}")


MAX_IMAGE_BYTES = 7 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}


async def read_image_attachments(message):
    urls, errors = [], []
    for attachment in message.attachments:
        mime = (attachment.content_type or "").split(";")[0].lower()
        if mime not in ALLOWED_IMAGE_TYPES:
            mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(os.path.splitext(attachment.filename.lower())[1], "")
        if not mime:
            continue
        if attachment.size > MAX_IMAGE_BYTES:
            errors.append(f"{attachment.filename}: 7 MB sinirini asiyor")
            continue
        try:
            encoded = base64.b64encode(await attachment.read()).decode("ascii")
            urls.append(f"data:{mime};base64,{encoded}")
        except (discord.HTTPException, OSError):
            errors.append(f"{attachment.filename}: okunamadi")
        if len(urls) >= 3:
            break
    return urls, errors


# -------------------- SLASH DURUM KOMUTU --------------------
@bot.tree.command(name="durum", description="Baklava1923 Botun durumunu gosterir")
async def durum(interaction: discord.Interaction):
    if not bot.is_ready():
        await interaction.response.send_message("Bot hazir degil.", ephemeral=True)
        return
    guild_count = len(bot.guilds)
    member_count = sum(g.member_count or 0 for g in bot.guilds)
    ping = round(bot.latency * 1000)
    embed = discord.Embed(title=f"Baklava1923 Bot {BOT_VERSION}", color=discord.Color.green())
    embed.add_field(name="Sunucu sayisi", value=str(guild_count))
    embed.add_field(name="Toplam sunucu uye sayisi", value=str(member_count))
    embed.add_field(name="Ping", value=f"{ping} ms")
    embed.add_field(name="Komut onekleri", value="!  B!  b!")
    if interaction.user.id == OWNER_ID:
        names = [f"• {g.name} ({g.id})" for g in bot.guilds]
        embed.add_field(name="Botun bulundugu sunucular", value=("\n".join(names)[:1024] or "Sunucu yok"), inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# -------------------- BAKLAVA1923 BOT 3.6 BUYUK GUNCELLEME --------------------
ACHIEVEMENT_DEFS = {
    "first_message": ("Ilk Mesaj", "Sunucuda ilk sohbet mesajini gonderdin.", 50),
    "level_5": ("Seviye 5", "5. seviyeye ulastin.", 100),
    "level_10": ("Seviye 10", "10. seviyeye ulastin.", 250),
    "level_25": ("Seviye 25", "25. seviyeye ulastin.", 750),
    "daily_quest": ("Gunluk Gorev", "Gunluk sohbet gorevini tamamladin.", 100),
    "weekly_quest": ("Haftalik Gorev", "Haftalik sohbet gorevini tamamladin.", 500),
    "first_ticket": ("Destek Acildi", "Ilk destek biletini actin.", 50),
}

scheduled_reminders = set()
scheduled_giveaways = set()
scheduled_polls = set()
scheduled_tournaments = set()


def unlock_achievement(guild_id, user_id, achievement_key):
    if achievement_key not in ACHIEVEMENT_DEFS:
        return False
    cursor.execute(
        "INSERT OR IGNORE INTO achievements (guild_id,user_id,achievement_key,unlocked_at) VALUES (?,?,?,?)",
        (guild_id, user_id, achievement_key, time.time()),
    )
    got_new = cursor.rowcount > 0
    if got_new:
        reward = ACHIEVEMENT_DEFS[achievement_key][2]
        add_balance(0, user_id, reward)
    db.commit()
    return got_new


def get_level_requirement(level):
    return 100 + max(0, int(level)) * 75


async def process_member_activity(message):
    if not message.guild or message.author.bot:
        return
    guild_id, user_id = message.guild.id, message.author.id
    now = time.time()
    today = datetime.now(timezone.utc).date().isoformat()
    iso = datetime.now(timezone.utc).isocalendar()
    week_key = f"{iso.year}-W{iso.week:02d}"

    cursor.execute("SELECT xp, level, messages FROM user_profiles WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    row = cursor.fetchone()
    if row is None:
        xp, level, messages = 0, 0, 0
    else:
        xp, level, messages = map(int, row)
    messages += 1

    cursor.execute("SELECT daily_key,daily_messages,daily_claimed,weekly_key,weekly_messages,weekly_claimed FROM quest_progress WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    quest = cursor.fetchone()
    if quest is None:
        daily_messages, daily_claimed = 1, 0
        weekly_messages, weekly_claimed = 1, 0
        cursor.execute(
            "INSERT INTO quest_progress (guild_id,user_id,daily_key,daily_messages,daily_claimed,weekly_key,weekly_messages,weekly_claimed) VALUES (?,?,?,?,?,?,?,?)",
            (guild_id, user_id, today, daily_messages, daily_claimed, week_key, weekly_messages, weekly_claimed),
        )
    else:
        old_day, d_count, d_claimed, old_week, w_count, w_claimed = quest
        daily_messages = int(d_count) + 1 if old_day == today else 1
        daily_claimed = int(d_claimed) if old_day == today else 0
        weekly_messages = int(w_count) + 1 if old_week == week_key else 1
        weekly_claimed = int(w_claimed) if old_week == week_key else 0
        cursor.execute(
            "UPDATE quest_progress SET daily_key=?,daily_messages=?,daily_claimed=?,weekly_key=?,weekly_messages=?,weekly_claimed=? WHERE guild_id=? AND user_id=?",
            (today, daily_messages, daily_claimed, week_key, weekly_messages, weekly_claimed, guild_id, user_id),
        )

    levelled_to = None
    previous_messages = messages - 1
    if previous_messages == 0:
        unlock_achievement(guild_id, user_id, "first_message")
    key = (guild_id, user_id)
    if now - xp_last_awarded.get(key, 0) >= 45:
        xp_last_awarded[key] = now
        xp += random.randint(10, 20)
        while xp >= get_level_requirement(level):
            xp -= get_level_requirement(level)
            level += 1
            levelled_to = level
        if levelled_to == 5:
            unlock_achievement(guild_id, user_id, "level_5")
        elif levelled_to == 10:
            unlock_achievement(guild_id, user_id, "level_10")
        elif levelled_to == 25:
            unlock_achievement(guild_id, user_id, "level_25")

    cursor.execute(
        "INSERT INTO user_profiles (guild_id,user_id,xp,level,messages) VALUES (?,?,?,?,?) "
        "ON CONFLICT(guild_id,user_id) DO UPDATE SET xp=excluded.xp,level=excluded.level,messages=excluded.messages",
        (guild_id, user_id, xp, level, messages),
    )
    db.commit()

    if levelled_to is not None:
        cursor.execute("SELECT role_id FROM level_rewards WHERE guild_id=? AND level=?", (guild_id, levelled_to))
        role_row = cursor.fetchone()
        if role_row:
            role = message.guild.get_role(role_row[0])
            if role and message.guild.me and role < message.guild.me.top_role:
                try:
                    await message.author.add_roles(role, reason=f"Baklava1923 Bot seviye odulu: {levelled_to}")
                except (discord.Forbidden, discord.HTTPException):
                    await send_security_log(message.guild, f"Seviye rolu verilemedi: {message.author.mention} — {role.name}")
        try:
            await message.channel.send(f"Tebrikler {message.author.mention}, **{levelled_to}. seviyeye** ulastin. `B!profil` ile profilini gor.")
        except (discord.Forbidden, discord.HTTPException):
            pass


@bot.command(name="profil")
@commands.guild_only()
async def profil(ctx, oyuncu: discord.Member = None):
    target = oyuncu or ctx.author
    cursor.execute("SELECT xp,level,messages FROM user_profiles WHERE guild_id=? AND user_id=?", (ctx.guild.id, target.id))
    row = cursor.fetchone() or (0, 0, 0)
    xp, level, messages = map(int, row)
    next_req = get_level_requirement(level)
    cursor.execute("SELECT COUNT(*) FROM achievements WHERE guild_id=? AND user_id=?", (ctx.guild.id, target.id))
    achievements_count = cursor.fetchone()[0]
    embed = discord.Embed(title=f"{target.display_name} | Profil", color=discord.Color.blurple())
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Seviye", value=str(level), inline=True)
    embed.add_field(name="XP", value=f"{xp}/{next_req}", inline=True)
    embed.add_field(name="Sohbet mesajı", value=str(messages), inline=True)
    embed.add_field(name="Paklava", value=balance_label(target.id, get_balance(0, target.id)), inline=True)
    embed.add_field(name="Başarım", value=str(achievements_count), inline=True)
    embed.add_field(name="Son 7 gün aktiflik", value=format_time(get_activity(ctx.guild.id, target.id, 7)), inline=False)
    await ctx.send(embed=embed)


@bot.command(name="seviye")
@commands.guild_only()
async def seviye(ctx, oyuncu: discord.Member = None):
    target = oyuncu or ctx.author
    cursor.execute("SELECT xp,level FROM user_profiles WHERE guild_id=? AND user_id=?", (ctx.guild.id, target.id))
    row = cursor.fetchone() or (0, 0)
    xp, level = map(int, row)
    needed = get_level_requirement(level)
    ratio = min(10, int((xp / max(1, needed)) * 10))
    bar = "█" * ratio + "░" * (10 - ratio)
    await ctx.send(f"{target.mention} | Seviye **{level}**\n`{bar}` **{xp}/{needed} XP**")


@bot.command(name="liderlik", aliases=["seviye-liderlik", "xptop"])
@commands.guild_only()
async def liderlik(ctx):
    cursor.execute("SELECT user_id,level,xp,messages FROM user_profiles WHERE guild_id=? ORDER BY level DESC,xp DESC,messages DESC LIMIT 10", (ctx.guild.id,))
    rows = cursor.fetchall()
    lines = []
    for rank, (user_id, level, xp, messages) in enumerate(rows, 1):
        member = ctx.guild.get_member(user_id)
        name = member.display_name if member else f"Kullanıcı {user_id}"
        lines.append(f"**{rank}.** {name} — Seviye {level} ({xp} XP)")
    embed = discord.Embed(title="Seviye Sıralaması", description="\n".join(lines) or "Henüz sıralama verisi yok.", color=discord.Color.gold())
    await ctx.send(embed=embed)


@bot.command(name="basarim", aliases=["başarım", "basarimlar"])
@commands.guild_only()
async def basarim(ctx, oyuncu: discord.Member = None):
    target = oyuncu or ctx.author
    cursor.execute("SELECT achievement_key FROM achievements WHERE guild_id=? AND user_id=?", (ctx.guild.id, target.id))
    unlocked = {row[0] for row in cursor.fetchall()}
    lines = []
    for key, (name, description, reward) in ACHIEVEMENT_DEFS.items():
        state = "Açıldı" if key in unlocked else "Kilitli"
        lines.append(f"**{name}** — {state}\n{description} (+{reward} paklava)")
    embed = discord.Embed(title=f"{target.display_name} | Başarımlar", description="\n\n".join(lines)[:4000], color=discord.Color.gold())
    await ctx.send(embed=embed)


@bot.command(name="gorev", aliases=["görev", "gorevler"])
@commands.guild_only()
async def gorev(ctx):
    guild_id, user_id = ctx.guild.id, ctx.author.id
    today = datetime.now(timezone.utc).date().isoformat()
    iso = datetime.now(timezone.utc).isocalendar()
    week_key = f"{iso.year}-W{iso.week:02d}"
    cursor.execute("SELECT daily_key,daily_messages,daily_claimed,weekly_key,weekly_messages,weekly_claimed FROM quest_progress WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    row = cursor.fetchone()
    if row is None:
        daily_key, daily_messages, daily_claimed, stored_week, weekly_messages, weekly_claimed = today, 0, 0, week_key, 0, 0
        cursor.execute("INSERT INTO quest_progress (guild_id,user_id,daily_key,daily_messages,daily_claimed,weekly_key,weekly_messages,weekly_claimed) VALUES (?,?,?,?,?,?,?,?)", (guild_id, user_id, daily_key, daily_messages, daily_claimed, stored_week, weekly_messages, weekly_claimed))
    else:
        daily_key, daily_messages, daily_claimed, stored_week, weekly_messages, weekly_claimed = row
        if daily_key != today:
            daily_key, daily_messages, daily_claimed = today, 0, 0
        if stored_week != week_key:
            stored_week, weekly_messages, weekly_claimed = week_key, 0, 0
        cursor.execute("UPDATE quest_progress SET daily_key=?,daily_messages=?,daily_claimed=?,weekly_key=?,weekly_messages=?,weekly_claimed=? WHERE guild_id=? AND user_id=?", (daily_key, daily_messages, daily_claimed, stored_week, weekly_messages, weekly_claimed, guild_id, user_id))
    rewards = []
    if int(daily_messages) >= 15 and not int(daily_claimed):
        daily_claimed = 1
        add_balance(0, user_id, 300)
        unlock_achievement(guild_id, user_id, "daily_quest")
        rewards.append("+300 paklava günlük görev ödülü")
    if int(weekly_messages) >= 75 and not int(weekly_claimed):
        weekly_claimed = 1
        add_balance(0, user_id, 1200)
        unlock_achievement(guild_id, user_id, "weekly_quest")
        rewards.append("+1200 paklava haftalık görev ödülü")
    cursor.execute("UPDATE quest_progress SET daily_claimed=?,weekly_claimed=? WHERE guild_id=? AND user_id=?", (daily_claimed, weekly_claimed, guild_id, user_id))
    db.commit()
    desc = f"Günlük sohbet: **{daily_messages}/15 mesaj**\nHaftalık sohbet: **{weekly_messages}/75 mesaj**"
    if rewards:
        desc += "\n\nÖdüller: " + "; ".join(rewards)
    else:
        desc += "\n\nÖdüller görevi tamamladıktan sonra burada alınır."
    await ctx.send(embed=discord.Embed(title="Görev Merkezi", description=desc, color=discord.Color.green()))


@bot.command(name="seviyerol", aliases=["levelrole"])
@commands.guild_only()
@commands.has_guild_permissions(administrator=True, manage_roles=True)
async def seviyerol(ctx, level: int = None, role: discord.Role = None):
    if level is None or role is None or not 1 <= level <= 100:
        await ctx.send("Kullanım: `B!seviyerol <1-100> @rol` — o seviyeye ulaşana rol verilir.")
        return
    if not ctx.guild.me or role >= ctx.guild.me.top_role:
        await ctx.send("Botun en üst rolünün altında bir rol seçmelisin.")
        return
    cursor.execute("INSERT INTO level_rewards (guild_id,level,role_id) VALUES (?,?,?) ON CONFLICT(guild_id,level) DO UPDATE SET role_id=excluded.role_id", (ctx.guild.id, level, role.id))
    db.commit()
    await ctx.send(f"Seviye {level} ödülü ayarlandı: {role.mention}")


@bot.command(name="ekonomisiralamasi", aliases=["zenginler", "paklavatop"])
async def ekonomisiralamasi(ctx):
    cursor.execute("""SELECT e.user_id, e.balance + COALESCE(b.balance, 0) AS total
                      FROM economy e LEFT JOIN bank_accounts b ON b.user_id=e.user_id
                      WHERE e.guild_id=0 ORDER BY total DESC LIMIT 10""")
    rows = cursor.fetchall()
    lines = []
    for rank, (user_id, total) in enumerate(rows, 1):
        user = bot.get_user(user_id)
        name = user.display_name if user else f"Kullanıcı {user_id}"
        lines.append(f"**{rank}.** {name} — {balance_label(user_id, total)} paklava")
    await ctx.send(embed=discord.Embed(title="Paklava Zenginler Sıralaması", description="\n".join(lines) or "Henüz veri yok.", color=discord.Color.gold()))


@bot.command(name="ticaret")
@commands.guild_only()
@commands.cooldown(1, 15, commands.BucketType.user)
async def ticaret(ctx, oyuncu: discord.Member = None, *, esya: str = None):
    if oyuncu is None or esya is None:
        await ctx.send("Kullanım: `B!ticaret @oyuncu <eşya_anahtarı>` — anahtarları `B!market` içinde görürsün.")
        return
    if oyuncu.bot or oyuncu.id == ctx.author.id:
        await ctx.send("Kendinle veya botla eşya takası yapamazsın.")
        return
    key = esya.strip().lower().replace(" ", "_")
    if key not in MARKET_ITEMS:
        await ctx.send("Bu eşya markette bulunamadı. `B!market` komutuna bak.")
        return
    sender_items = get_inventory(0, ctx.author.id)
    receiver_items = get_inventory(0, oyuncu.id)
    if key not in sender_items:
        await ctx.send("Bu eşyaya sahip değilsin.")
        return
    if key in receiver_items:
        await ctx.send("Hedef oyuncu bu eşyaya zaten sahip. Takas yapılamadı.")
        return
    cursor.execute("DELETE FROM inventory WHERE guild_id=0 AND user_id=? AND item_key=?", (ctx.author.id, key))
    cursor.execute("INSERT OR IGNORE INTO inventory (guild_id,user_id,item_key) VALUES (0,?,?)", (oyuncu.id, key))
    db.commit()
    await ctx.send(f"Takas tamamlandı: {ctx.author.mention} → {oyuncu.mention}: **{MARKET_ITEMS[key]['ad']}**")


@bot.command(name="bilet")
@commands.guild_only()
@commands.cooldown(1, 30, commands.BucketType.user)
async def bilet(ctx, *, konu: str = "Genel destek"):
    guild = ctx.guild
    cursor.execute("SELECT channel_id FROM tickets WHERE guild_id=? AND creator_id=? AND closed=0", (guild.id, ctx.author.id))
    existing = cursor.fetchone()
    if existing:
        channel = guild.get_channel(existing[0])
        if channel:
            await ctx.send(f"Zaten açık bir biletin var: {channel.mention}")
            return
    category = next((c for c in guild.categories if "destek" in c.name.lower() or "ticket" in c.name.lower()), None)
    try:
        if category is None:
            category = await guild.create_category("Destek", reason="Baklava1923 Bot bilet sistemi")
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            ctx.author: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
        }
        if guild.me:
            overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
        staff_roles = [r for r in guild.roles if r.permissions.administrator or r.permissions.manage_channels or r.permissions.manage_messages]
        for role in staff_roles[:20]:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        safe_name = re.sub(r"[^a-z0-9-]", "-", ctx.author.name.lower()).strip("-")[:35] or "uye"
        channel = await guild.create_text_channel(f"ticket-{safe_name}", category=category, overwrites=overwrites, topic=f"Sahip ID: {ctx.author.id} | Konu: {konu[:150]}", reason=f"Destek bileti: {ctx.author}")
        cursor.execute("INSERT INTO tickets (channel_id,guild_id,creator_id,opened_at,closed) VALUES (?,?,?,?,0)", (channel.id, guild.id, ctx.author.id, time.time()))
        db.commit()
        unlock_achievement(guild.id, ctx.author.id, "first_ticket")
        await channel.send(f"{ctx.author.mention} destek talebin açıldı.\n**Konu:** {discord.utils.escape_mentions(konu)[:1000]}\nKapatmak için `B!biletkapat` yaz.")
        await ctx.send(f"Destek biletin oluşturuldu: {channel.mention}")
        await send_security_log(guild, f"Destek bileti açıldı: {channel.mention} — {ctx.author} ({ctx.author.id})")
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send("Bilet açılamadı. Botun kategori/kanal yönetme izinlerini kontrol et.")
        print(f"BILET OLUSTURMA HATASI: {type(exc).__name__}: {exc}")


@bot.command(name="biletkapat", aliases=["bilet-kapat"])
@commands.guild_only()
async def biletkapat(ctx):
    cursor.execute("SELECT creator_id,closed FROM tickets WHERE channel_id=? AND guild_id=?", (ctx.channel.id, ctx.guild.id))
    row = cursor.fetchone()
    if not row:
        await ctx.send("Bu kanal kayıtlı bir destek bileti değil.")
        return
    creator_id, closed = row
    if closed:
        await ctx.send("Bu bilet zaten kapatılmış.")
        return
    if ctx.author.id != creator_id and not (ctx.author.guild_permissions.manage_channels or ctx.author.guild_permissions.administrator):
        await ctx.send("Bu bileti yalnızca açan kişi veya yetkili kapatabilir.")
        return
    creator = ctx.guild.get_member(creator_id)
    try:
        if creator:
            overwrite = ctx.channel.overwrites_for(creator)
            overwrite.send_messages = False
            await ctx.channel.set_permissions(creator, overwrite=overwrite, reason="Destek bileti kapatıldı")
        if not ctx.channel.name.startswith("kapali-"):
            await ctx.channel.edit(name=("kapali-" + ctx.channel.name)[:95], reason="Destek bileti kapatıldı")
        cursor.execute("UPDATE tickets SET closed=1 WHERE channel_id=?", (ctx.channel.id,))
        db.commit()
        await ctx.send("Bilet kapatıldı. Kanal kayıt amacıyla korunuyor.")
        await send_security_log(ctx.guild, f"Destek bileti kapatıldı: #{ctx.channel.name} — {ctx.author}")
    except (discord.Forbidden, discord.HTTPException):
        await ctx.send("Bilet kapatılamadı. Botun kanal izinlerini kontrol et.")


@bot.command(name="önerikanal", aliases=["onerikanal", "öneri-kanal"])
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def onerikanal(ctx, kanal: discord.TextChannel = None):
    if kanal is None:
        cursor.execute("DELETE FROM suggestion_channels WHERE guild_id=?", (ctx.guild.id,))
        db.commit()
        await ctx.send("Öneri kanalı ayarı kaldırıldı.")
        return
    cursor.execute("INSERT INTO suggestion_channels (guild_id,channel_id) VALUES (?,?) ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id", (ctx.guild.id, kanal.id))
    db.commit()
    await ctx.send(f"Öneriler artık {kanal.mention} kanalına da gönderilecek.")


@bot.command(name="biletistatistik", aliases=["biletler"])
@commands.guild_only()
@commands.has_guild_permissions(manage_channels=True)
async def biletistatistik(ctx):
    cursor.execute("SELECT COUNT(*),SUM(CASE WHEN closed=0 THEN 1 ELSE 0 END) FROM tickets WHERE guild_id=?", (ctx.guild.id,))
    total, opened = cursor.fetchone()
    await ctx.send(f"Bilet istatistiği: **{total or 0} toplam**, **{opened or 0} açık**, **{(total or 0)-(opened or 0)} kapalı**")


@bot.command(name="ayarlar")
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def ayarlar(ctx):
    st = ensure_guild_settings(ctx.guild.id)
    cursor.execute("SELECT channel_id FROM join_leave_settings WHERE guild_id=?", (ctx.guild.id,))
    jl = cursor.fetchone()
    cursor.execute("SELECT channel_id FROM suggestion_channels WHERE guild_id=?", (ctx.guild.id,))
    sug = cursor.fetchone()
    cursor.execute("SELECT COUNT(*) FROM level_rewards WHERE guild_id=?", (ctx.guild.id,))
    rewards = cursor.fetchone()[0]
    embed = discord.Embed(title=f"{ctx.guild.name} | Bot Ayar Merkezi", color=discord.Color.blurple())
    embed.add_field(name="Güvenlik", value=f"Koruma: {'Açık' if st['protection_enabled'] else 'Kapalı'}\nSpam eşiği: {st['spam_limit']} / {st['spam_window']} sn\nTimeout: {st['timeout_minutes']} dk", inline=False)
    log_channel_text = f"<#{st['log_channel_id']}>" if st["log_channel_id"] else "Yok"
    join_leave_text = f"<#{jl[0]}>" if jl else "Yok"
    suggestion_text = f"<#{sug[0]}>" if sug else "Yok"
    autorole_text = f"<@&{st['autorole_id']}>" if st["autorole_id"] else "Kapalı"
    embed.add_field(name="Kanallar", value=f"Log: {log_channel_text}\nGiriş-çıkış: {join_leave_text}\nÖneriler: {suggestion_text}", inline=False)
    embed.add_field(name="Otomasyon", value=f"Oto-rol: {autorole_text}\nSeviye rol ödülü: {rewards} adet", inline=False)
    embed.set_footer(text="Ayarları değiştirmek için B!sunucukoruma, B!oto-rol, B!gelengiden ve B!önerikanal komutlarını kullan.")
    await ctx.send(embed=embed)


@bot.command(name="yedekle")
@commands.guild_only()
@commands.has_guild_permissions(administrator=True)
async def yedekle(ctx):
    st = ensure_guild_settings(ctx.guild.id)
    cursor.execute("SELECT channel_id FROM join_leave_settings WHERE guild_id=?", (ctx.guild.id,))
    jl = cursor.fetchone()
    cursor.execute("SELECT channel_id FROM suggestion_channels WHERE guild_id=?", (ctx.guild.id,))
    sug = cursor.fetchone()
    payload = {
        "settings": {
            "log_channel_id": st["log_channel_id"] or 0,
            "autorole_id": st["autorole_id"] or 0,
            "spam_limit": st["spam_limit"],
            "spam_window": st["spam_window"],
            "timeout_minutes": st["timeout_minutes"],
            "protection_enabled": int(st["protection_enabled"]),
        },
        "join_leave_channel_id": jl[0] if jl else 0,
        "suggestion_channel_id": sug[0] if sug else 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Bot ayarları; kanal/rol yapısı veya mesajlar dahil değildir.",
    }
    cursor.execute("INSERT INTO config_backups (guild_id,created_by,created_at,payload) VALUES (?,?,?,?)", (ctx.guild.id, ctx.author.id, time.time(), json.dumps(payload, ensure_ascii=False)))
    db.commit()
    backup_id = cursor.lastrowid
    await ctx.send(f"Bot ayarlarının yedeği alındı. Yedek numarası: **{backup_id}**. Geri yükleme: `B!gerial {backup_id}`. Bu yedek sunucu kanallarını/rollerini veya mesajları içermez.")


@bot.command(name="yedekler")
@commands.guild_only()
@commands.has_guild_permissions(administrator=True)
async def yedekler(ctx):
    cursor.execute("SELECT id,created_at,created_by FROM config_backups WHERE guild_id=? ORDER BY id DESC LIMIT 10", (ctx.guild.id,))
    rows = cursor.fetchall()
    if not rows:
        await ctx.send("Henüz ayar yedeği yok. `B!yedekle` yaz.")
        return
    lines = [f"**#{bid}** — {discord.utils.format_dt(datetime.fromtimestamp(ts, timezone.utc), style='f')} — oluşturan ID `{uid}`" for bid, ts, uid in rows]
    await ctx.send(embed=discord.Embed(title="Son Ayar Yedekleri", description="\n".join(lines), color=discord.Color.blurple()))


@bot.command(name="gerial", aliases=["yedekgerial"])
@commands.guild_only()
@commands.has_guild_permissions(administrator=True)
async def gerial(ctx, backup_id: int = None):
    if backup_id is None:
        await ctx.send("Kullanım: `B!gerial <yedek_numarası>` — yalnızca bot ayarlarını geri yükler.")
        return
    cursor.execute("SELECT payload FROM config_backups WHERE id=? AND guild_id=?", (backup_id, ctx.guild.id))
    row = cursor.fetchone()
    if not row:
        await ctx.send("Bu sunucuda böyle bir yedek bulunamadı.")
        return
    try:
        payload = json.loads(row[0])
        settings = payload["settings"]
        for key, value in settings.items():
            set_guild_setting(ctx.guild.id, key, value)
        jl_id = int(payload.get("join_leave_channel_id", 0) or 0)
        if jl_id and ctx.guild.get_channel(jl_id):
            cursor.execute("INSERT INTO join_leave_settings (guild_id,channel_id) VALUES (?,?) ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id", (ctx.guild.id, jl_id))
        else:
            cursor.execute("DELETE FROM join_leave_settings WHERE guild_id=?", (ctx.guild.id,))
        sug_id = int(payload.get("suggestion_channel_id", 0) or 0)
        if sug_id and ctx.guild.get_channel(sug_id):
            cursor.execute("INSERT INTO suggestion_channels (guild_id,channel_id) VALUES (?,?) ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id", (ctx.guild.id, sug_id))
        else:
            cursor.execute("DELETE FROM suggestion_channels WHERE guild_id=?", (ctx.guild.id,))
        db.commit()
        await ctx.send(f"**#{backup_id}** numaralı yedekteki bot ayarları geri yüklendi. Kanallar ve roller silinmedi veya yeniden oluşturulmadı.")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        await ctx.send("Yedek dosyası okunamadı; ayarlar değiştirilmedi.")
        print(f"YEDEK GERIAL HATASI: {type(exc).__name__}: {exc}")


@bot.command(name="duyuru")
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def duyuru(ctx, kanal: discord.TextChannel = None, *, metin: str = None):
    if kanal is None or not metin:
        await ctx.send("Kullanım: `B!duyuru #kanal <mesaj>`")
        return
    try:
        embed = discord.Embed(title="Sunucu Duyurusu", description=discord.utils.escape_mentions(metin)[:4000], color=discord.Color.gold())
        embed.set_footer(text=f"Duyuran: {ctx.author.display_name}")
        await kanal.send(embed=embed)
        await ctx.send(f"Duyuru {kanal.mention} kanalına gönderildi.")
    except (discord.Forbidden, discord.HTTPException):
        await ctx.send("Duyuru gönderilemedi. Botun ilgili kanalda mesaj ve embed izni olmalı.")


@bot.command(name="uyar")
@commands.guild_only()
@commands.has_guild_permissions(moderate_members=True)
async def uyar(ctx, oyuncu: discord.Member = None, *, sebep: str = "Sebep belirtilmedi"):
    if oyuncu is None:
        await ctx.send("Kullanım: `B!uyar @üye <sebep>`")
        return
    if oyuncu.bot or oyuncu.id == ctx.author.id:
        await ctx.send("Kendini veya botu uyaramazsın.")
        return
    cursor.execute("INSERT INTO warnings (guild_id,user_id,moderator_id,reason,created_at) VALUES (?,?,?,?,?)", (ctx.guild.id, oyuncu.id, ctx.author.id, sebep[:1000], time.time()))
    db.commit()
    cursor.execute("SELECT COUNT(*) FROM warnings WHERE guild_id=? AND user_id=?", (ctx.guild.id, oyuncu.id))
    count = cursor.fetchone()[0]
    await ctx.send(f"{oyuncu.mention} uyarıldı. Toplam kayıtlı uyarı: **{count}**. Sebep: {discord.utils.escape_mentions(sebep)[:500]}")
    await send_security_log(ctx.guild, f"Üye uyarıldı: {oyuncu} ({oyuncu.id}) — yetkili: {ctx.author} — sebep: {sebep[:500]}")


@bot.command(name="uyarilar", aliases=["uyarılar", "uyarigeçmişi"])
@commands.guild_only()
@commands.has_guild_permissions(moderate_members=True)
async def uyarilar(ctx, oyuncu: discord.Member = None):
    if oyuncu is None:
        await ctx.send("Kullanım: `B!uyarilar @üye`")
        return
    cursor.execute("SELECT id,reason,moderator_id,created_at FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 10", (ctx.guild.id, oyuncu.id))
    rows = cursor.fetchall()
    if not rows:
        await ctx.send(f"{oyuncu.mention} için kayıtlı uyarı yok.")
        return
    lines = []
    for wid, reason, moderator_id, stamp in rows:
        lines.append(f"**#{wid}** · {datetime.fromtimestamp(stamp, timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\nSebep: {reason[:300]}\nYetkili ID: `{moderator_id}`")
    await ctx.send(embed=discord.Embed(title=f"{oyuncu.display_name} | Uyarı Geçmişi", description="\n\n".join(lines)[:4000], color=discord.Color.orange()))


@bot.command(name="uyarisil", aliases=["uyarılarısil"])
@commands.guild_only()
@commands.has_guild_permissions(administrator=True)
async def uyarisil(ctx, oyuncu: discord.Member = None):
    if oyuncu is None:
        await ctx.send("Kullanım: `B!uyarisil @üye`")
        return
    cursor.execute("DELETE FROM warnings WHERE guild_id=? AND user_id=?", (ctx.guild.id, oyuncu.id))
    removed = cursor.rowcount
    db.commit()
    await ctx.send(f"{oyuncu.mention} için **{removed}** uyarı kaydı silindi.")
    await send_security_log(ctx.guild, f"Uyarı kayıtları silindi: {oyuncu} — yetkili: {ctx.author}; kayıt sayısı: {removed}")


@bot.command(name="sustur")
@commands.guild_only()
@commands.has_guild_permissions(moderate_members=True)
async def sustur(ctx, oyuncu: discord.Member = None, dakika: int = 5, *, sebep: str = "Sebep belirtilmedi"):
    if oyuncu is None or not 1 <= dakika <= 40320:
        await ctx.send("Kullanım: `B!sustur @üye <1-40320 dakika> <sebep>`")
        return
    if oyuncu.id == ctx.author.id or oyuncu.guild_permissions.administrator:
        await ctx.send("Kendini veya sunucu yöneticisini bu komutla susturamazsın.")
        return
    try:
        await oyuncu.timeout(discord.utils.utcnow() + timedelta(minutes=dakika), reason=f"{ctx.author}: {sebep[:400]}")
        await ctx.send(f"{oyuncu.mention} **{dakika} dakika** susturuldu. Sebep: {discord.utils.escape_mentions(sebep)[:500]}")
        await send_security_log(ctx.guild, f"Timeout: {oyuncu} — {dakika} dakika — yetkili {ctx.author} — sebep {sebep[:500]}")
    except (discord.Forbidden, discord.HTTPException):
        await ctx.send("Susturma uygulanamadı. Bot rol sırasını ve Üyeleri Zaman Aşımına Uğrat iznini kontrol et.")


@bot.command(name="susturac", aliases=["sustur-aç", "timeoutac"])
@commands.guild_only()
@commands.has_guild_permissions(moderate_members=True)
async def susturac(ctx, oyuncu: discord.Member = None):
    if oyuncu is None:
        await ctx.send("Kullanım: `B!susturac @üye`")
        return
    try:
        await oyuncu.timeout(None, reason=f"{ctx.author} timeout kaldırdı")
        await ctx.send(f"{oyuncu.mention} için zaman aşımı kaldırıldı.")
        await send_security_log(ctx.guild, f"Timeout kaldırıldı: {oyuncu} — yetkili {ctx.author}")
    except (discord.Forbidden, discord.HTTPException):
        await ctx.send("Zaman aşımı kaldırılamadı. Bot izinlerini ve rol sırasını kontrol et.")


async def finish_reminder(reminder_id, due_at=None):
    if reminder_id in scheduled_reminders:
        return
    scheduled_reminders.add(reminder_id)
    try:
        cursor.execute("SELECT guild_id,channel_id,user_id,due_at,reminder_text,done FROM reminders WHERE id=?", (reminder_id,))
        row = cursor.fetchone()
        if not row or row[5]:
            return
        guild_id, channel_id, user_id, stored_due, reminder_text, done = row
        await asyncio.sleep(max(0, float(stored_due) - time.time()))
        cursor.execute("SELECT done FROM reminders WHERE id=?", (reminder_id,))
        fresh = cursor.fetchone()
        if not fresh or fresh[0]:
            return
        channel = bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None
        if channel:
            try:
                await channel.send(f"<@{user_id}> hatırlatıcın: {discord.utils.escape_mentions(reminder_text)[:1500]}")
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"HATIRLATICI GONDERME HATASI: {exc}")
        cursor.execute("UPDATE reminders SET done=1 WHERE id=?", (reminder_id,))
        db.commit()
    finally:
        scheduled_reminders.discard(reminder_id)


def schedule_reminder(reminder_id, due_at=None):
    if reminder_id not in scheduled_reminders:
        asyncio.create_task(finish_reminder(reminder_id, due_at))


@bot.command(name="hatirlat", aliases=["hatırlat", "remind"])
async def hatirlat(ctx, dakika: int = None, *, metin: str = None):
    if dakika is None or not 1 <= dakika <= 10080 or not metin:
        await ctx.send("Kullanım: `B!hatirlat <1-10080 dakika> <hatırlatma>`")
        return
    due_at = time.time() + dakika * 60
    cursor.execute("INSERT INTO reminders (guild_id,channel_id,user_id,due_at,reminder_text,done) VALUES (?,?,?,?,?,0)", (ctx.guild.id if ctx.guild else 0, ctx.channel.id, ctx.author.id, due_at, metin[:1500]))
    db.commit()
    reminder_id = cursor.lastrowid
    schedule_reminder(reminder_id, due_at)
    await ctx.send(f"Tamam, {dakika} dakika sonra burada hatırlatacağım. Hatırlatıcı numaran: **#{reminder_id}**")


async def finish_giveaway(giveaway_id):
    if giveaway_id in scheduled_giveaways:
        return
    scheduled_giveaways.add(giveaway_id)
    try:
        cursor.execute("SELECT guild_id,channel_id,message_id,ends_at,prize,done FROM giveaways WHERE id=?", (giveaway_id,))
        row = cursor.fetchone()
        if not row or row[5]:
            return
        guild_id, channel_id, message_id, ends_at, prize, done = row
        await asyncio.sleep(max(0, float(ends_at) - time.time()))
        cursor.execute("SELECT done FROM giveaways WHERE id=?", (giveaway_id,))
        fresh = cursor.fetchone()
        if not fresh or fresh[0]:
            return
        channel = bot.get_channel(channel_id)
        message = None
        if channel:
            try:
                message = await channel.fetch_message(message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = None
        if message is not None:
            reaction = discord.utils.get(message.reactions, emoji="🎉")
            users = [u async for u in reaction.users()] if reaction else []
            candidates = [u for u in users if not u.bot]
            if candidates:
                winner = random.choice(candidates)
                await channel.send(f"Çekiliş bitti! Ödül: **{discord.utils.escape_mentions(prize)[:200]}** — Kazanan: {winner.mention}")
            else:
                await channel.send(f"Çekiliş bitti ancak katılımcı bulunamadı. Ödül: **{discord.utils.escape_mentions(prize)[:200]}**")
        cursor.execute("UPDATE giveaways SET done=1 WHERE id=?", (giveaway_id,))
        db.commit()
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"CEKILIS BITIRME HATASI: {exc}")
    finally:
        scheduled_giveaways.discard(giveaway_id)


def schedule_giveaway(giveaway_id):
    if giveaway_id not in scheduled_giveaways:
        asyncio.create_task(finish_giveaway(giveaway_id))


@bot.command(name="cekilis", aliases=["çekiliş", "giveaway"])
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def cekilis(ctx, dakika: int = None, *, odul: str = None):
    if dakika is None or not 1 <= dakika <= 10080 or not odul:
        await ctx.send("Kullanım: `B!cekilis <1-10080 dakika> <ödül>`")
        return
    ends_at = time.time() + dakika * 60
    embed = discord.Embed(title="ÇEKİLİŞ", description=f"**Ödül:** {discord.utils.escape_mentions(odul)[:500]}\nKatılmak için 🎉 tepkisine bas.\nBitiş: {discord.utils.format_dt(datetime.fromtimestamp(ends_at, timezone.utc), style='R')}", color=discord.Color.gold())
    message = await ctx.send(embed=embed)
    await message.add_reaction("🎉")
    cursor.execute("INSERT INTO giveaways (guild_id,channel_id,message_id,ends_at,prize,done) VALUES (?,?,?,?,?,0)", (ctx.guild.id, ctx.channel.id, message.id, ends_at, odul[:500]))
    db.commit()
    schedule_giveaway(cursor.lastrowid)


async def finish_poll(message_id):
    if message_id in scheduled_polls:
        return
    scheduled_polls.add(message_id)
    try:
        cursor.execute("SELECT channel_id,question,ends_at,done FROM polls WHERE message_id=?", (message_id,))
        row = cursor.fetchone()
        if not row or row[3]:
            return
        channel_id, question, ends_at, done = row
        await asyncio.sleep(max(0, float(ends_at) - time.time()))
        channel = bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None
        if channel:
            try:
                message = await channel.fetch_message(message_id)
                counts = []
                for reaction in message.reactions:
                    if str(reaction.emoji) in ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"):
                        users = [u async for u in reaction.users()]
                        counts.append((str(reaction.emoji), max(0, sum(1 for u in users if not u.bot))))
                result = "\n".join(f"{emoji} **{count}** oy" for emoji, count in counts) or "Oy kullanılmadı."
                await channel.send(f"Oylama sona erdi: **{discord.utils.escape_mentions(question)[:300]}**\n{result}")
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                print(f"OYLAMA BITIRME HATASI: {exc}")
        cursor.execute("UPDATE polls SET done=1 WHERE message_id=?", (message_id,))
        db.commit()
    finally:
        scheduled_polls.discard(message_id)


def schedule_poll(message_id):
    if message_id not in scheduled_polls:
        asyncio.create_task(finish_poll(message_id))


@bot.command(name="oylama", aliases=["anket", "poll"])
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def oylama(ctx, *, metin: str = None):
    if not metin:
        await ctx.send("Kullanım: `B!oylama <dakika> <soru> | <seçenek 1> | <seçenek 2>`")
        return
    try:
        minute_text, rest = metin.strip().split(maxsplit=1)
        dakika = int(minute_text)
        parts = [part.strip() for part in rest.split("|") if part.strip()]
    except (ValueError, TypeError):
        await ctx.send("Örneğin: `B!oylama 10 En iyi oyun? | Minecraft | Roblox | Among Us`")
        return
    if not 1 <= dakika <= 10080 or len(parts) < 3 or len(parts) > 6:
        await ctx.send("Süre 1-10080 dakika, seçenek sayısı 2-5 olmalı. İlk bölüm soru, kalanlar seçeneklerdir.")
        return
    question, options = parts[0], parts[1:]
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    lines = [f"{emojis[i]} {discord.utils.escape_mentions(option)[:200]}" for i, option in enumerate(options)]
    ends_at = time.time() + dakika * 60
    embed = discord.Embed(title="Sunucu Oylaması", description=f"**{discord.utils.escape_mentions(question)[:500]}**\n\n" + "\n".join(lines), color=discord.Color.blurple())
    embed.set_footer(text=f"Oylama {dakika} dakika sonra kapanır")
    message = await ctx.send(embed=embed)
    for emoji in emojis[:len(options)]:
        await message.add_reaction(emoji)
    cursor.execute("INSERT OR REPLACE INTO polls (message_id,guild_id,channel_id,question,ends_at,done) VALUES (?,?,?,?,?,0)", (message.id, ctx.guild.id, ctx.channel.id, question[:500], ends_at))
    db.commit()
    schedule_poll(message.id)


def make_reaction_gif(name, title, subtitle):
    width, height = 640, 320
    frames = []
    try:
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 36)
        mid_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 27)
        small_font = ImageFont.truetype("DejaVuSans.ttf", 19)
    except OSError:
        title_font = mid_font = small_font = ImageFont.load_default()
    for frame_no in range(18):
        image = Image.new("RGB", (width, height), (17, 20, 45))
        draw = ImageDraw.Draw(image)
        pulse = (frame_no % 9) / 8
        for i in range(24):
            x = (i * 79 + frame_no * 13) % width
            y = (i * 47 + frame_no * 7) % height
            r = 2 + (i % 3)
            draw.ellipse((x-r, y-r, x+r, y+r), fill=(80 + (i*3)%100, 150 + (i*5)%100, 230))
        draw.rounded_rectangle((35, 32, width-35, height-32), radius=24, outline=(120, 110, 255), width=3)
        big = title[:30]
        box = draw.textbbox((0,0), big, font=title_font)
        draw.text(((width-(box[2]-box[0]))/2, 60), big, font=title_font, fill=(255, 235, 170))
        label = name[:28]
        box = draw.textbbox((0,0), label, font=mid_font)
        draw.text(((width-(box[2]-box[0]))/2, 145 + int(4*pulse)), label, font=mid_font, fill=(255,255,255))
        box = draw.textbbox((0,0), subtitle[:42], font=small_font)
        draw.text(((width-(box[2]-box[0]))/2, 222), subtitle[:42], font=small_font, fill=(130, 255, 200))
        frames.append(image)
    output = io.BytesIO()
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:], duration=85, loop=0, optimize=True)
    output.seek(0)
    return output


@bot.command(name="hype", aliases=["gaz", "costur"])
async def hype(ctx, oyuncu: discord.Member = None):
    target = oyuncu or ctx.author
    try:
        gif = make_reaction_gif(target.display_name, "HYPE MODE", "Sunucunun enerjisi yükseliyor")
        await ctx.send(content=f"{target.mention} HYPE MODE AKTİF", file=discord.File(gif, filename="hype.gif"))
    except (discord.HTTPException, OSError, ValueError) as exc:
        print(f"HYPE GIF HATASI: {type(exc).__name__}: {exc}")
        await ctx.send(f"{target.mention} HYPE MODE AKTİF")


@bot.command(name="zafer", aliases=["victory", "kutla"])
async def zafer(ctx, oyuncu: discord.Member = None):
    target = oyuncu or ctx.author
    try:
        gif = make_reaction_gif(target.display_name, "ZAFER ANI", "Efsane başarı kaydedildi")
        await ctx.send(content=f"{target.mention} zafer animasyonu başlıyor", file=discord.File(gif, filename="zafer.gif"))
    except (discord.HTTPException, OSError, ValueError) as exc:
        print(f"ZAFER GIF HATASI: {type(exc).__name__}: {exc}")
        await ctx.send(f"{target.mention} zafer animasyonu başlıyor")


async def finish_tournament(message_id):
    if message_id in scheduled_tournaments:
        return
    scheduled_tournaments.add(message_id)
    try:
        cursor.execute("SELECT channel_id,title,ends_at,done FROM tournaments WHERE message_id=?", (message_id,))
        row = cursor.fetchone()
        if not row or row[3]:
            return
        channel_id, title, ends_at, done = row
        await asyncio.sleep(max(0, float(ends_at) - time.time()))
        channel = bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None
        if channel:
            try:
                message = await channel.fetch_message(message_id)
                reaction = discord.utils.get(message.reactions, emoji="🏆")
                users = [u async for u in reaction.users()] if reaction else []
                participants = [u for u in users if not u.bot]
                if participants:
                    preview = ", ".join(user.mention for user in participants[:40])
                    extra = f" ve {len(participants)-40} kişi daha" if len(participants) > 40 else ""
                    await channel.send(f"Kayıtları kapanan turnuva: **{discord.utils.escape_mentions(title)[:250]}**\nKatılımcılar ({len(participants)}): {preview}{extra}\nMaç eşleşmelerini düzenleyici belirleyebilir.")
                else:
                    await channel.send(f"**{discord.utils.escape_mentions(title)[:250]}** turnuvasının kayıtları kapandı; katılımcı çıkmadı.")
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                print(f"TURNUVA BITIRME HATASI: {exc}")
        cursor.execute("UPDATE tournaments SET done=1 WHERE message_id=?", (message_id,))
        db.commit()
    finally:
        scheduled_tournaments.discard(message_id)


def schedule_tournament(message_id):
    if message_id not in scheduled_tournaments:
        asyncio.create_task(finish_tournament(message_id))


@bot.command(name="turnuva", aliases=["turnuvaac", "turnuva-ac"])
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def turnuva(ctx, dakika: int = None, *, baslik: str = None):
    if dakika is None or not 1 <= dakika <= 10080 or not baslik:
        await ctx.send("Kullanım: `B!turnuva <1-10080 dakika> <oyun veya etkinlik adı>`")
        return
    ends_at = time.time() + dakika * 60
    embed = discord.Embed(title="Turnuva kayıtları", description=f"**Etkinlik:** {discord.utils.escape_mentions(baslik)[:500]}\nKatılmak için 🏆 tepkisine bas.\nKayıt bitişi: {discord.utils.format_dt(datetime.fromtimestamp(ends_at, timezone.utc), style='R')}", color=discord.Color.gold())
    message = await ctx.send(embed=embed)
    await message.add_reaction("🏆")
    cursor.execute("INSERT OR REPLACE INTO tournaments (message_id,guild_id,channel_id,title,ends_at,done) VALUES (?,?,?,?,?,0)", (message.id, ctx.guild.id, ctx.channel.id, baslik[:500], ends_at))
    db.commit()
    schedule_tournament(message.id)


@bot.command(name="botdurum")
async def botdurum(ctx):
    total_members = sum(g.member_count or 0 for g in bot.guilds)
    embed = discord.Embed(title=f"Baklava1923 Bot {BOT_VERSION}", color=discord.Color.green() if bot.is_ready() else discord.Color.red())
    embed.add_field(name="Durum", value="Çalışıyor" if bot.is_ready() else "Hazır değil", inline=True)
    embed.add_field(name="Ping", value=f"{round(bot.latency * 1000)} ms" if bot.is_ready() else "—", inline=True)
    embed.add_field(name="Sunucu", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="Üye toplamı (yaklaşık)", value=str(total_members), inline=True)
    embed.add_field(name="Komut önekleri", value="`!` · `B!` · `b!`", inline=True)
    embed.add_field(name="AI ayarı", value="Hazır" if GROQ_API_KEY else "GROQ_API_KEY eksik", inline=True)
    await ctx.send(embed=embed)


# -------------------- 3.6.2 ULTIMATE KOMUT PAKETI --------------------

@bot.command(name="afk", aliases=["uzakta", "away"])
@commands.guild_only()
async def afk_command(ctx, *, sebep: str = "Şu anda meşgul"):
    sebep = discord.utils.escape_mentions(sebep.strip())[:250]
    cursor.execute("INSERT OR REPLACE INTO afk_status (guild_id,user_id,reason,since) VALUES (?,?,?,?)", (ctx.guild.id, ctx.author.id, sebep, time.time()))
    db.commit()
    await ctx.send(f"{ctx.author.mention} AFK modu açıldı · Sebep: {sebep}")


@bot.command(name="özelkomut-ekle", aliases=["ozelkomutekle", "komutoluştur"])
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def custom_command_add(ctx, *, veri: str = None):
    if not veri or "|" not in veri:
        await ctx.send("Kullanım: `B!özelkomut-ekle isim | cevap`")
        return
    isim, cevap = (part.strip() for part in veri.split("|", 1))
    isim = isim.lower().replace(" ", "-")
    if not re.fullmatch(r"[a-z0-9_-]{2,24}", isim) or isim in {c.name for c in bot.commands}:
        await ctx.send("Komut adı 2-24 karakter olmalı ve mevcut komutlarla çakışmamalı.")
        return
    if not cevap or len(cevap) > 1500:
        await ctx.send("Cevap 1-1500 karakter arasında olmalı.")
        return
    cursor.execute("INSERT OR REPLACE INTO custom_commands (guild_id,command_name,response,created_by,created_at) VALUES (?,?,?,?,?)", (ctx.guild.id, isim, cevap, ctx.author.id, time.time()))
    db.commit()
    await ctx.send(f"Özel komut `{isim}` kaydedildi. Kullanım: `B!{isim}`")


@bot.command(name="özelkomut-sil", aliases=["ozelkomutsil"])
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def custom_command_delete(ctx, *, isim: str = None):
    if not isim:
        await ctx.send("Kullanım: `B!özelkomut-sil <isim>`")
        return
    cursor.execute("DELETE FROM custom_commands WHERE guild_id=? AND command_name=?", (ctx.guild.id, isim.lower().strip()))
    removed = cursor.rowcount
    db.commit()
    await ctx.send("Özel komut silindi." if removed else "Bu isimde özel komut bulunamadı.")


@bot.command(name="özelkomutlar", aliases=["ozelkomutlar"])
@commands.guild_only()
async def custom_command_list(ctx):
    cursor.execute("SELECT command_name FROM custom_commands WHERE guild_id=? ORDER BY command_name LIMIT 50", (ctx.guild.id,))
    names = [row[0] for row in cursor.fetchall()]
    await ctx.send("**Sunucu özel komutları**\n" + (" · ".join(f'`{n}`' for n in names) if names else "Henüz özel komut yok."))


@bot.command(name="rolver", aliases=["rolverme"])
@commands.guild_only()
@commands.has_guild_permissions(manage_roles=True)
@commands.bot_has_guild_permissions(manage_roles=True)
async def role_give(ctx, uye: discord.Member = None, rol: discord.Role = None):
    if uye is None or rol is None:
        await ctx.send("Kullanım: `B!rolver @üye @rol`")
        return
    if rol.is_default() or rol.managed or rol >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
        await ctx.send("Bu rolü verme yetkin yok.")
        return
    if ctx.guild.me and rol >= ctx.guild.me.top_role:
        await ctx.send("Botun rolü hedef rolden yüksek olmalı.")
        return
    await uye.add_roles(rol, reason=f"{ctx.author} tarafından verildi")
    await ctx.send(f"{rol.mention} rolü {uye.mention} kullanıcısına verildi.")
    await send_security_log(ctx.guild, f"ROL VERİLDİ: {uye} ← {rol} · Yetkili: {ctx.author}")


@bot.command(name="rolal", aliases=["rolkaldir"])
@commands.guild_only()
@commands.has_guild_permissions(manage_roles=True)
@commands.bot_has_guild_permissions(manage_roles=True)
async def role_remove(ctx, uye: discord.Member = None, rol: discord.Role = None):
    if uye is None or rol is None:
        await ctx.send("Kullanım: `B!rolal @üye @rol`")
        return
    if rol.is_default() or rol.managed or (rol >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id):
        await ctx.send("Bu rolü kaldırma yetkin yok.")
        return
    if ctx.guild.me and rol >= ctx.guild.me.top_role:
        await ctx.send("Botun rolü hedef rolden yüksek olmalı.")
        return
    await uye.remove_roles(rol, reason=f"{ctx.author} tarafından kaldırıldı")
    await ctx.send(f"{rol.mention} rolü {uye.mention} kullanıcısından kaldırıldı.")
    await send_security_log(ctx.guild, f"ROL KALDIRILDI: {uye} - {rol} · Yetkili: {ctx.author}")


@bot.command(name="yavasmod", aliases=["slowmode", "yavaşmod"])
@commands.guild_only()
@commands.has_guild_permissions(manage_channels=True)
@commands.bot_has_guild_permissions(manage_channels=True)
async def slowmode_command(ctx, saniye: int = None):
    if saniye is None or not 0 <= saniye <= 21600:
        await ctx.send("Kullanım: `B!yavasmod <0-21600 saniye>` · 0 kapatır")
        return
    await ctx.channel.edit(slowmode_delay=saniye, reason=f"{ctx.author} tarafından ayarlandı")
    await ctx.send(f"Bu kanalın yavaş modu **{saniye} saniye** olarak ayarlandı.")


@bot.command(name="duyuruyap", aliases=["embedduyuru"])
@commands.guild_only()
@commands.has_guild_permissions(manage_messages=True)
async def rich_announcement(ctx, *, veri: str = None):
    if not veri or "|" not in veri:
        await ctx.send("Kullanım: `B!duyuruyap başlık | mesaj`")
        return
    baslik, metin = (x.strip() for x in veri.split("|", 1))
    if not baslik or not metin:
        await ctx.send("Başlık ve mesaj boş olamaz.")
        return
    embed = discord.Embed(title=discord.utils.escape_mentions(baslik[:250]), description=discord.utils.escape_mentions(metin[:4000]), color=discord.Color.gold(), timestamp=discord.utils.utcnow())
    embed.set_footer(text=f"{ctx.guild.name} · {ctx.author.display_name}")
    await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="sunucistatistik", aliases=["sunucuistatistik", "serverstats"])
@commands.guild_only()
async def server_stats(ctx):
    guild = ctx.guild
    humans = sum(not m.bot for m in guild.members)
    bots = sum(m.bot for m in guild.members)
    online = sum(1 for m in guild.members if getattr(m.status, "value", "offline") != "offline")
    text_channels = len(guild.text_channels)
    voice_channels = len(guild.voice_channels)
    embed = discord.Embed(title=f"{guild.name} · Sunucu istatistikleri", color=discord.Color.blurple())
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="Üyeler", value=f"Toplam: {guild.member_count}\nİnsan: {humans}\nBot: {bots}\nÇevrimiçi: {online}", inline=True)
    embed.add_field(name="Kanallar", value=f"Metin: {text_channels}\nSes: {voice_channels}\nKategori: {len(guild.categories)}", inline=True)
    embed.add_field(name="Roller ve emojiler", value=f"Rol: {len(guild.roles)}\nEmoji: {len(guild.emojis)}\nSticker: {len(guild.stickers)}", inline=True)
    embed.add_field(name="Kuruluş", value=discord.utils.format_dt(guild.created_at, style="F"), inline=False)
    embed.add_field(name="Sahip", value=f"<@{guild.owner_id}>", inline=True)
    embed.add_field(name="Sunucu kimliği", value=str(guild.id), inline=True)
    await ctx.send(embed=embed)


@bot.command(name="yetkili-notu", aliases=["notekle", "modnot"])
@commands.guild_only()
@commands.has_guild_permissions(moderate_members=True)
async def moderator_note_add(ctx, uye: discord.Member = None, *, not_metni: str = None):
    if uye is None or not not_metni:
        await ctx.send("Kullanım: `B!yetkili-notu @üye <not>`")
        return
    if uye.bot or uye.id == ctx.guild.owner_id:
        await ctx.send("Bu üyeye not eklenemez.")
        return
    cursor.execute("INSERT INTO moderator_notes (guild_id,user_id,moderator_id,note,created_at) VALUES (?,?,?,?,?)", (ctx.guild.id, uye.id, ctx.author.id, not_metni[:700], time.time()))
    db.commit()
    await ctx.send(f"{uye.mention} için yetkili notu kaydedildi.")
    await send_security_log(ctx.guild, f"YETKİLİ NOTU: {uye} için {ctx.author} not ekledi.")


@bot.command(name="notlar", aliases=["yetkilinotlari"])
@commands.guild_only()
@commands.has_guild_permissions(moderate_members=True)
async def moderator_notes_list(ctx, uye: discord.Member = None):
    if uye is None:
        await ctx.send("Kullanım: `B!notlar @üye`")
        return
    cursor.execute("SELECT moderator_id,note,created_at FROM moderator_notes WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 10", (ctx.guild.id, uye.id))
    rows = cursor.fetchall()
    if not rows:
        await ctx.send("Bu üye için kayıtlı yetkili notu yok.")
        return
    lines = [f"• <@{mid}> · {discord.utils.escape_mentions(note)} · {discord.utils.format_dt(datetime.fromtimestamp(ts, timezone.utc), style='R')}" for mid, note, ts in rows]
    embed = discord.Embed(title=f"{uye.display_name} · Yetkili notları", description="\n".join(lines)[:4000], color=discord.Color.orange())
    await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="doğumgünü", aliases=["dogumgunu", "birthday"])
@commands.guild_only()
async def birthday_set(ctx, gun: int = None, ay: int = None):
    if gun is None or ay is None or not 1 <= ay <= 12 or not 1 <= gun <= 31:
        await ctx.send("Kullanım: `B!doğumgünü <gün> <ay>` örnek `B!doğumgünü 25 12`")
        return
    try:
        datetime(2024, ay, gun)
    except ValueError:
        await ctx.send("Geçerli bir gün ve ay gir.")
        return
    cursor.execute("INSERT OR REPLACE INTO birthdays (guild_id,user_id,month,day) VALUES (?,?,?,?)", (ctx.guild.id, ctx.author.id, ay, gun))
    db.commit()
    await ctx.send(f"Doğum günün **{gun:02d}.{ay:02d}** olarak kaydedildi.")


@bot.command(name="doğumgünüm", aliases=["dogumgunum"])
@commands.guild_only()
async def birthday_show(ctx):
    cursor.execute("SELECT month,day FROM birthdays WHERE guild_id=? AND user_id=?", (ctx.guild.id, ctx.author.id))
    row = cursor.fetchone()
    await ctx.send(f"Kayıtlı doğum günün **{row[1]:02d}.{row[0]:02d}**." if row else "Henüz doğum gününü kaydetmedin. `B!doğumgünü 25 12`")


@bot.command(name="kelimeengelle", aliases=["filtreekle"])
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def filtered_word_add(ctx, *, kelime: str = None):
    kelime = (kelime or "").strip()
    if not 2 <= len(kelime) <= 50:
        await ctx.send("Kullanım: `B!kelimeengelle <2-50 karakter>`")
        return
    cursor.execute("INSERT OR IGNORE INTO filtered_words (guild_id,word,added_by,added_at) VALUES (?,?,?,?)", (ctx.guild.id, kelime, ctx.author.id, time.time()))
    db.commit()
    await ctx.send("Kelime filtreye eklendi." if cursor.rowcount else "Bu kelime zaten filtrede.")


@bot.command(name="kelimeizinver", aliases=["filtresil"])
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def filtered_word_remove(ctx, *, kelime: str = None):
    if not kelime:
        await ctx.send("Kullanım: `B!kelimeizinver <kelime>`")
        return
    cursor.execute("DELETE FROM filtered_words WHERE guild_id=? AND word=?", (ctx.guild.id, kelime.strip()))
    removed = cursor.rowcount
    db.commit()
    await ctx.send("Kelime filtreden kaldırıldı." if removed else "Bu kelime filtrede yok.")


@bot.command(name="kelimefiltrelistesi", aliases=["filtrelistesi"])
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def filtered_word_list(ctx):
    cursor.execute("SELECT word FROM filtered_words WHERE guild_id=? ORDER BY word LIMIT 40", (ctx.guild.id,))
    words = [row[0] for row in cursor.fetchall()]
    await ctx.send("**Filtrelenen kelimeler**\n" + ("\n".join(f"• `{discord.utils.escape_markdown(w)}`" for w in words) if words else "Liste boş."))


@bot.event
async def on_message_custom_commands(message):
    # Bu yardımcı olay doğrudan Discord tarafından çağrılmaz; özel komut kontrolü on_message içinde yapılır.
    return


# -------------------- HATALAR VE BASLATMA --------------------
@bot.event
async def on_ready():
    print(f"{bot.user} olarak giris yapildi. Bot surumu: {BOT_VERSION}")
    if not getattr(bot, "_mega_expansion_loaded", False):
        try:
            await bot.load_extension("baklava_mega")
            bot._mega_expansion_loaded = True
            print("MEGA EKLENTI: Yeni sistemler yuklendi.")
        except Exception as exc:
            print(f"MEGA EKLENTI HATASI: {type(exc).__name__}: {exc}")
    if GITHUB_DATA_TOKEN and not getattr(bot, "_github_backup_task", None):
        bot._github_backup_task = asyncio.create_task(github_backup_loop())
    global _verification_view_registered
    if not _verification_view_registered:
        bot.add_view(VerificationView())
        _verification_view_registered = True
    for guild in bot.guilds:
        ensure_guild_settings(guild.id)
        ensure_raid_settings(guild.id)
        for member in guild.members:
            if not member.bot and is_active(member.status):
                start_tracking(guild.id, member.id)
    # Zamanli islemleri yeniden baslatmadan sonra da devam ettir.
    cursor.execute("SELECT id,due_at FROM reminders WHERE done=0")
    for reminder_id, due_at in cursor.fetchall():
        schedule_reminder(reminder_id, due_at)
    cursor.execute("SELECT id FROM giveaways WHERE done=0")
    for (giveaway_id,) in cursor.fetchall():
        schedule_giveaway(giveaway_id)
    cursor.execute("SELECT message_id FROM polls WHERE done=0")
    for (message_id,) in cursor.fetchall():
        schedule_poll(message_id)
    cursor.execute("SELECT message_id FROM tournaments WHERE done=0")
    for (message_id,) in cursor.fetchall():
        schedule_tournament(message_id)
    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)} slash komutu senkronize edildi.")
    except discord.HTTPException as exc:
        print(f"Slash komutlari senkronize edilemedi: {exc}")


# -------------------- 3.6.4: YENI EGLENCE, BALIK VE MOD ARACLARI --------------------
EIGHT_BALL_ANSWERS = [
    "Kesinlikle evet.", "Bence evet.", "Görünüşe göre evet.", "Büyük ihtimalle evet.",
    "Belki.", "Şimdi söylemesem daha iyi.", "Pek umut yok.", "Bence hayır.", "Kesinlikle hayır.",
]
WHEEL_PRIZES = [
    ("Boş", 0, 30), ("50 paklava", 50, 25), ("100 paklava", 100, 18), ("250 paklava", 250, 12),
    ("500 paklava", 500, 8), ("1000 paklava", 1000, 5), ("5000 paklava", 5000, 2),
]
ESPRI_LIST = [
    "Bilgisayar neden üşümüş? Çünkü Windows'u açık bırakmışlar.",
    "Programcılar neden karanlık modu sever? Hataları daha az görüyorlarmış.",
    "Oyuncu neden doktora gitmiş? Pingi uzaya çıkmış.",
    "Telefon neden yorgun? Bütün gün şarjdan kalkmamış.",
    "Balık neden internete girmez? Ağı zaten suda buluyor.",
    "Klavye neden mutsuz? Her gün birileri ona vuruyormuş.",
]


@bot.command(name="sekizbilir", aliases=["8ball"])
async def sekiz_bilir(ctx, *, soru: str = None):
    if not soru:
        await ctx.send("Kullanım: `B!sekizbilir <soru>`")
        return
    await ctx.send(
        f"🎱 **Soru:** {soru[:200]}\n**Cevap:** {random.choice(EIGHT_BALL_ANSWERS)}",
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.command(name="çarkıfelek", aliases=["carkifelek", "çark"])
async def carki_felek(ctx):
    remaining = fun_cooldown_remaining(ctx.author.id, "wheel", 60)
    if remaining:
        await ctx.send(f"Çarkı tekrar çevirmek için **{remaining} saniye** bekle.")
        return
    set_fun_cooldown(ctx.author.id, "wheel")
    label, prize, _ = random.choices(WHEEL_PRIZES, weights=[w for _, _, w in WHEEL_PRIZES], k=1)[0]
    if prize:
        add_balance(0, ctx.author.id, prize)
    await ctx.send(
        f"🎡 {ctx.author.mention} çarkı çevirdi ve **{label}** kazandı!\n"
        f"Güncel bakiye: **{balance_label(ctx.author.id, get_balance(0, ctx.author.id))}**"
    )


@bot.command(name="espri")
async def espri(ctx):
    await ctx.send(f"😄 {random.choice(ESPRI_LIST)}")


@bot.command(name="balıkbilgi", aliases=["balikbilgi", "balıkbilgisi"])
async def balik_bilgi(ctx, *, tur: str = None):
    if not tur:
        await ctx.send("Kullanım: `B!balıkbilgi <balık adı>` örnek: `B!balıkbilgi levrek`")
        return
    key = normalize_economy_key(tur)
    name_to_key = {normalize_economy_key(v["name"]): k for k, v in FISH_TYPES.items()}
    key = name_to_key.get(key, key)
    fish = FISH_TYPES.get(key)
    if not fish:
        await ctx.send("Bu balık türü bulunamadı. `B!balıklar` komutuna bak.")
        return
    cursor.execute("SELECT level FROM fishing_rods WHERE user_id=?", (ctx.author.id,))
    row = cursor.fetchone()
    level = max(0, min(MAX_ROD_LEVEL, int(row[0]))) if row else 0
    available, weights = fishing_weights(level)
    if key in available:
        chance = weights[available.index(key)] / sum(weights) * 100
        chance_text = f"%{chance:.2f}".replace(".", ",")
    else:
        chance_text = f"olta seviyesi **{fish['unlock']}** olunca açılır"
    embed = discord.Embed(title=fish["name"], color=discord.Color.teal())
    embed.add_field(name="Satış aralığı", value=f"{fish['min']:,} – {fish['max']:,} paklava".replace(",", "."), inline=False)
    embed.add_field(name="Açılma seviyesi", value=str(fish["unlock"]), inline=True)
    embed.add_field(name="Senin şansın", value=chance_text, inline=True)
    await ctx.send(embed=embed)


@bot.command(name="unban", aliases=["yasakkaldir", "yasakac"])
@commands.cooldown(1, 15, commands.BucketType.guild)
@commands.guild_only()
@commands.has_guild_permissions(ban_members=True)
@commands.bot_has_guild_permissions(ban_members=True)
async def unban_member(ctx, kullanici_id: int = None, *, sebep: str = "Sebep belirtilmedi"):
    if kullanici_id is None:
        await ctx.send("Kullanım: `B!unban <kullanıcı_id> [sebep]`")
        return
    try:
        user = await bot.fetch_user(kullanici_id)
        await ctx.guild.unban(user, reason=f"{ctx.author} tarafından: {sebep}")
    except discord.NotFound:
        await ctx.send("Bu ID ile yasaklı bir kullanıcı bulunamadı.")
        return
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Yasak kaldırılamadı: {type(exc).__name__}. İzinleri kontrol et.")
        return
    await ctx.send(f"**{user}** kullanıcısının yasağı kaldırıldı.", allowed_mentions=discord.AllowedMentions.none())
    await send_security_log(ctx.guild, f"Yasak kaldırıldı: {user} ({user.id}) · Yetkili: {ctx.author} · Sebep: {sebep}")


@bot.command(name="nick", aliases=["takmaad", "isimdegistir"])
@commands.guild_only()
@commands.has_guild_permissions(manage_nicknames=True)
@commands.bot_has_guild_permissions(manage_nicknames=True)
async def nick_command(ctx, oyuncu: discord.Member = None, *, yeni_isim: str = None):
    if oyuncu is None or yeni_isim is None:
        await ctx.send("Kullanım: `B!nick @üye <yeni isim>` · takma adı kaldırmak için `B!nick @üye sıfırla`")
        return
    if oyuncu.top_role >= ctx.guild.me.top_role or oyuncu == ctx.guild.owner:
        await ctx.send("Bu üyenin takma adını değiştirmek için botun rolü o üyeden daha üstte olmalı.")
        return
    reset = normalize_economy_key(yeni_isim) == "sifirla"
    new_nick = None if reset else yeni_isim.strip()[:32]
    try:
        await oyuncu.edit(nick=new_nick, reason=f"{ctx.author} tarafından takma ad değişikliği")
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Takma ad değiştirilemedi: {type(exc).__name__}.")
        return
    shown = new_nick or oyuncu.name
    await ctx.send(f"**{shown}** olarak güncellendi.", allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="kanalbilgi", aliases=["kanalbilgisi", "channelinfo"])
@commands.guild_only()
@commands.has_guild_permissions(manage_channels=True)
async def kanal_bilgi(ctx, kanal: discord.TextChannel = None):
    kanal = kanal or ctx.channel
    embed = discord.Embed(title=f"#{kanal.name}", color=discord.Color.blurple())
    embed.add_field(name="Kanal kimliği", value=str(kanal.id), inline=True)
    embed.add_field(name="Kategori", value=kanal.category.name if kanal.category else "Yok", inline=True)
    embed.add_field(name="Yavaş mod", value=f"{kanal.slowmode_delay} saniye" if kanal.slowmode_delay else "Kapalı", inline=True)
    embed.add_field(name="NSFW", value="Evet" if kanal.is_nsfw() else "Hayır", inline=True)
    embed.add_field(name="Oluşturulma", value=discord.utils.format_dt(kanal.created_at, "R"), inline=True)
    embed.add_field(name="Konu", value=(kanal.topic or "Konu yok")[:200], inline=False)
    await ctx.send(embed=embed)


# -------------------- 3.6.4: EK KOMUTLAR --------------------
@bot.command(name="karar", aliases=["sec", "seç"])
async def karar_command(ctx, *, secenekler: str = None):
    if not secenekler or "|" not in secenekler:
        await ctx.send("Kullanım: `B!karar seçenek1 | seçenek2 | seçenek3`")
        return
    options = [o.strip() for o in secenekler.split("|") if o.strip()]
    if len(options) < 2:
        await ctx.send("En az iki seçenek yaz. Örnek: `B!karar pizza | makarna`")
        return
    if len(options) > 20:
        await ctx.send("En fazla 20 seçenek yazabilirsin.")
        return
    choice = random.choice(options)[:200]
    await ctx.send(f"🤔 Seçimim: **{choice}**", allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="renk", aliases=["color", "rastgelerenk"])
async def renk_command(ctx):
    value = random.randint(0, 0xFFFFFF)
    embed = discord.Embed(title=f"#{value:06X}", description="Rastgele renk kodu", color=discord.Color(value))
    await ctx.send(embed=embed)


@bot.command(name="şanslısayı", aliases=["sanslisayi", "sansisayi"])
async def sanslisayi(ctx):
    # Ayni kullanici icin gun icinde sayi degismez.
    today = time.strftime("%Y-%m-%d")
    number = random.Random(f"{ctx.author.id}-{today}").randint(1, 99)
    await ctx.send(f"🍀 {ctx.author.mention} bugünkü şanslı sayın: **{number}**")


@bot.command(name="kullanıcıbilgi", aliases=["kullanicibilgi", "userinfo"])
@commands.guild_only()
async def kullanici_bilgi(ctx, uye: discord.Member = None):
    uye = uye or ctx.author
    roles = [r.mention for r in reversed(uye.roles[1:])]
    embed = discord.Embed(
        title=uye.display_name,
        color=uye.color if uye.color.value else discord.Color.blurple(),
    )
    embed.set_thumbnail(url=uye.display_avatar.url)
    embed.add_field(name="Kullanıcı adı", value=str(uye), inline=True)
    embed.add_field(name="Kimlik", value=str(uye.id), inline=True)
    embed.add_field(name="Hesap açılış", value=discord.utils.format_dt(uye.created_at, "D"), inline=True)
    embed.add_field(
        name="Sunucuya katılış",
        value=discord.utils.format_dt(uye.joined_at, "D") if uye.joined_at else "Bilinmiyor",
        inline=True,
    )
    embed.add_field(name=f"Roller ({len(roles)})", value=(" ".join(roles))[:1000] or "Yok", inline=False)
    await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="rolbilgi", aliases=["roleinfo"])
@commands.guild_only()
async def rol_bilgi(ctx, rol: discord.Role = None):
    if rol is None:
        await ctx.send("Kullanım: `B!rolbilgi @rol`")
        return
    embed = discord.Embed(
        title=rol.name,
        color=rol.color if rol.color.value else discord.Color.blurple(),
    )
    embed.add_field(name="Kimlik", value=str(rol.id), inline=True)
    embed.add_field(name="Üye sayısı", value=str(len(rol.members)), inline=True)
    embed.add_field(name="Renk", value=f"#{rol.color.value:06X}", inline=True)
    embed.add_field(name="Oluşturulma", value=discord.utils.format_dt(rol.created_at, "D"), inline=True)
    embed.add_field(name="Bahsedilebilir", value="Evet" if rol.mentionable else "Hayır", inline=True)
    embed.add_field(name="Ayrı gösteriliyor", value="Evet" if rol.hoist else "Hayır", inline=True)
    await ctx.send(embed=embed)


# -------------------- 3.6.4: 20 EK KOMUT --------------------
import ast as _ast
import operator as _operator
from datetime import date as _date, datetime as _datetime, timedelta as _timedelta, timezone as _timezone
from math import ceil as _ceil

TR_TZ = _timezone(_timedelta(hours=3))  # Türkiye yıllık saat değişimi kullanmıyor, sabit UTC+3.

ADVICE_LIST = [
    "Her gün küçük bir adım at; birikince büyük fark yaratır.",
    "Uyku bir lüks değil, oyun performansının parçası.",
    "Mesaj atmadan önce bir kere oku, sonra gönder.",
    "Yardım istemek zayıflık değil, en hızlı çözüm yollarından biri.",
    "Kötü bir maçtan sonra mola ver, sonraki maç daha iyi gider.",
]
MOTIVATION_LIST = [
    "Bugün bir şey başarmak için yeterince iyi bir gün.",
    "Denemeye devam eden her zaman bir yere varır.",
    "Dünkü hatan, bugünkü dersin.",
    "Kendine inan, gerisini sabır halledecek.",
    "Bir adım daha: bu sefer de olur.",
]
BET_WEIGHTS = {"kirmizi": 18, "siyah": 18, "yesil": 2}
BET_MULTIPLIERS = {"kirmizi": 2, "siyah": 2, "yesil": 14}
BET_NAMES = {"kirmizi": "Kırmızı", "siyah": "Siyah", "yesil": "Yeşil"}
TREASURE_NOTHING_CHANCE = 0.1
BEG_NOTHING_CHANCE = 0.25
_CALC_OPS = {
    _ast.Add: _operator.add, _ast.Sub: _operator.sub, _ast.Mult: _operator.mul,
    _ast.Div: _operator.truediv, _ast.Pow: _operator.pow, _ast.Mod: _operator.mod,
    _ast.USub: _operator.neg, _ast.UAdd: _operator.pos,
}


def _safe_calc(node):
    """Sadece sayı ve temel aritmetik işlemlerine izin verir; eval kullanılmaz."""
    if isinstance(node, _ast.Expression):
        return _safe_calc(node.body)
    if isinstance(node, _ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, _ast.BinOp) and type(node.op) in _CALC_OPS:
        left = _safe_calc(node.left)
        right = _safe_calc(node.right)
        if isinstance(node.op, _ast.Pow):
            if abs(right) > 100 or (isinstance(left, int) and abs(left) > 1 and abs(right) * _math.log2(abs(left)) > 2000):
                raise ValueError("üs çok büyük")
        return _CALC_OPS[type(node.op)](left, right)
    if isinstance(node, _ast.UnaryOp) and type(node.op) in _CALC_OPS:
        return _CALC_OPS[type(node.op)](_safe_calc(node.operand))
    raise ValueError("desteklenmeyen ifade")


async def _fetch_message_or_warn(ctx, mesaj_id):
    try:
        return await ctx.channel.fetch_message(mesaj_id)
    except discord.NotFound:
        await ctx.send("Bu kanalda bu ID ile bir mesaj bulunamadı.")
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Mesaja ulaşılamadı: {type(exc).__name__}.")
    return None


@bot.command(name="tavsiye", aliases=["advice"])
async def tavsiye(ctx):
    await ctx.send(f"💡 {random.choice(ADVICE_LIST)}")


@bot.command(name="motivasyon", aliases=["motivation"])
async def motivasyon(ctx):
    await ctx.send(f"🔥 {random.choice(MOTIVATION_LIST)}")


@bot.command(name="zarkapis", aliases=["zarkapış"])
@commands.guild_only()
async def zar_kapis(ctx, rakip: discord.Member = None):
    if rakip is None or rakip.bot or rakip.id == ctx.author.id:
        await ctx.send("Kullanım: `B!zarkapis @oyuncu` (kendine veya bota karşı oynayamazsın)")
        return
    a, b = random.randint(1, 6), random.randint(1, 6)
    if a == b:
        result = "Berabere!"
    else:
        winner = ctx.author if a > b else rakip
        result = f"Kazanan: **{winner.display_name}**"
    await ctx.send(
        f"🎲 {ctx.author.mention} **{a}** — {rakip.mention} **{b}**\n{result}",
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.command(name="saat", aliases=["time"])
async def saat(ctx):
    now = _datetime.now(TR_TZ)
    await ctx.send(f"🕒 Türkiye saati: **{now:%d.%m.%Y %H:%M:%S}** (UTC+3)")


@bot.command(name="kacgun", aliases=["kaçgün", "geriyasay"])
async def kac_gun(ctx, gun: int = None, ay: int = None, yil: int = None):
    if gun is None or ay is None or yil is None:
        await ctx.send("Kullanım: `B!kacgun <gün> <ay> <yıl>` örnek: `B!kacgun 29 10 2027`")
        return
    try:
        target = _date(yil, ay, gun)
    except ValueError:
        await ctx.send("Geçersiz tarih girdin.")
        return
    delta = (target - _datetime.now(TR_TZ).date()).days
    if delta > 0:
        text = f"**{delta}** gün kaldı."
    elif delta == 0:
        text = "Bugün!"
    else:
        text = f"**{-delta}** gün önce."
    await ctx.send(f"📅 {target:%d.%m.%Y} için {text}")


@bot.command(name="hesapla", aliases=["hesap", "calc"])
async def hesapla(ctx, *, ifade: str = None):
    if not ifade:
        await ctx.send("Kullanım: `B!hesapla 12 * (3 + 4)` · desteklenen işlemler: + - * / % ve üs (**)")
        return
    try:
        tree = _ast.parse(ifade.replace(",", ".").strip(), mode="eval")
        result = _safe_calc(tree)
    except ZeroDivisionError:
        await ctx.send("Sıfıra bölme olmaz.")
        return
    except (ValueError, SyntaxError, OverflowError, TypeError):
        await ctx.send("Bu ifade hesaplanamadı. Sadece sayılar ve + - * / % ** kullan.")
        return
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    text = str(result) if len(str(result)) <= 300 else "Sonuç çok uzun."
    await ctx.send(f"🧮 `{ifade[:150]}` = **{text}**", allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="tersyazi", aliases=["reverse"])
async def tersyazi(ctx, *, metin: str = None):
    if not metin:
        await ctx.send("Kullanım: `B!tersyazi <metin>`")
        return
    await ctx.send(metin[:300][::-1], allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="kelimesay", aliases=["metinanaliz", "wordcount"])
async def kelime_say(ctx, *, metin: str = None):
    if not metin:
        await ctx.send("Kullanım: `B!kelimesay <metin>`")
        return
    words = len(metin.split())
    chars = len(metin)
    chars_no_space = len(metin.replace(" ", ""))
    sentences = sum(metin.count(p) for p in ".!?")
    embed = discord.Embed(title="Metin analizi", color=discord.Color.blurple())
    embed.add_field(name="Kelime", value=str(words), inline=True)
    embed.add_field(name="Karakter", value=str(chars), inline=True)
    embed.add_field(name="Boşluksuz karakter", value=str(chars_no_space), inline=True)
    embed.add_field(name="Cümle (yaklaşık)", value=str(max(1, sentences) if metin.strip() else 0), inline=True)
    await ctx.send(embed=embed)


@bot.command(name="bahis", aliases=["kumar"])
async def bahis(ctx, miktar: int = None, renk: str = None):
    if miktar is None or renk is None:
        await ctx.send("Kullanım: `B!bahis <miktar> <kırmızı|siyah|yeşil>` · kırmızı/siyah 2x, yeşil 14x")
        return
    choice = normalize_economy_key(renk)
    if choice not in BET_WEIGHTS:
        await ctx.send("Renk seç: `kırmızı`, `siyah` veya `yeşil`.")
        return
    if miktar <= 0:
        await ctx.send("Bahis miktarı en az 1 paklava olmalı.")
        return
    balance = get_balance(0, ctx.author.id)
    if ctx.author.id != OWNER_ID and balance < miktar:
        await ctx.send(f"Bu bahis için yeterli paklavan yok. Bakiyen **{balance_label(ctx.author.id, balance)}**.")
        return
    remaining = fun_cooldown_remaining(ctx.author.id, "bet", 30)
    if remaining:
        await ctx.send(f"Yeni bahis için **{remaining} saniye** bekle.")
        return
    set_fun_cooldown(ctx.author.id, "bet")
    result = random.choices(list(BET_WEIGHTS), weights=list(BET_WEIGHTS.values()), k=1)[0]
    if result == choice:
        gain = miktar * (BET_MULTIPLIERS[result] - 1)
        add_balance(0, ctx.author.id, gain)
        outcome = f"kazandın! **+{gain}** paklava"
    else:
        add_balance(0, ctx.author.id, -miktar)
        outcome = f"kaybettin. **-{miktar}** paklava"
    await ctx.send(
        f"🎰 Top **{BET_NAMES[result]}** geldi, {outcome}\n"
        f"Güncel bakiye: **{balance_label(ctx.author.id, get_balance(0, ctx.author.id))}**"
    )


@bot.command(name="hazineavi", aliases=["hazine"])
async def hazine_avi(ctx):
    remaining = fun_cooldown_remaining(ctx.author.id, "treasure", 3600)
    if remaining:
        await ctx.send(f"Hazine için **{remaining // 60} dakika** daha beklemen lazım.")
        return
    set_fun_cooldown(ctx.author.id, "treasure")
    if random.random() < TREASURE_NOTHING_CHANCE:
        await ctx.send("🗺️ Hazine avına çıktın ama sandık boştu.")
        return
    prize = random.randint(100, 800)
    add_balance(0, ctx.author.id, prize)
    await ctx.send(
        f"🏴‍☠️ Hazineyi buldun! **+{prize}** paklava\n"
        f"Güncel bakiye: **{balance_label(ctx.author.id, get_balance(0, ctx.author.id))}**"
    )


@bot.command(name="dilen", aliases=["dilenci"])
async def dilen(ctx):
    remaining = fun_cooldown_remaining(ctx.author.id, "beg", 300)
    if remaining:
        await ctx.send(f"Biraz daha bekle, **{remaining} saniye** sonra tekrar dilenebilirsin.")
        return
    set_fun_cooldown(ctx.author.id, "beg")
    if random.random() < BEG_NOTHING_CHANCE:
        await ctx.send("🙇 Kimse sana bir şey vermedi.")
        return
    amount = random.randint(5, 40)
    add_balance(0, ctx.author.id, amount)
    await ctx.send(f"🙇 Biri sana **{amount}** paklava verdi.")


@bot.command(name="balikliste", aliases=["balikfiyatlari", "balikfiyat"])
async def balik_liste(ctx, sayfa: int = 1):
    fishes = list(FISH_TYPES.values())
    per_page = 15
    pages = max(1, _ceil(len(fishes) / per_page))
    page = max(1, min(sayfa, pages))
    chunk = fishes[(page - 1) * per_page: page * per_page]
    lines = [
        f"**{fish['name']}** · {fish['min']:,} – {fish['max']:,} paklava · seviye {fish['unlock']}".replace(",", ".")
        for fish in chunk
    ]
    embed = discord.Embed(title="Balık fiyat listesi", description="\n".join(lines), color=discord.Color.teal())
    embed.set_footer(text=f"Sayfa {page}/{pages} · Sonraki sayfa: B!balikliste {min(page + 1, pages)}")
    await ctx.send(embed=embed)


@bot.command(name="balikzirvesi", aliases=["enegerlibaliklar", "topbalik"])
async def balik_zirvesi(ctx):
    top = sorted(FISH_TYPES.values(), key=lambda f: f["min"] + f["max"], reverse=True)[:10]
    lines = [
        f"{idx}. **{fish['name']}** · yaklaşık {(fish['min'] + fish['max']) // 2:,} paklava".replace(",", ".")
        for idx, fish in enumerate(top, start=1)
    ]
    embed = discord.Embed(title="En değerli 10 balık", description="\n".join(lines), color=discord.Color.gold())
    await ctx.send(embed=embed)


@bot.command(name="avatar", aliases=["pp"])
async def avatar_command(ctx, uye: discord.Member = None):
    uye = uye or ctx.author
    embed = discord.Embed(title=f"{uye.display_name} avatarı", color=discord.Color.blurple())
    embed.set_image(url=uye.display_avatar.url)
    await ctx.send(embed=embed)


@bot.command(name="sunucuresmi", aliases=["sunucuikon", "servericon"])
@commands.guild_only()
async def sunucu_resmi(ctx):
    if not ctx.guild.icon:
        await ctx.send("Bu sunucunun bir resmi yok.")
        return
    embed = discord.Embed(title=f"{ctx.guild.name} resmi", color=discord.Color.blurple())
    embed.set_image(url=ctx.guild.icon.url)
    await ctx.send(embed=embed)


@bot.command(name="kanalkonu", aliases=["konu"])
@commands.guild_only()
@commands.has_guild_permissions(manage_channels=True)
@commands.bot_has_guild_permissions(manage_channels=True)
async def kanal_konu(ctx, *, metin: str = None):
    if metin is None:
        await ctx.send("Kullanım: `B!kanalkonu <yeni konu>`")
        return
    if not isinstance(ctx.channel, discord.TextChannel):
        await ctx.send("Bu komut sadece normal metin kanallarında çalışır.")
        return
    try:
        await ctx.channel.edit(topic=metin[:1024], reason=f"{ctx.author} tarafından konu değişikliği")
        await ctx.send("Kanal konusu güncellendi.", delete_after=5)
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Konu değiştirilemedi: {type(exc).__name__}.")


@bot.command(name="kanalyenidenadlandir", aliases=["kanaladi"])
@commands.cooldown(1, 15, commands.BucketType.guild)
@commands.guild_only()
@commands.has_guild_permissions(manage_channels=True)
@commands.bot_has_guild_permissions(manage_channels=True)
async def kanal_yeniden_adlandir(ctx, *, isim: str = None):
    if not isim:
        await ctx.send("Kullanım: `B!kanalyenidenadlandir <yeni isim>`")
        return
    if not isinstance(ctx.channel, discord.TextChannel):
        await ctx.send("Bu komut sadece normal metin kanallarında çalışır.")
        return
    new_name = isim.strip().lower().replace(" ", "-")[:100]
    try:
        await ctx.channel.edit(name=new_name, reason=f"{ctx.author} tarafından yeniden adlandırma")
        await ctx.send(f"Kanal **#{new_name}** olarak adlandırıldı.", delete_after=5)
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Kanal adı değiştirilemedi: {type(exc).__name__}.")


@bot.command(name="sabitle", aliases=["pin"])
@commands.guild_only()
@commands.has_guild_permissions(manage_messages=True)
@commands.bot_has_guild_permissions(manage_messages=True)
async def sabitle(ctx, mesaj_id: int = None):
    if mesaj_id is None:
        await ctx.send("Kullanım: `B!sabitle <mesaj_id>`")
        return
    msg = await _fetch_message_or_warn(ctx, mesaj_id)
    if msg is None:
        return
    try:
        await msg.pin(reason=f"{ctx.author} tarafından sabitlendi")
        await ctx.send("Mesaj sabitlendi.", delete_after=5)
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Mesaj sabitlenemedi: {type(exc).__name__}.")


@bot.command(name="sabitikaldir", aliases=["unpin"])
@commands.guild_only()
@commands.has_guild_permissions(manage_messages=True)
@commands.bot_has_guild_permissions(manage_messages=True)
async def sabit_kaldir(ctx, mesaj_id: int = None):
    if mesaj_id is None:
        await ctx.send("Kullanım: `B!sabitikaldir <mesaj_id>`")
        return
    msg = await _fetch_message_or_warn(ctx, mesaj_id)
    if msg is None:
        return
    try:
        await msg.unpin(reason=f"{ctx.author} tarafından sabiti kaldırıldı")
        await ctx.send("Sabit kaldırıldı.", delete_after=5)
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Sabit kaldırılamadı: {type(exc).__name__}.")


@bot.command(name="mesajsil", aliases=["msgsil"])
@commands.cooldown(1, 15, commands.BucketType.guild)
@commands.guild_only()
@commands.has_guild_permissions(manage_messages=True)
@commands.bot_has_guild_permissions(manage_messages=True)
async def mesaj_sil(ctx, mesaj_id: int = None):
    if mesaj_id is None:
        await ctx.send("Kullanım: `B!mesajsil <mesaj_id>` (tek bir mesajı siler)")
        return
    msg = await _fetch_message_or_warn(ctx, mesaj_id)
    if msg is None:
        return
    try:
        await msg.delete(reason=f"{ctx.author} tarafından silindi")
        await ctx.send("Mesaj silindi.", delete_after=5)
        await send_security_log(ctx.guild, f"Tek mesaj silindi: #{ctx.channel} · Yetkili: {ctx.author} · Mesaj ID: {mesaj_id}")
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Mesaj silinemedi: {type(exc).__name__}.")


# -------------------- 3.6.4: 40 EK KOMUT (20 YETKILI, 20 EGLENCE) --------------------
PERM_CHECK_LABELS = {
    "administrator": "Yönetici", "manage_guild": "Sunucuyu yönet", "manage_channels": "Kanalları yönet",
    "manage_roles": "Rolleri yönet", "manage_messages": "Mesajları yönet", "kick_members": "Üyeleri at",
    "ban_members": "Üyeleri yasakla", "mention_everyone": "@everyone kullan", "send_messages": "Mesaj gönder",
    "view_channel": "Kanalı gör",
}
BOT_NEEDED_PERMS = [
    ("manage_channels", "Kanalları yönet"), ("manage_roles", "Rolleri yönet"),
    ("manage_messages", "Mesajları yönet"), ("ban_members", "Üyeleri yasakla"),
    ("kick_members", "Üyeleri at"), ("manage_nicknames", "Takma adları yönet"),
    ("moderate_members", "Üyeleri sustur"), ("send_messages", "Mesaj gönder"), ("embed_links", "Bağlantı göm"),
]
HUG_TEXTS = {
    "kahve": "☕ {author} {target} için kahve söyledi.",
    "cay": "🍵 {author} {target} için çay demledi.",
    "selam": "👋 {author} {target} selam söyledi!",
    "dans": "💃 {author} {target} ile dans ediyor!",
    "kucakla": "🤗 {author} {target} kucakladı.",
    "pat": "🫳 {author} {target} kafasını okşadı.",
}
STORY_STARTS = [
    "Gece yarısı sunucuya tanımadığı bir bot girdi ve her kanala aynı mesajı yazdı...",
    "Son kullanıcı sessizce çıktığında, kanaldaki herkes aynı anda fark etti.",
    "Harita açıldı, ama üzerindeki işaret sadece bir kişinin bildiği bir yere çıkıyordu.",
]
PROVERBS = [
    "Damlaya damlaya göl olur.", "Acele işe şeytan karışır.", "Ağaç yaşken eğilir.",
    "Emeğin olmayınca, ekmeğin de olmaz.", "Sabreden derviş muradına ermiş.",
]
FUN_FACTS = [
    "Ahtapotların üç kalbi vardır.", "Bal, bozulmadan binlerce yıl saklanabilir.",
    "Bir bulut ortalama yüzlerce ton su taşıyabilir.", "Kelebekler ayaklarıyla tat alır.",
    "Dünyadaki en eski yazılı dil çivi yazısıdır.",
]
MOODS = ["😌 Sakin", "😂 Gülme krizinde", "🥱 Uykulu", "🔥 Enerjik", "🤔 Düşünceli", "😎 Havalı", "🫠 Eriyor"]
HOROSCOPE_LINES = [
    "Bugün beklenmedik bir sürpriz seni bekliyor.", "Sabırlı ol, iyi haber geç ama kesin gelecek.",
    "Bir arkadaşın sana yardım etmek için yer açacak.", "Bugün kararlarında biraz daha cesur ol.",
    "Küçük bir kayıp, büyük bir kazanca dönüşebilir.",
]
ZODIAC = {
    "koc": "Koç", "boga": "Boğa", "ikizler": "İkizler", "yengec": "Yengeç", "aslan": "Aslan",
    "basak": "Başak", "terazi": "Terazi", "akrep": "Akrep", "yay": "Yay", "oglak": "Oğlak",
    "kova": "Kova", "balik": "Balık",
}
ICEBREAKERS = [
    "Bu hafta oynadığın en iyi oyun hangisi?", "Sence en iyi pizza malzemesi ne?",
    "Yaz mı kış mı, hangisini seçerdin?", "Son izlediğin dizi veya film ne?",
    "Hayatında bir gün tekrar yaşayabilseydin hangi gün olurdu?",
]
GAME_SUGGESTIONS = [
    "Stardew Valley", "Hades", "Celeste", "Hollow Knight", "Portal 2",
    "Terraria", "Rocket League", "Among Us", "Minecraft", "Hollow Knight: Silksong",
]
MORNING_TEXT = "☀️ Günaydın {mention}!"


def _switch_value(text):
    value = normalize_economy_key(text or "")
    if value in ("ac", "acik", "on", "evet", "yes", "aktif"):
        return True
    if value in ("kapat", "kapa", "kapali", "off", "hayir", "pasif"):
        return False
    return None


def _role_too_high(ctx, role):
    return role >= ctx.guild.me.top_role or role.is_default()


def _member_too_high(ctx, member):
    return member.top_role >= ctx.guild.me.top_role or member == ctx.guild.owner or member.bot


async def _react_text(ctx, uye, template):
    target = uye.mention if uye else "herkese"
    await ctx.send(template.format(author=ctx.author.mention, target=target))


# ---------- YETKILI KOMUTLAR ----------
@bot.command(name="nsfw")
@commands.guild_only()
@commands.has_guild_permissions(manage_channels=True)
@commands.bot_has_guild_permissions(manage_channels=True)
async def nsfw_command(ctx, durum: str = None):
    flag = _switch_value(durum)
    if flag is None or not isinstance(ctx.channel, discord.TextChannel):
        await ctx.send("Kullanım: `B!nsfw <aç|kapat>` (sadece metin kanallarında)")
        return
    try:
        await ctx.channel.edit(nsfw=flag, reason=f"{ctx.author} tarafından NSFW ayarı")
        await ctx.send(f"Bu kanal için NSFW **{'açıldı' if flag else 'kapatıldı'}**.", delete_after=5)
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"NSFW ayarı değiştirilemedi: {type(exc).__name__}.")


@bot.command(name="kanalgizle")
@commands.guild_only()
@commands.has_guild_permissions(manage_channels=True)
@commands.bot_has_guild_permissions(manage_channels=True)
async def kanal_gizle(ctx):
    try:
        await ctx.channel.set_permissions(ctx.guild.default_role, view_channel=False, reason=f"{ctx.author} kanalı gizledi")
        await ctx.send("Kanal herkesten gizlendi. Geri açmak için `B!kanalgoster`.")
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Kanal gizlenemedi: {type(exc).__name__}.")


@bot.command(name="kanalgoster")
@commands.guild_only()
@commands.has_guild_permissions(manage_channels=True)
@commands.bot_has_guild_permissions(manage_channels=True)
async def kanal_goster(ctx):
    try:
        await ctx.channel.set_permissions(ctx.guild.default_role, view_channel=None, reason=f"{ctx.author} kanalı gösterdi")
        await ctx.send("Kanal herkes için tekrar görünür.")
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Kanal gösterilemedi: {type(exc).__name__}.")


@bot.command(name="rolrenk")
@commands.guild_only()
@commands.has_guild_permissions(manage_roles=True)
@commands.bot_has_guild_permissions(manage_roles=True)
async def rol_renk(ctx, rol: discord.Role = None, renk: str = None):
    hex_text = (renk or "").strip().lstrip("#")
    if rol is None or len(hex_text) != 6:
        await ctx.send("Kullanım: `B!rolrenk @rol #ff0000`")
        return
    if _role_too_high(ctx, rol):
        await ctx.send("Bu rolü düzenlemek için botun rolü daha üstte olmalı.")
        return
    try:
        value = int(hex_text, 16)
    except ValueError:
        await ctx.send("Geçersiz renk kodu. Örnek: `#ff0000`")
        return
    try:
        await rol.edit(color=discord.Color(value), reason=f"{ctx.author} tarafından renk değişikliği")
        await ctx.send(f"**{rol.name}** rolünün rengi `#{value:06X}` oldu.")
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Rol rengi değiştirilemedi: {type(exc).__name__}.")


@bot.command(name="rolyenidenadlandir", aliases=["roladi"])
@commands.guild_only()
@commands.has_guild_permissions(manage_roles=True)
@commands.bot_has_guild_permissions(manage_roles=True)
async def rol_yeniden_adlandir(ctx, rol: discord.Role = None, *, isim: str = None):
    if rol is None or not isim:
        await ctx.send("Kullanım: `B!rolyenidenadlandir @rol <yeni isim>`")
        return
    if _role_too_high(ctx, rol):
        await ctx.send("Bu rolü düzenlemek için botun rolü daha üstte olmalı.")
        return
    try:
        old = rol.name
        await rol.edit(name=isim[:100], reason=f"{ctx.author} tarafından yeniden adlandırma")
        await ctx.send(f"**{old}** rolü **{isim[:100]}** olarak değiştirildi.", allowed_mentions=discord.AllowedMentions.none())
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Rol adı değiştirilemedi: {type(exc).__name__}.")


@bot.command(name="yasaklilar", aliases=["banlist"])
@commands.guild_only()
@commands.has_guild_permissions(ban_members=True)
async def yasaklilar(ctx):
    lines = []
    try:
        async for entry in ctx.guild.bans(limit=20):
            lines.append(f"{entry.user} · `{entry.user.id}`")
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Yasak listesi alınamadı: {type(exc).__name__}.")
        return
    text = "\n".join(lines) if lines else "Yasaklı kullanıcı yok."
    embed = discord.Embed(title="Yasaklı kullanıcılar (ilk 20)", description=text[:4000], color=discord.Color.red())
    await ctx.send(embed=embed)


@bot.command(name="davetler", aliases=["invites"])
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def davetler(ctx):
    try:
        invites = await ctx.guild.invites()
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Davetler alınamadı: {type(exc).__name__}.")
        return
    if not invites:
        await ctx.send("Aktif davet yok.")
        return
    lines = [
        f"`{inv.code}` · {inv.uses or 0} kullanım · oluşturan: {inv.inviter.display_name if inv.inviter else 'bilinmiyor'}"
        for inv in invites[:20]
    ]
    embed = discord.Embed(title="Aktif davetler", description="\n".join(lines)[:4000], color=discord.Color.blurple())
    await ctx.send(embed=embed)


@bot.command(name="davetsil", aliases=["davetiptal"])
@commands.cooldown(1, 15, commands.BucketType.guild)
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
@commands.bot_has_guild_permissions(manage_guild=True)
async def davet_sil(ctx, kod: str = None):
    if not kod:
        await ctx.send("Kullanım: `B!davetsil <davet_kodu>` (kodları `B!davetler` ile görürsün)")
        return
    try:
        invites = await ctx.guild.invites()
        target = next((inv for inv in invites if inv.code == kod.strip()), None)
        if target is None:
            await ctx.send("Bu koda ait aktif davet bulunamadı.")
            return
        await target.delete(reason=f"{ctx.author} tarafından silindi")
        await ctx.send(f"`{target.code}` davet kodu silindi.")
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Davet silinemedi: {type(exc).__name__}.")


@bot.command(name="kanallistesi", aliases=["kanallar2"])
@commands.guild_only()
@commands.has_guild_permissions(manage_channels=True)
async def kanal_listesi(ctx):
    lines = []
    for ch in ctx.guild.channels:
        if isinstance(ch, discord.CategoryChannel):
            continue
        prefix = "🔊" if isinstance(ch, discord.VoiceChannel) else "#"
        lines.append(f"{prefix}{ch.name} · `{ch.id}`")
    text = "\n".join(lines[:40]) or "Kanal yok."
    if len(lines) > 40:
        text += f"\n... ve {len(lines) - 40} kanal daha"
    await ctx.send(embed=discord.Embed(title=f"Kanallar ({len(lines)})", description=text[:4000], color=discord.Color.blurple()))


@bot.command(name="rollistesi", aliases=["roller2"])
@commands.guild_only()
@commands.has_guild_permissions(manage_roles=True)
async def rol_listesi(ctx):
    roles = [r for r in reversed(ctx.guild.roles) if not r.is_default()]
    lines = [f"{r.mention} · `{r.id}`" for r in roles[:40]]
    text = "\n".join(lines) or "Rol yok."
    if len(roles) > 40:
        text += f"\n... ve {len(roles) - 40} rol daha"
    await ctx.send(embed=discord.Embed(title=f"Roller ({len(roles)})", description=text[:4000], color=discord.Color.blurple()), allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="izinler", aliases=["perms"])
@commands.guild_only()
async def izinler(ctx, uye: discord.Member = None):
    uye = uye or ctx.author
    perms = ctx.channel.permissions_for(uye)
    lines = [f"{'✅' if getattr(perms, key) else '❌'} {label}" for key, label in PERM_CHECK_LABELS.items()]
    embed = discord.Embed(title=f"{uye.display_name} · bu kanaldaki izinler", description="\n".join(lines), color=discord.Color.blurple())
    await ctx.send(embed=embed)


@bot.command(name="botyetkisi", aliases=["botizin"])
@commands.guild_only()
async def bot_yetkisi(ctx):
    perms = ctx.channel.permissions_for(ctx.guild.me)
    lines = [f"{'✅' if getattr(perms, key) else '❌'} {label}" for key, label in BOT_NEEDED_PERMS]
    missing = sum(1 for key, _ in BOT_NEEDED_PERMS if not getattr(perms, key))
    footer = "Her şey hazır." if not missing else f"{missing} izin eksik. Botun rolünü kontrol et."
    embed = discord.Embed(title="Botun bu kanaldaki izinleri", description="\n".join(lines), color=discord.Color.teal())
    embed.set_footer(text=footer)
    await ctx.send(embed=embed)


@bot.command(name="kanalsira", aliases=["kanalpozisyon"])
@commands.guild_only()
@commands.has_guild_permissions(manage_channels=True)
@commands.bot_has_guild_permissions(manage_channels=True)
async def kanal_sira(ctx, pozisyon: int = None):
    if pozisyon is None or pozisyon < 0:
        await ctx.send("Kullanım: `B!kanalsira <pozisyon>` (0 en üst)")
        return
    try:
        await ctx.channel.edit(position=pozisyon, reason=f"{ctx.author} tarafından sıra değişikliği")
        await ctx.send(f"Kanal sırası **{pozisyon}** olarak ayarlandı.", delete_after=5)
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Kanal sırası değiştirilemedi: {type(exc).__name__}.")


@bot.command(name="kategoriolustur", aliases=["kategorioluştur", "kategoriekle"])
@commands.cooldown(1, 15, commands.BucketType.guild)
@commands.guild_only()
@commands.has_guild_permissions(manage_channels=True)
@commands.bot_has_guild_permissions(manage_channels=True)
async def kategori_olustur(ctx, *, isim: str = None):
    if not isim:
        await ctx.send("Kullanım: `B!kategoriolustur <isim>`")
        return
    try:
        category = await ctx.guild.create_category(name=isim[:100], reason=f"{ctx.author} tarafından oluşturuldu")
        await ctx.send(f"Kategori **{category.name}** oluşturuldu.", allowed_mentions=discord.AllowedMentions.none())
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Kategori oluşturulamadı: {type(exc).__name__}.")


@bot.command(name="emojiler", aliases=["sunucuemoji"])
@commands.guild_only()
async def emojiler(ctx):
    emojis = ctx.guild.emojis
    if not emojis:
        await ctx.send("Bu sunucuda özel emoji yok.")
        return
    text = " ".join(str(e) for e in emojis[:60])
    if len(emojis) > 60:
        text += f"\n... ve {len(emojis) - 60} emoji daha"
    await ctx.send(embed=discord.Embed(title=f"Sunucu emojileri ({len(emojis)})", description=text[:4000], color=discord.Color.gold()))


@bot.command(name="adminler", aliases=["yetkililer"])
@commands.guild_only()
async def adminler(ctx):
    admins = [m for m in ctx.guild.members if not m.bot and m.guild_permissions.administrator]
    lines = [m.display_name for m in admins[:30]]
    text = "\n".join(lines) or "Yönetici yetkisine sahip üye yok."
    if len(admins) > 30:
        text += f"\n... ve {len(admins) - 30} yönetici daha"
    await ctx.send(embed=discord.Embed(title="Yönetici yetkisi olan üyeler", description=text[:4000], color=discord.Color.orange()))


@bot.command(name="hoist")
@commands.guild_only()
@commands.has_guild_permissions(manage_roles=True)
@commands.bot_has_guild_permissions(manage_roles=True)
async def hoist_command(ctx, rol: discord.Role = None, durum: str = None):
    flag = _switch_value(durum)
    if rol is None or flag is None:
        await ctx.send("Kullanım: `B!hoist @rol <aç|kapat>` (rolün üye listesinde ayrı gösterilmesi)")
        return
    if _role_too_high(ctx, rol):
        await ctx.send("Bu rolü düzenlemek için botun rolü daha üstte olmalı.")
        return
    try:
        await rol.edit(hoist=flag, reason=f"{ctx.author} tarafından ayrı gösterim ayarı")
        await ctx.send(f"**{rol.name}** için ayrı gösterim **{'açıldı' if flag else 'kapatıldı'}**.", allowed_mentions=discord.AllowedMentions.none())
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Ayar değiştirilemedi: {type(exc).__name__}.")


@bot.command(name="mentionable")
@commands.guild_only()
@commands.has_guild_permissions(manage_roles=True)
@commands.bot_has_guild_permissions(manage_roles=True)
async def mentionable_command(ctx, rol: discord.Role = None, durum: str = None):
    flag = _switch_value(durum)
    if rol is None or flag is None:
        await ctx.send("Kullanım: `B!mentionable @rol <aç|kapat>`")
        return
    if _role_too_high(ctx, rol):
        await ctx.send("Bu rolü düzenlemek için botun rolü daha üstte olmalı.")
        return
    try:
        await rol.edit(mentionable=flag, reason=f"{ctx.author} tarafından etiketlenebilirlik ayarı")
        await ctx.send(f"**{rol.name}** etiketlenebilirliği **{'açıldı' if flag else 'kapatıldı'}**.", allowed_mentions=discord.AllowedMentions.none())
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Ayar değiştirilemedi: {type(exc).__name__}.")


@bot.command(name="yazmaizni")
@commands.guild_only()
@commands.has_guild_permissions(manage_channels=True)
@commands.bot_has_guild_permissions(manage_channels=True)
async def yazma_izni(ctx, uye: discord.Member = None, durum: str = None):
    value = _switch_value(durum)
    if uye is None or durum is None or durum.strip() == "" or (value is None and durum not in ("sifirla", "sıfırla")):
        await ctx.send("Kullanım: `B!yazmaizni @üye <aç|kapat|sıfırla>` (bu kanalda)")
        return
    if _member_too_high(ctx, uye):
        await ctx.send("Bu üye için izin değiştirilemez (rolü botun üstünde veya bot/sahip).")
        return
    new_value = False if value is False else None if value is True or value is None else None
    try:
        await ctx.channel.set_permissions(uye, send_messages=new_value, reason=f"{ctx.author} tarafından yazma izni")
        label = "kapatıldı" if new_value is False else "varsayılana döndü"
        await ctx.send(f"{uye.display_name} için bu kanalda yazma izni **{label}**.", allowed_mentions=discord.AllowedMentions.none())
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"İzin değiştirilemedi: {type(exc).__name__}.")


@bot.command(name="gorusizni")
@commands.guild_only()
@commands.has_guild_permissions(manage_channels=True)
@commands.bot_has_guild_permissions(manage_channels=True)
async def gorus_izni(ctx, uye: discord.Member = None, durum: str = None):
    value = _switch_value(durum)
    if uye is None or value is None and durum not in ("sifirla", "sıfırla"):
        await ctx.send("Kullanım: `B!gorusizni @üye <aç|kapat|sıfırla>` (bu kanalı görme izni)")
        return
    if _member_too_high(ctx, uye):
        await ctx.send("Bu üye için izin değiştirilemez (rolü botun üstünde veya bot/sahip).")
        return
    new_value = False if value is False else None
    try:
        await ctx.channel.set_permissions(uye, view_channel=new_value, reason=f"{ctx.author} tarafından görme izni")
        label = "kapatıldı" if new_value is False else "varsayılana döndü"
        await ctx.send(f"{uye.display_name} için bu kanalı görme izni **{label}**.", allowed_mentions=discord.AllowedMentions.none())
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"İzin değiştirilemedi: {type(exc).__name__}.")


# ---------- EGLENCE KOMUTLARI ----------
@bot.command(name="hikaye", aliases=["story"])
async def hikaye(ctx):
    await ctx.send(f"📖 {random.choice(STORY_STARTS)}")


@bot.command(name="atasozu", aliases=["atasözü"])
async def atasozu(ctx):
    await ctx.send(f"📜 {random.choice(PROVERBS)}")


@bot.command(name="bilgi", aliases=["funfact"])
async def bilgi(ctx):
    await ctx.send(f"🧠 {random.choice(FUN_FACTS)}")


@bot.command(name="slogan")
@commands.guild_only()
async def slogan(ctx):
    words = ["Oyun", "Paklava", "Dostluk", "Kahkaha", "Efsane", "Rekabet"]
    random.Random(f"{ctx.guild.id}-{time.strftime('%Y-%m-%d')}").shuffle(words)
    await ctx.send(f"📣 **{ctx.guild.name}** için slogan: *{words[0]} burada başlar!*", allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="kahve")
async def kahve(ctx, uye: discord.Member = None):
    await _react_text(ctx, uye, HUG_TEXTS["kahve"])


@bot.command(name="cay", aliases=["çay"])
async def cay(ctx, uye: discord.Member = None):
    await _react_text(ctx, uye, HUG_TEXTS["cay"])


@bot.command(name="selam")
async def selam(ctx, uye: discord.Member = None):
    await _react_text(ctx, uye, HUG_TEXTS["selam"])


@bot.command(name="dans")
async def dans(ctx, uye: discord.Member = None):
    await _react_text(ctx, uye, HUG_TEXTS["dans"])


@bot.command(name="kucakla")
async def kucakla(ctx, uye: discord.Member = None):
    if uye is None or uye.id == ctx.author.id:
        await ctx.send("Kullanım: `B!kucakla @üye` (kendini kucaklayamazsın, birini seç)")
        return
    await _react_text(ctx, uye, HUG_TEXTS["kucakla"])


@bot.command(name="pat")
async def pat(ctx, uye: discord.Member = None):
    await _react_text(ctx, uye, HUG_TEXTS["pat"])


@bot.command(name="ruhhali", aliases=["ruhhal"])
async def ruh_hali(ctx, uye: discord.Member = None):
    uye = uye or ctx.author
    mood = random.Random(f"{uye.id}-{time.strftime('%Y-%m-%d')}").choice(MOODS)
    await ctx.send(f"{uye.display_name} bugün: **{mood}**", allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="burc", aliases=["burç", "horoskop"])
async def burc(ctx, *, isim: str = None):
    if not isim:
        await ctx.send("Kullanım: `B!burc <burç>` örnek: `B!burc aslan`")
        return
    key = normalize_economy_key(isim)
    if key not in ZODIAC:
        await ctx.send("Bu burç bulunamadı. Örnek: koç, boğa, ikizler, aslan, terazi, balık.")
        return
    line = random.Random(f"{key}-{time.strftime('%Y-%m-%d')}").choice(HOROSCOPE_LINES)
    await ctx.send(f"♈ **{ZODIAC[key]}** günlük yorum: {line}")


@bot.command(name="puanla", aliases=["rate"])
async def puanla(ctx, *, sey: str = None):
    if not sey:
        await ctx.send("Kullanım: `B!puanla <şey>`")
        return
    score = random.randint(0, 10)
    await ctx.send(f"⭐ **{sey[:100]}** için puanım: **{score}/10**", allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="iq")
async def iq_command(ctx, uye: discord.Member = None):
    uye = uye or ctx.author
    score = random.Random(f"iq-{uye.id}").randint(70, 150)
    await ctx.send(f"🧠 {uye.display_name} için tamamen bilimsel IQ tahmini: **{score}**", allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="muhabbet", aliases=["sohbetbaslat"])
async def muhabbet(ctx):
    await ctx.send(f"💬 {random.choice(ICEBREAKERS)}")


@bot.command(name="balon")
async def balon(ctx):
    if random.random() < 0.3:
        await ctx.send("💥 Balon patladı!")
    else:
        await ctx.send("🎈 Balon şişti, dikkatli ol!")


@bot.command(name="oyunoner", aliases=["oyunönerisi"])
async def oyun_oner(ctx):
    await ctx.send(f"🎮 Bugün şunu dene: **{random.choice(GAME_SUGGESTIONS)}**")


@bot.command(name="gunaydin", aliases=["günaydın"])
async def gunaydin(ctx):
    hour = _datetime.now(TR_TZ).hour
    if 5 <= hour < 12:
        text = "Günaydın"
    elif 12 <= hour < 17:
        text = "İyi günler"
    elif 17 <= hour < 22:
        text = "İyi akşamlar"
    else:
        text = "İyi geceler"
    await ctx.send(f"☀️ {text} {ctx.author.mention}!", allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="iyigeceler", aliases=["iyigece"])
async def iyigeceler(ctx):
    await ctx.send(f"🌙 İyi geceler {ctx.author.mention}, yarın görüşmek üzere.", allowed_mentions=discord.AllowedMentions.none())


# -------------------- 3.6.4: 20 EK YETKILI KOMUT --------------------
@bot.command(name="kanalsil")
@commands.cooldown(1, 15, commands.BucketType.guild)
@commands.guild_only()
@commands.has_guild_permissions(manage_channels=True)
@commands.bot_has_guild_permissions(manage_channels=True)
async def kanal_sil(ctx, kanal: discord.abc.GuildChannel = None, onay: str = None):
    if kanal is None or normalize_economy_key(onay or "") != "onayla":
        await ctx.send("Kullanım: `B!kanalsil #kanal onayla` · bu işlem geri alınamaz.")
        return
    try:
        await kanal.delete(reason=f"{ctx.author} tarafından silindi")
        await ctx.send(f"**{kanal.name}** kanalı silindi.", allowed_mentions=discord.AllowedMentions.none())
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Kanal silinemedi: {type(exc).__name__}.")


@bot.command(name="rolsil")
@commands.cooldown(1, 15, commands.BucketType.guild)
@commands.guild_only()
@commands.has_guild_permissions(manage_roles=True)
@commands.bot_has_guild_permissions(manage_roles=True)
async def rol_sil(ctx, rol: discord.Role = None, onay: str = None):
    if rol is None or normalize_economy_key(onay or "") != "onayla":
        await ctx.send("Kullanım: `B!rolsil @rol onayla` · bu işlem geri alınamaz.")
        return
    if _role_too_high(ctx, rol):
        await ctx.send("Bu rolü silmek için botun rolü daha üstte olmalı.")
        return
    try:
        name = rol.name
        await rol.delete(reason=f"{ctx.author} tarafından silindi")
        await ctx.send(f"**{name}** rolü silindi.", allowed_mentions=discord.AllowedMentions.none())
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Rol silinemedi: {type(exc).__name__}.")


@bot.command(name="mesajlar", aliases=["kullanicimesajlari"])
@commands.cooldown(1, 15, commands.BucketType.guild)
@commands.guild_only()
@commands.has_guild_permissions(manage_messages=True)
@commands.bot_has_guild_permissions(manage_messages=True, read_message_history=True)
async def kullanici_mesajlari_sil(ctx, uye: discord.Member = None, sayi: int = None):
    if uye is None or sayi is None or not 1 <= sayi <= 100:
        await ctx.send("Kullanım: `B!mesajlar @üye <1-100>` · bu kanalda son 100 mesaj içinde arar.")
        return
    try:
        count = 0

        def is_target(m):
            nonlocal count
            if count < sayi and m.author.id == uye.id:
                count += 1
                return True
            return False

        deleted = await ctx.channel.purge(limit=100, check=is_target, reason=f"{ctx.author} tarafından üye mesajları")
        removed = len(deleted)
        await ctx.send(f"{uye.display_name} kullanıcısının **{removed}** mesajı silindi.", delete_after=5)
        await send_security_log(ctx.guild, f"Üye mesajları silindi: {uye} · #{ctx.channel} · {removed} mesaj · Yetkili: {ctx.author}")
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Mesajlar silinemedi: {type(exc).__name__}.")


@bot.command(name="uyeara", aliases=["uyebul"])
@commands.guild_only()
async def uye_ara(ctx, *, isim: str = None):
    if not isim:
        await ctx.send("Kullanım: `B!uyeara <isim>`")
        return
    key = isim.strip().lower()
    matches = [m for m in ctx.guild.members if key in m.name.lower() or key in m.display_name.lower()]
    lines = [f"{m.display_name} · {m} · `{m.id}`" for m in matches[:20]]
    text = "\n".join(lines) or "Eşleşen üye yok."
    if len(matches) > 20:
        text += f"\n... ve {len(matches) - 20} üye daha"
    await ctx.send(embed=discord.Embed(title="Üye arama sonuçları", description=text[:4000], color=discord.Color.blurple()), allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="sesat", aliases=["sesdisconnect"])
@commands.cooldown(1, 15, commands.BucketType.guild)
@commands.guild_only()
@commands.has_guild_permissions(move_members=True)
@commands.bot_has_guild_permissions(move_members=True)
async def ses_at(ctx, uye: discord.Member = None):
    if uye is None or uye.voice is None:
        await ctx.send("Kullanım: `B!sesat @üye` (üye bir ses kanalında olmalı)")
        return
    if _member_too_high(ctx, uye):
        await ctx.send("Bu üyeyi ses kanalından çıkaramam (rolü botun üstünde veya bot/sahip).")
        return
    try:
        await uye.move_to(None, reason=f"{ctx.author} tarafından ses kanalından çıkarıldı")
        await ctx.send(f"{uye.display_name} ses kanalından çıkarıldı.", allowed_mentions=discord.AllowedMentions.none())
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Üye çıkarılamadı: {type(exc).__name__}.")


@bot.command(name="sesgetir", aliases=["sestecek"])
@commands.guild_only()
@commands.has_guild_permissions(move_members=True)
@commands.bot_has_guild_permissions(move_members=True)
async def ses_getir(ctx, uye: discord.Member = None, kanal: discord.VoiceChannel = None):
    if uye is None or kanal is None or uye.voice is None:
        await ctx.send("Kullanım: `B!sesgetir @üye #ses-kanalı` (üye bir ses kanalında olmalı)")
        return
    if _member_too_high(ctx, uye):
        await ctx.send("Bu üyeyi taşıyamam (rolü botun üstünde veya bot/sahip).")
        return
    try:
        await uye.move_to(kanal, reason=f"{ctx.author} tarafından taşındı")
        await ctx.send(f"{uye.display_name} **{kanal.name}** kanalına taşındı.", allowed_mentions=discord.AllowedMentions.none())
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Üye taşınamadı: {type(exc).__name__}.")


@bot.command(name="sesgizle", aliases=["sesmute"])
@commands.guild_only()
@commands.has_guild_permissions(mute_members=True)
@commands.bot_has_guild_permissions(mute_members=True)
async def ses_gizle(ctx, uye: discord.Member = None, durum: str = None):
    flag = _switch_value(durum)
    if uye is None or flag is None:
        await ctx.send("Kullanım: `B!sesgizle @üye <aç|kapat>` (aç = sesi kapat)")
        return
    if _member_too_high(ctx, uye):
        await ctx.send("Bu üyeye ses komutu uygulanamaz (rolü botun üstünde veya bot/sahip).")
        return
    try:
        await uye.edit(mute=flag, reason=f"{ctx.author} tarafından ses ayarı")
        await ctx.send(f"{uye.display_name} için sunucu sesi **{'kapatıldı' if flag else 'açıldı'}**.", allowed_mentions=discord.AllowedMentions.none())
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Ses ayarı değiştirilemedi: {type(exc).__name__}.")


@bot.command(name="sunucuadi", aliases=["sunucuisim"])
@commands.cooldown(1, 15, commands.BucketType.guild)
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
@commands.bot_has_guild_permissions(manage_guild=True)
async def sunucu_adi(ctx, *, isim: str = None):
    if not isim:
        await ctx.send("Kullanım: `B!sunucuadi <yeni isim>`")
        return
    try:
        await ctx.guild.edit(name=isim[:100], reason=f"{ctx.author} tarafından sunucu adı değişikliği")
        await ctx.send(f"Sunucu adı **{isim[:100]}** olarak değiştirildi.", allowed_mentions=discord.AllowedMentions.none())
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.send(f"Sunucu adı değiştirilemedi: {type(exc).__name__}.")


@bot.command(name="rolunuyeleri", aliases=["rolyeleri"])
@commands.guild_only()
@commands.has_guild_permissions(manage_roles=True)
async def rol_uyeleri(ctx, rol: discord.Role = None):
    if rol is None:
        await ctx.send("Kullanım: `B!rolunuyeleri @rol`")
        return
    members = rol.members
    lines = [m.display_name for m in members[:40]]
    text = "\n".join(lines) or "Bu role sahip üye yok."
    if len(members) > 40:
        text += f"\n... ve {len(members) - 40} üye daha"
    await ctx.send(embed=discord.Embed(title=f"{rol.name} rolü ({len(members)} üye)", description=text[:4000], color=discord.Color.blurple()), allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="bosroller")
@commands.guild_only()
@commands.has_guild_permissions(manage_roles=True)
async def bos_roller(ctx):
    empty = [r.name for r in ctx.guild.roles if not r.is_default() and not r.members]
    text = "\n".join(empty[:40]) or "Boş rol yok."
    if len(empty) > 40:
        text += f"\n... ve {len(empty) - 40} rol daha"
    await ctx.send(embed=discord.Embed(title=f"Üyesi olmayan roller ({len(empty)})", description=text[:4000], color=discord.Color.orange()), allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="seskanallar", aliases=["sesdurumu"])
@commands.guild_only()
async def ses_kanallari(ctx):
    lines = []
    for ch in ctx.guild.voice_channels:
        names = [m.display_name for m in ch.members]
        if names:
            lines.append(f"🔊 **{ch.name}** ({len(names)}): {', '.join(names[:10])}")
    text = "\n".join(lines) or "Şu anda hiçbir ses kanalında kimse yok."
    await ctx.send(embed=discord.Embed(title="Aktif ses kanalları", description=text[:4000], color=discord.Color.green()), allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="hesapyasi", aliases=["hesapbilgi"])
@commands.guild_only()
@commands.has_guild_permissions(manage_messages=True)
async def hesap_yasi(ctx, uye: discord.Member = None):
    uye = uye or ctx.author
    age_days = (_datetime.now(uye.created_at.tzinfo) - uye.created_at).days
    warning = "⚠️ Hesap çok yeni." if age_days < 7 else "Hesap yeterince eski."
    embed = discord.Embed(title=f"{uye.display_name} · hesap yaşı", color=discord.Color.blurple())
    embed.add_field(name="Hesap yaşı", value=f"{age_days} gün", inline=True)
    embed.add_field(name="Hesap açılış", value=discord.utils.format_dt(uye.created_at, "D"), inline=True)
    embed.set_footer(text=warning)
    await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="yenikatilan", aliases=["yenigelenler"])
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def yeni_katilan(ctx, gun: int = 7):
    if not 1 <= gun <= 30:
        await ctx.send("Gün sayısı 1 ile 30 arasında olmalı.")
        return
    now = _datetime.now(TR_TZ)
    recent = [m for m in ctx.guild.members if m.joined_at and (now - m.joined_at).days < gun and not m.bot]
    recent.sort(key=lambda m: m.joined_at, reverse=True)
    lines = [f"{m.display_name} · {discord.utils.format_dt(m.joined_at, 'R')}" for m in recent[:25]]
    text = "\n".join(lines) or "Bu süre içinde yeni üye yok."
    await ctx.send(embed=discord.Embed(title=f"Son {gun} günde katılanlar ({len(recent)})", description=text[:4000], color=discord.Color.green()), allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="rolsuzuyeler", aliases=["rolsuz"])
@commands.guild_only()
@commands.has_guild_permissions(manage_roles=True)
async def rolsuz_uyeler(ctx):
    members = [m for m in ctx.guild.members if not m.bot and len(m.roles) <= 1]
    lines = [m.display_name for m in members[:40]]
    text = "\n".join(lines) or "Rolsüz üye yok."
    if len(members) > 40:
        text += f"\n... ve {len(members) - 40} üye daha"
    await ctx.send(embed=discord.Embed(title=f"Rolsüz üyeler ({len(members)})", description=text[:4000], color=discord.Color.orange()), allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="rolbul", aliases=["rolara"])
@commands.guild_only()
async def rol_bul(ctx, *, isim: str = None):
    if not isim:
        await ctx.send("Kullanım: `B!rolbul <isim>`")
        return
    key = isim.strip().lower()
    matches = [r for r in ctx.guild.roles if not r.is_default() and key in r.name.lower()]
    lines = [f"{r.mention} · `{r.id}`" for r in matches[:20]]
    text = "\n".join(lines) or "Eşleşen rol yok."
    await ctx.send(embed=discord.Embed(title="Rol arama sonuçları", description=text[:4000], color=discord.Color.blurple()), allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="kanalbul", aliases=["kanalara"])
@commands.guild_only()
async def kanal_bul(ctx, *, isim: str = None):
    if not isim:
        await ctx.send("Kullanım: `B!kanalbul <isim>`")
        return
    key = isim.strip().lower()
    matches = [c for c in ctx.guild.channels if key in c.name.lower()]
    lines = [f"{c.name} · `{c.id}`" for c in matches[:20]]
    text = "\n".join(lines) or "Eşleşen kanal yok."
    await ctx.send(embed=discord.Embed(title="Kanal arama sonuçları", description=text[:4000], color=discord.Color.blurple()))


@bot.command(name="susturulanlar", aliases=["timeoutlist"])
@commands.guild_only()
@commands.has_guild_permissions(moderate_members=True)
async def susturulanlar(ctx):
    now = _datetime.now(TR_TZ)
    muted = [m for m in ctx.guild.members if m.timed_out_until and m.timed_out_until > now]
    lines = [f"{m.display_name} · {discord.utils.format_dt(m.timed_out_until, 'R')}" for m in muted[:25]]
    text = "\n".join(lines) or "Şu anda susturulmuş üye yok."
    await ctx.send(embed=discord.Embed(title=f"Susturulmuş üyeler ({len(muted)})", description=text[:4000], color=discord.Color.orange()), allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="sunucuid", aliases=["guildid"])
@commands.guild_only()
async def sunucu_id(ctx):
    await ctx.send(f"Sunucu kimliği: `{ctx.guild.id}`")


@bot.command(name="uyesayilari", aliases=["uyeistatistik2"])
@commands.guild_only()
async def uye_sayilari(ctx):
    members = ctx.guild.members
    humans = [m for m in members if not m.bot]
    bots = len(members) - len(humans)
    online = sum(1 for m in humans if m.status != discord.Status.offline)
    embed = discord.Embed(title="Üye sayıları", color=discord.Color.blurple())
    embed.add_field(name="Toplam", value=str(len(members)), inline=True)
    embed.add_field(name="İnsan", value=str(len(humans)), inline=True)
    embed.add_field(name="Bot", value=str(bots), inline=True)
    embed.add_field(name="Çevrimiçi (yaklaşık)", value=str(online), inline=True)
    await ctx.send(embed=embed)


@bot.command(name="mesajinfo", aliases=["msginfo"])
@commands.guild_only()
@commands.has_guild_permissions(manage_messages=True)
async def mesaj_info(ctx, mesaj_id: int = None):
    if mesaj_id is None:
        await ctx.send("Kullanım: `B!mesajinfo <mesaj_id>`")
        return
    msg = await _fetch_message_or_warn(ctx, mesaj_id)
    if msg is None:
        return
    embed = discord.Embed(title="Mesaj bilgisi", color=discord.Color.blurple())
    embed.add_field(name="Gönderen", value=f"{msg.author} · `{msg.author.id}`", inline=False)
    embed.add_field(name="Gönderilme", value=discord.utils.format_dt(msg.created_at, "F"), inline=True)
    embed.add_field(name="Sabitli", value="Evet" if msg.pinned else "Hayır", inline=True)
    embed.add_field(name="Ek sayısı", value=str(len(msg.attachments)), inline=True)
    embed.add_field(name="Bağlantı", value=msg.jump_url, inline=False)
    await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


# -------------------- 3.6.4: SUNUCU BAZLI KUFUR AYARI --------------------
cursor.execute("""CREATE TABLE IF NOT EXISTS guild_profanity (
    guild_id INTEGER PRIMARY KEY, allowed INTEGER NOT NULL DEFAULT 1
)""")
db.commit()


def profanity_allowed(guild_id):
    try:
        cursor.execute("SELECT allowed FROM guild_profanity WHERE guild_id=?", (guild_id,))
        row = cursor.fetchone()
    except sqlite3.Error:
        return True
    return True if row is None else bool(row[0])


@bot.command(name="kufur", aliases=["küfür"])
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def kufur_ayari(ctx, durum: str = None):
    flag = _switch_value(durum)
    if flag is None:
        current = "açık" if profanity_allowed(ctx.guild.id) else "kapalı"
        await ctx.send(f"Bu sunucuda küfür ayarı şu an **{current}**. Kullanım: `B!kufur <aç|kapat>`")
        return
    cursor.execute(
        "INSERT INTO guild_profanity (guild_id, allowed) VALUES (?,?) "
        "ON CONFLICT(guild_id) DO UPDATE SET allowed=excluded.allowed",
        (ctx.guild.id, 1 if flag else 0),
    )
    db.commit()
    await ctx.send(f"Bu sunucuda bot küfürü **{'açıldı' if flag else 'kapatıldı'}**.")


# -------------------- 3.6.4: EK YARDIMCI KOMUTLAR --------------------
PERSONALITY_TRAITS = ["sakin", "enerjik", "esprici", "düşünceli", "meraklı", "cesur", "şakacı", "sabırlı"]
APOLOGY_LINES = ["Özür dilerim, bu benim hatamdı.", "Kusura bakma, bir daha olmayacak.", "Pardon, dikkatsizlik ettim."]
CONGRAT_LINES = ["Tebrikler! Harika iş çıkardın.", "Helal olsun, gurur duyuyoruz!", "Büyük başarı, kutlu olsun!"]
TR_DAY_NAMES = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]


@bot.command(name="kisilik", aliases=["kişilik"])
async def kisilik(ctx, uye: discord.Member = None):
    uye = uye or ctx.author
    trait = random.Random(f"trait-{uye.id}-{time.strftime('%Y-%m-%d')}").choice(PERSONALITY_TRAITS)
    await ctx.send(f"🧩 {uye.display_name} bugün **{trait}** bir ruh hali taşıyor.", allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="enerji", aliases=["pil"])
async def enerji(ctx, uye: discord.Member = None):
    uye = uye or ctx.author
    level = random.Random(f"energy-{uye.id}-{time.strftime('%Y-%m-%d')}").randint(5, 100)
    bar = "█" * (level // 10) + "░" * (10 - level // 10)
    await ctx.send(f"🔋 {uye.display_name} bugünkü enerji: `{bar}` **%{level}**", allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="rastgelesayi", aliases=["rsayi"])
async def rastgele_sayi(ctx, minimum: int = None, maximum: int = None):
    if minimum is None or maximum is None or minimum > maximum:
        await ctx.send("Kullanım: `B!rastgelesayi <min> <max>` örnek: `B!rastgelesayi 1 50`")
        return
    await ctx.send(f"🔢 Sonuç: **{random.randint(minimum, maximum)}** ({minimum} – {maximum})")


@bot.command(name="ortalama", aliases=["average"])
async def ortalama(ctx, *sayilar: str):
    try:
        values = [float(s.replace(",", ".")) for s in sayilar]
    except ValueError:
        await ctx.send("Sadece sayı gir. Örnek: `B!ortalama 4 7 9`")
        return
    if not values or len(values) > 100:
        await ctx.send("Kullanım: `B!ortalama <sayı1> <sayı2> ...` (en fazla 100 sayı)")
        return
    avg = sum(values) / len(values)
    text = f"{avg:.2f}".rstrip("0").rstrip(".")
    await ctx.send(f"📊 {len(values)} sayının ortalaması: **{text}**")


@bot.command(name="tarih", aliases=["bugun", "bugün"])
async def tarih(ctx):
    now = _datetime.now(TR_TZ)
    await ctx.send(f"📅 Bugün **{now:%d.%m.%Y}**, {TR_DAY_NAMES[now.weekday()]}")


@bot.command(name="ozur", aliases=["özür"])
async def ozur(ctx, uye: discord.Member = None):
    target = uye.mention if uye else ""
    await ctx.send(f"🙏 {ctx.author.mention} {target} {random.choice(APOLOGY_LINES)}".replace("  ", " ").strip(),
                   allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="tebrik", aliases=["congrats"])
async def tebrik(ctx, uye: discord.Member = None):
    target = uye.mention if uye else "herkese"
    await ctx.send(f"🎉 {ctx.author.mention} {target} için: {random.choice(CONGRAT_LINES)}",
                   allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="sunucuyas", aliases=["sunucuyaş"])
@commands.guild_only()
async def sunucu_yasi(ctx):
    created = ctx.guild.created_at
    days = (_datetime.now(created.tzinfo) - created).days
    await ctx.send(f"🎂 **{ctx.guild.name}** {days} günlük. Kuruluş: {discord.utils.format_dt(created, 'D')}")


@bot.command(name="kanalsayisi", aliases=["kanalsay"])
@commands.guild_only()
async def kanal_sayisi(ctx):
    embed = discord.Embed(title="Kanal sayıları", color=discord.Color.blurple())
    embed.add_field(name="Metin", value=str(len(ctx.guild.text_channels)), inline=True)
    embed.add_field(name="Ses", value=str(len(ctx.guild.voice_channels)), inline=True)
    embed.add_field(name="Kategori", value=str(len(ctx.guild.categories)), inline=True)
    embed.add_field(name="Toplam", value=str(len(ctx.guild.channels)), inline=True)
    await ctx.send(embed=embed)


@bot.command(name="sesbilgi", aliases=["voiceinfo"])
@commands.guild_only()
async def ses_bilgi(ctx, kanal: discord.VoiceChannel = None):
    kanal = kanal or (ctx.author.voice.channel if ctx.author.voice else None)
    if kanal is None:
        await ctx.send("Bir ses kanalı belirt veya bir ses kanalında ol. Örnek: `B!sesbilgi #genel-ses`")
        return
    embed = discord.Embed(title=f"🔊 {kanal.name}", color=discord.Color.green())
    embed.add_field(name="Bit hızı", value=f"{kanal.bitrate // 1000} kbps", inline=True)
    embed.add_field(name="Kullanıcı limiti", value=str(kanal.user_limit or "Sınırsız"), inline=True)
    embed.add_field(name="Şu an içeride", value=str(len(kanal.members)), inline=True)
    await ctx.send(embed=embed)


# -------------------- 3.6.4: EK KOMUT PAKETI (METIN, RASTGELE, OYUN, ZAMAN, SOSYAL, NOT, EKONOMI) --------------------
import codecs as _codecs
import math as _math
import string as _string

cursor.execute("""CREATE TABLE IF NOT EXISTS personal_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    note TEXT NOT NULL, created_at REAL NOT NULL
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS quiz_wins (
    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, wins INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
)""")
db.commit()

active_games = {}  # kanal_id -> aktif oyun durumu (quiz, karisik, matematik, adamasmaca)
GAME_TIMEOUT = 300  # saniye; bu sureden sonra takili oyun kendiliginden kapanir


def _get_active_game(channel_id):
    game = active_games.get(channel_id)
    if game and time.time() - game.get("started", 0) > GAME_TIMEOUT:
        active_games.pop(channel_id, None)
        return None
    return game

_TR_TO_ASCII = str.maketrans({
    "ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u",
    "Ç": "c", "Ğ": "g", "İ": "i", "Ö": "o", "Ş": "s", "Ü": "u", "I": "i",
})
MORSE_CODE = {
    "a": ".-", "b": "-...", "c": "-.-.", "d": "-..", "e": ".", "f": "..-.", "g": "--.", "h": "....",
    "i": "..", "j": ".---", "k": "-.-", "l": ".-..", "m": "--", "n": "-.", "o": "---", "p": ".--.",
    "q": "--.-", "r": ".-.", "s": "...", "t": "-", "u": "..-", "v": "...-", "w": ".--", "x": "-..-",
    "y": "-.--", "z": "--..", "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
    "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.",
}
MORSE_DECODE = {value: key for key, value in MORSE_CODE.items()}
ROMAN_VALUES = [
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"),
    (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
]
QUIZ_QUESTIONS = [
    ("Türkiye'nin başkenti neresidir?", "Ankara"),
    ("Güneş sisteminin en büyük gezegeni hangisidir?", "Jüpiter"),
    ("Suyun kimyasal formülü nedir?", "H2O"),
    ("Bir yılda kaç hafta vardır? (kabaca, tam sayı)", "52"),
    ("Hangi gezegen 'Kızıl Gezegen' olarak bilinir?", "Mars"),
    ("Türkiye'nin en kalabalık şehri hangisidir?", "İstanbul"),
    ("Bir üçgenin iç açılarının toplamı kaç derecedir?", "180"),
    ("İnsan kalbinin kaç odacığı vardır?", "4"),
    ("Bir haftada kaç saat vardır?", "168"),
    ("Hangi hayvan en hızlı karada koşar?", "Çita"),
]
SCRAMBLE_WORDS = ["bilgisayar", "kahvaltı", "sunucu", "paklava", "oyuncu", "telefon", "kitap", "balık", "zeytin", "gökyüzü"]
HANGMAN_WORDS = ["sunucu", "paklava", "bilgisayar", "oyuncu", "kitap", "telefon", "kahve", "balık", "zeytin", "tarih"]
NAMES_LIST = ["Ayşe", "Mehmet", "Elif", "Can", "Zeynep", "Emre", "Derya", "Kerem", "Selin", "Burak", "Defne", "Oğuz"]
WORDS_LIST = ["gökyüzü", "pencere", "yolculuk", "deniz", "kütüphane", "rüzgar", "köprü", "macera", "ışık", "bahçe"]
ANIMALS_LIST = ["kedi", "köpek", "kartal", "yunus", "tilki", "panda", "baykuş", "kaplumbağa", "kirpi", "at"]
FOODS_LIST = ["mantı", "kuru fasulye", "köfte", "pide", "karnıyarık", "lahmacun", "menemen", "börek", "mercimek çorbası", "dolma"]
DRINKS_LIST = ["ayran", "çay", "limonata", "kahve", "şalgam", "sahlep", "soda", "ıhlamur", "meyve suyu", "türk kahvesi"]
PLACES_LIST = ["Kapadokya", "Ege sahilleri", "Karadeniz yaylaları", "Pamukkale", "Efes", "Trabzon", "Antalya", "Bursa", "Safranbolu", "Eğirdir"]
MUSIC_LIST = ["rock", "pop", "caz", "klasik", "hip-hop", "türk halk müziği", "elektronik", "blues", "metal", "reggae"]
HOBBY_LIST = ["resim yapmak", "fotoğrafçılık", "yürüyüş", "kitap okumak", "bahçecilik", "yemek yapmak", "bisiklet", "origami", "sudoku", "puzzle"]
GENRE_LIST = ["aksiyon", "komedi", "korku", "bilim kurgu", "dram", "gerilim", "romantik", "animasyon", "belgesel", "fantastik"]
CITY_LIST = ["Ankara", "İzmir", "Konya", "Antalya", "Erzurum", "Van", "Gaziantep", "Samsun", "Eskişehir", "Mardin"]
EMOJI_LIST = ["😀", "🚀", "🍕", "🎮", "🌙", "🔥", "🎲", "⚡", "🍀", "🎯"]
GIFT_IDEAS = ["Bir kahve ikramı", "Sevdiği oyunun dijital kopyası", "Kişiye özel bir not", "Kitap", "Bitki", "Çikolata kutusu", "Bir film gecesi"]
CARD_SUITS = ["♠️", "♥️", "♦️", "♣️"]
CARD_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]


def _to_ascii(text):
    return text.translate(_TR_TO_ASCII)


def _tr_upper(text):
    return text.translate(str.maketrans({"i": "İ"})).upper()


def _tr_lower(text):
    return text.translate(str.maketrans({"I": "ı", "İ": "i"})).lower()


def _tr_title(text):
    return " ".join(_tr_upper(w[:1]) + _tr_lower(w[1:]) for w in text.split())


def _to_roman(number):
    out = ""
    for value, symbol in ROMAN_VALUES:
        while number >= value:
            out += symbol
            number -= value
    return out


def _award_game(user_id, guild_id, reward):
    add_balance(0, user_id, reward)
    if guild_id:
        cursor.execute(
            "INSERT INTO quiz_wins (guild_id,user_id,wins) VALUES (?,?,1) "
            "ON CONFLICT(guild_id,user_id) DO UPDATE SET wins=wins+1",
            (guild_id, user_id),
        )
        db.commit()


def _hangman_board(game):
    masked = " ".join(c if (c in game["guessed"] or c == " ") else "_" for c in game["answer"])
    return f"🪢 `{masked}`  ·  yanlış: **{game['wrong']}/6**  ·  harf tahmini: `B!harf <harf>`"


# ---------- METIN VE DIL ARACLARI ----------
@bot.command(name="buyuk", aliases=["büyük"])
async def buyuk(ctx, *, metin: str = None):
    if not metin:
        await ctx.send("Kullanım: `B!buyuk <metin>`")
        return
    await ctx.send(_tr_upper(metin)[:1900], allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="kucuk", aliases=["küçük"])
async def kucuk(ctx, *, metin: str = None):
    if not metin:
        await ctx.send("Kullanım: `B!kucuk <metin>`")
        return
    await ctx.send(_tr_lower(metin)[:1900], allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="baslik", aliases=["başlık"])
async def baslik(ctx, *, metin: str = None):
    if not metin:
        await ctx.send("Kullanım: `B!baslik <metin>`")
        return
    await ctx.send(_tr_title(metin)[:1900], allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="morse")
async def morse(ctx, *, metin: str = None):
    if not metin:
        await ctx.send("Kullanım: `B!morse <metin>`")
        return
    parts = []
    for ch in _to_ascii(metin).lower()[:300]:
        if ch == " ":
            parts.append("/")
        elif ch in MORSE_CODE:
            parts.append(MORSE_CODE[ch])
    await ctx.send(f"`{' '.join(parts)[:1900]}`")


@bot.command(name="morsecoz")
async def morsecoz(ctx, *, kod: str = None):
    if not kod:
        await ctx.send("Kullanım: `B!morsecoz .... . .-.. .-.. ---` (kelimeler arası `/`)")
        return
    out = []
    for token in kod.split()[:300]:
        if token == "/":
            out.append(" ")
        elif token in MORSE_DECODE:
            out.append(MORSE_DECODE[token])
        else:
            await ctx.send("Geçersiz morse kodu.")
            return
    await ctx.send("".join(out)[:1900], allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="rot13")
async def rot13(ctx, *, metin: str = None):
    if not metin:
        await ctx.send("Kullanım: `B!rot13 <metin>`")
        return
    await ctx.send(_codecs.encode(metin[:500], "rot13")[:1900], allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="base64", aliases=["base64kodla"])
async def base64_kodla(ctx, *, metin: str = None):
    if not metin:
        await ctx.send("Kullanım: `B!base64 <metin>`")
        return
    encoded = base64.b64encode(metin[:800].encode("utf-8")).decode("ascii")
    await ctx.send(f"`{encoded[:1900]}`")


@bot.command(name="base64coz", aliases=["base64decode"])
async def base64_coz(ctx, *, kod: str = None):
    if not kod:
        await ctx.send("Kullanım: `B!base64coz <kod>`")
        return
    try:
        decoded = base64.b64decode(kod.strip().encode("ascii"), validate=True).decode("utf-8")
    except ValueError:
        await ctx.send("Bu geçerli bir base64 kodu değil.")
        return
    await ctx.send(decoded[:1900], allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="harfsay")
async def harfsay(ctx, *, metin: str = None):
    if not metin:
        await ctx.send("Kullanım: `B!harfsay <metin>`")
        return
    letters = [c for c in _tr_lower(metin) if c.isalpha()]
    vowels = sum(1 for c in letters if c in "aeıioöuü")
    digits = sum(1 for c in metin if c.isdigit())
    spaces = metin.count(" ")
    await ctx.send(
        f"🔤 Harf: **{len(letters)}** · Sesli: **{vowels}** · Sessiz: **{len(letters) - vowels}** · "
        f"Rakam: **{digits}** · Boşluk: **{spaces}**"
    )


@bot.command(name="palindrom")
async def palindrom(ctx, *, metin: str = None):
    if not metin:
        await ctx.send("Kullanım: `B!palindrom <metin>`")
        return
    clean = [c for c in _tr_lower(metin) if c.isalnum()]
    is_palindrome = bool(clean) and clean == clean[::-1]
    await ctx.send("✅ Palindrom!" if is_palindrome else "❌ Palindrom değil.")


@bot.command(name="alfabe")
async def alfabe(ctx):
    await ctx.send(f"🔠 Rastgele harf: **{random.choice('ABCÇDEFGĞHIİJKLMNOÖPRSŞTUÜVYZ')}**")


@bot.command(name="bosluksil")
async def bosluksil(ctx, *, metin: str = None):
    if not metin:
        await ctx.send("Kullanım: `B!bosluksil <metin>`")
        return
    await ctx.send(metin.replace(" ", "")[:1900], allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="emojiyaz")
async def emojiyaz(ctx, *, metin: str = None):
    if not metin:
        await ctx.send("Kullanım: `B!emojiyaz <metin>` (harf ve rakamları emoji yapar)")
        return
    out = []
    for ch in _to_ascii(metin.lower())[:150]:
        if "a" <= ch <= "z":
            out.append(chr(0x1F1E6 + ord(ch) - ord("a")))
        elif ch.isdigit():
            out.append(ch + "\N{COMBINING ENCLOSING KEYCAP}")
        elif ch == " ":
            out.append("   ")
    await ctx.send(" ".join(out)[:1900] or "Gösterilecek harf yok.")


@bot.command(name="kelimeayir")
async def kelimeayir(ctx, *, metin: str = None):
    if not metin:
        await ctx.send("Kullanım: `B!kelimeayir <metin>`")
        return
    words = metin.split()[:20]
    lines = [f"{i}. {w}" for i, w in enumerate(words, start=1)]
    await ctx.send("\n".join(lines)[:1900], allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="tekrarla")
async def tekrarla(ctx, adet: int = None, *, metin: str = None):
    if adet is None or metin is None or not 1 <= adet <= 10:
        await ctx.send("Kullanım: `B!tekrarla <1-10> <metin>`")
        return
    text = " ".join([metin[:200]] * adet)[:1900]
    await ctx.send(text, allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="sifre", aliases=["sifreuret", "şifre"])
async def sifre(ctx, uzunluk: int = 12):
    if not 6 <= uzunluk <= 64:
        await ctx.send("Kullanım: `B!sifre <6-64>` (şifre DM olarak gönderilir)")
        return
    alphabet = _string.ascii_letters + _string.digits + "!@#$%&*?"
    rng = random.SystemRandom()
    password = "".join(rng.choice(alphabet) for _ in range(uzunluk))
    try:
        await ctx.author.send(f"🔐 Şifren ({uzunluk} karakter): `{password}`")
        await ctx.send("Şifren DM olarak gönderildi.", delete_after=8)
    except discord.Forbidden:
        await ctx.send("DM'lerin kapalı, şifreyi göndermedim. DM'lerini açıp tekrar dene.")


@bot.command(name="rakamtoplam")
async def rakam_toplam(ctx, sayi: str = None):
    if not sayi or not sayi.lstrip("-").isdigit():
        await ctx.send("Kullanım: `B!rakamtoplam <sayı>`")
        return
    total = sum(int(c) for c in sayi if c.isdigit())
    await ctx.send(f"➕ `{sayi}` sayısının rakamları toplamı: **{total}**")


@bot.command(name="asal")
async def asal(ctx, sayi: int = None):
    if sayi is None or not 0 <= sayi <= 10 ** 12:
        await ctx.send("Kullanım: `B!asal <0-10^12>`")
        return
    is_prime = sayi >= 2 and all(sayi % d for d in range(2, _math.isqrt(sayi) + 1))
    await ctx.send(f"🔢 **{sayi}** {'asal' if is_prime else 'asal değil'}.")


@bot.command(name="fibonacci", aliases=["fibo"])
async def fibonacci(ctx, n: int = None):
    if n is None or not 1 <= n <= 50:
        await ctx.send("Kullanım: `B!fibonacci <1-50>`")
        return
    seq, a, b = [], 0, 1
    for _ in range(n):
        seq.append(a)
        a, b = b, a + b
    await ctx.send(", ".join(map(str, seq)))


@bot.command(name="faktoriyel")
async def faktoriyel(ctx, n: int = None):
    if n is None or not 0 <= n <= 20:
        await ctx.send("Kullanım: `B!faktoriyel <0-20>`")
        return
    await ctx.send(f"🧮 {n}! = **{_math.factorial(n)}**")


@bot.command(name="romen")
async def romen(ctx, sayi: int = None):
    if sayi is None or not 1 <= sayi <= 3999:
        await ctx.send("Kullanım: `B!romen <1-3999>`")
        return
    await ctx.send(f"🏛️ {sayi} = **{_to_roman(sayi)}**")


@bot.command(name="ikilik")
async def ikilik(ctx, sayi: int = None):
    if sayi is None or not 0 <= sayi <= 10 ** 18:
        await ctx.send("Kullanım: `B!ikilik <0-10^18>`")
        return
    await ctx.send(f"💻 {sayi} = `{bin(sayi)[2:]}` (ikilik)")


@bot.command(name="onluk")
async def onluk(ctx, ikili: str = None):
    if not ikili or len(ikili) > 64 or set(ikili) - {"0", "1"}:
        await ctx.send("Kullanım: `B!onluk <ikilik sayı>` örnek: `B!onluk 1011`")
        return
    await ctx.send(f"💻 `{ikili}` = **{int(ikili, 2)}** (onluk)")


# ---------- RASTGELE URETIM ----------
@bot.command(name="rastgeleisim")
async def rastgele_isim(ctx):
    await ctx.send(f"🧑 Rastgele isim: **{random.choice(NAMES_LIST)}**")


@bot.command(name="rastgelekelime")
async def rastgele_kelime(ctx):
    await ctx.send(f"📝 Rastgele kelime: **{random.choice(WORDS_LIST)}**")


@bot.command(name="rastgelehayvan")
async def rastgele_hayvan(ctx):
    await ctx.send(f"🐾 Rastgele hayvan: **{random.choice(ANIMALS_LIST)}**")


@bot.command(name="rastgeleyemek")
async def rastgele_yemek(ctx):
    await ctx.send(f"🍽️ Bugün şunu yiyebilirsin: **{random.choice(FOODS_LIST)}**")


@bot.command(name="icecekoner")
async def icecek_oner(ctx):
    await ctx.send(f"🥤 Bir şey iç: **{random.choice(DRINKS_LIST)}**")


@bot.command(name="rastgeleyer")
async def rastgele_yer(ctx):
    await ctx.send(f"🗺️ Gidilecek yer: **{random.choice(PLACES_LIST)}**")


@bot.command(name="muzikturu")
async def muzik_turu(ctx):
    await ctx.send(f"🎵 Bugünkü tür: **{random.choice(MUSIC_LIST)}**")


@bot.command(name="hobioner")
async def hobi_oner(ctx):
    await ctx.send(f"🎨 Yeni hobi denemek ister misin? **{random.choice(HOBBY_LIST)}**")


@bot.command(name="turoner")
async def tur_oner(ctx):
    await ctx.send(f"🎬 Film türü önerisi: **{random.choice(GENRE_LIST)}**")


@bot.command(name="sehironer")
async def sehir_oner(ctx):
    await ctx.send(f"🏙️ Şehir önerisi: **{random.choice(CITY_LIST)}**")


@bot.command(name="rastgeleemoji")
async def rastgele_emoji(ctx):
    await ctx.send(random.choice(EMOJI_LIST))


@bot.command(name="kartcek")
async def kart_cek(ctx):
    card = f"{random.choice(CARD_RANKS)}{random.choice(CARD_SUITS)}"
    await ctx.send(f"🃏 Çekilen kart: **{card}**")


@bot.command(name="zarat")
async def zar_at(ctx, adet: int = 1, yuz: int = 6):
    if not (1 <= adet <= 10 and 2 <= yuz <= 100):
        await ctx.send("Kullanım: `B!zarat <1-10 adet> <2-100 yüz>`")
        return
    rolls = [random.randint(1, yuz) for _ in range(adet)]
    await ctx.send(f"🎲 Atışlar: {', '.join(map(str, rolls))} · Toplam: **{sum(rolls)}**")


# ---------- OYUN VE BULMACA ----------
@bot.command(name="quiz", aliases=["bilgiyarismasi"])
async def quiz(ctx):
    if _get_active_game(ctx.channel.id) is not None:
        await ctx.send("Bu kanalda zaten aktif bir oyun var. `B!cevap`, `B!ipucu` veya `B!atla` kullan.")
        return
    question, answer = random.choice(QUIZ_QUESTIONS)
    active_games[ctx.channel.id] = {"kind": "quiz", "answer": answer, "started": time.time()}
    await ctx.send(f"🧠 Soru: **{question}**\nCevap için `B!cevap <cevap>` · ipucu `B!ipucu`")


@bot.command(name="karisik", aliases=["karışık"])
async def karisik(ctx):
    if _get_active_game(ctx.channel.id) is not None:
        await ctx.send("Bu kanalda zaten aktif bir oyun var.")
        return
    word = random.choice(SCRAMBLE_WORDS)
    letters = list(word)
    scrambled = word
    for _ in range(10):
        random.shuffle(letters)
        scrambled = "".join(letters)
        if scrambled != word:
            break
    active_games[ctx.channel.id] = {"kind": "karisik", "answer": word, "started": time.time()}
    await ctx.send(f"🔀 Karışık kelime: **{scrambled}**\nCevap için `B!cevap <kelime>`")


@bot.command(name="matematik", aliases=["mat"])
async def matematik(ctx):
    if _get_active_game(ctx.channel.id) is not None:
        await ctx.send("Bu kanalda zaten aktif bir oyun var.")
        return
    a, b = random.randint(2, 30), random.randint(2, 30)
    op = random.choice(["+", "-", "*"])
    answer = {"+": a + b, "-": a - b, "*": a * b}[op]
    active_games[ctx.channel.id] = {"kind": "matematik", "answer": str(answer), "started": time.time()}
    await ctx.send(f"🧮 Soru: **{a} {op} {b} = ?**\nCevap için `B!cevap <sayı>`")


@bot.command(name="adamasmaca", aliases=["hangman"])
async def adamasmaca(ctx):
    if _get_active_game(ctx.channel.id) is not None:
        await ctx.send("Bu kanalda zaten aktif bir oyun var.")
        return
    word = random.choice(HANGMAN_WORDS)
    game = {"kind": "adamasmaca", "answer": word, "guessed": set(), "wrong": 0, "started": time.time()}
    active_games[ctx.channel.id] = game
    await ctx.send(_hangman_board(game))


@bot.command(name="harf")
async def harf(ctx, girdi: str = None):
    game = _get_active_game(ctx.channel.id)
    if not game or game["kind"] != "adamasmaca":
        await ctx.send("Aktif adam asmaca yok. `B!adamasmaca` ile başlat.")
        return
    if not girdi or len(girdi.strip()) != 1 or not girdi.strip().isalpha():
        await ctx.send("Kullanım: `B!harf <tek harf>`")
        return
    letter = _tr_lower(girdi.strip())
    if letter in game["guessed"]:
        await ctx.send("Bu harfi zaten denedin.")
        return
    game["guessed"].add(letter)
    if letter not in game["answer"]:
        game["wrong"] += 1
    if all(c in game["guessed"] or c == " " for c in game["answer"]):
        del active_games[ctx.channel.id]
        _award_game(ctx.author.id, ctx.guild.id if ctx.guild else 0, 100)
        await ctx.send(f"🎉 Bildin! Kelime **{game['answer']}**. **+100** paklava kazandın.")
        return
    if game["wrong"] >= 6:
        del active_games[ctx.channel.id]
        await ctx.send(f"💀 Kaybettin. Kelime: **{game['answer']}**")
        return
    await ctx.send(_hangman_board(game))


@bot.command(name="cevap", aliases=["answer"])
async def cevap(ctx, *, metin: str = None):
    game = _get_active_game(ctx.channel.id)
    if not game:
        await ctx.send("Bu kanalda aktif bir oyun yok. `B!quiz`, `B!karisik`, `B!matematik` veya `B!adamasmaca` ile başlat.")
        return
    if not metin:
        await ctx.send("Kullanım: `B!cevap <cevap>`")
        return
    if normalize_economy_key(metin) != normalize_economy_key(str(game["answer"])):
        await ctx.send("❌ Yanlış cevap.")
        return
    del active_games[ctx.channel.id]
    rewards = {"quiz": 50, "karisik": 80, "matematik": 40}
    reward = rewards.get(game["kind"], 30)
    _award_game(ctx.author.id, ctx.guild.id if ctx.guild else 0, reward)
    await ctx.send(f"✅ Doğru! {ctx.author.mention} **+{reward}** paklava kazandı. Cevap: **{game['answer']}**")


@bot.command(name="ipucu", aliases=["hint"])
async def ipucu(ctx):
    game = _get_active_game(ctx.channel.id)
    if not game:
        await ctx.send("Aktif oyun yok.")
        return
    if game["kind"] == "adamasmaca":
        await ctx.send(f"💡 Kelime **{len(game['answer'])}** harfli. {_hangman_board(game)}")
        return
    answer = str(game["answer"])
    await ctx.send(f"💡 İpucu: **{answer[0]}{'_' * (len(answer) - 1)}** ({len(answer)} karakter)")


@bot.command(name="atla", aliases=["pas"])
async def atla(ctx):
    game = active_games.pop(ctx.channel.id, None)
    if not game:
        await ctx.send("Atlanacak aktif oyun yok.")
        return
    await ctx.send(f"⏭️ Oyun atlandı. Cevap: **{game['answer']}**")


@bot.command(name="quizliderlik", aliases=["quiztop"])
@commands.guild_only()
async def quiz_liderlik(ctx):
    cursor.execute("SELECT user_id, wins FROM quiz_wins WHERE guild_id=? ORDER BY wins DESC LIMIT 10", (ctx.guild.id,))
    rows = cursor.fetchall()
    if not rows:
        await ctx.send("Henüz kazanan yok. `B!quiz` ile başla.")
        return
    lines = []
    for idx, (uid, wins) in enumerate(rows, start=1):
        member = ctx.guild.get_member(uid)
        name = member.display_name if member else "Ayrılmış üye"
        lines.append(f"{idx}. {name} · **{wins}** galibiyet")
    await ctx.send(embed=discord.Embed(title="Bilgi yarışması liderliği", description="\n".join(lines), color=discord.Color.gold()),
                   allowed_mentions=discord.AllowedMentions.none())


# ---------- ZAMAN VE HATIRLATMA ----------
@bot.command(name="geridayim", aliases=["zamanlayici", "zamanlayıcı"])
@commands.cooldown(1, 30, commands.BucketType.user)
async def geri_sayim(ctx, saniye: int = None):
    if saniye is None or not 1 <= saniye <= 600:
        await ctx.send("Kullanım: `B!geridayim <1-600 saniye>`")
        return
    await ctx.send(f"⏳ {saniye} saniye sonra haber vereceğim.")
    await asyncio.sleep(saniye)
    await ctx.send(f"⏰ {ctx.author.mention} süre doldu!")


@bot.command(name="pomodoro")
@commands.cooldown(1, 120, commands.BucketType.user)
async def pomodoro(ctx):
    await ctx.send(f"🍅 {ctx.author.mention} 25 dakikalık odak başladı.")
    await asyncio.sleep(25 * 60)
    await ctx.send(f"🍅 {ctx.author.mention} pomodoro bitti. 5 dakika mola ver.")


@bot.command(name="mola")
@commands.cooldown(1, 30, commands.BucketType.user)
async def mola(ctx, dakika: int = None):
    if dakika is None or not 1 <= dakika <= 60:
        await ctx.send("Kullanım: `B!mola <1-60 dakika>`")
        return
    await ctx.send(f"☕ {dakika} dakika mola. Dönünce haber vereceğim.")
    await asyncio.sleep(dakika * 60)
    await ctx.send(f"☕ {ctx.author.mention} mola bitti!")


@bot.command(name="tarihfark")
async def tarih_fark(ctx, ilk: str = None, ikinci: str = None):
    try:
        d1 = _datetime.strptime(ilk or "", "%d.%m.%Y").date()
        d2 = _datetime.strptime(ikinci or "", "%d.%m.%Y").date()
    except ValueError:
        await ctx.send("Kullanım: `B!tarihfark <gg.aa.yyyy> <gg.aa.yyyy>`")
        return
    await ctx.send(f"📆 İki tarih arasında **{abs((d2 - d1).days)}** gün var.")


@bot.command(name="yas")
async def yas(ctx, dogum: str = None):
    try:
        birth = _datetime.strptime(dogum or "", "%d.%m.%Y").date()
    except ValueError:
        await ctx.send("Kullanım: `B!yas <gg.aa.yyyy>`")
        return
    today = _datetime.now(TR_TZ).date()
    if birth > today:
        await ctx.send("Doğum tarihi gelecekte olamaz.")
        return
    years = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
    await ctx.send(f"🎂 Yaşın: **{years}** yıl ({(today - birth).days} gün)")


@bot.command(name="yilbasi")
async def yilbasi(ctx):
    today = _datetime.now(TR_TZ).date()
    days = (_date(today.year + 1, 1, 1) - today).days
    await ctx.send(f"🎆 Yeni yıla **{days}** gün kaldı.")


@bot.command(name="haftasonu")
async def haftasonu(ctx):
    weekday = _datetime.now(TR_TZ).weekday()
    days = (5 - weekday) % 7
    text = "Bugün cumartesi!" if days == 0 else f"Cumartesiye **{days}** gün kaldı."
    await ctx.send(f"🏖️ {text}")


@bot.command(name="zaman")
async def zaman(ctx, saniye: int = None):
    if saniye is None or saniye < 0:
        await ctx.send("Kullanım: `B!zaman <saniye>`")
        return
    hours, rest = divmod(saniye, 3600)
    minutes, seconds = divmod(rest, 60)
    await ctx.send(f"⌛ {saniye} saniye = **{hours} saat {minutes} dakika {seconds} saniye**")


# ---------- SOSYAL ISTATISTIK ----------
@bot.command(name="mesajsayim", aliases=["mesajsay"])
@commands.guild_only()
async def mesaj_sayim(ctx, uye: discord.Member = None):
    uye = uye or ctx.author
    cursor.execute("SELECT messages FROM user_profiles WHERE guild_id=? AND user_id=?", (ctx.guild.id, uye.id))
    row = cursor.fetchone()
    count = int(row[0]) if row else 0
    await ctx.send(f"💬 {uye.display_name} toplam **{count}** mesaj gönderdi.", allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="enaktif", aliases=["enaktifler"])
@commands.guild_only()
async def en_aktif(ctx):
    cursor.execute("SELECT user_id, messages FROM user_profiles WHERE guild_id=? ORDER BY messages DESC LIMIT 10", (ctx.guild.id,))
    rows = cursor.fetchall()
    if not rows:
        await ctx.send("Henüz mesaj verisi yok.")
        return
    lines = []
    for idx, (uid, count) in enumerate(rows, start=1):
        member = ctx.guild.get_member(uid)
        name = member.display_name if member else "Ayrılmış üye"
        lines.append(f"{idx}. {name} · **{count}** mesaj")
    await ctx.send(embed=discord.Embed(title="En aktif 10 üye", description="\n".join(lines), color=discord.Color.blurple()),
                   allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="uyelikyas", aliases=["uyelikbilgi"])
@commands.guild_only()
async def uyelik_yas(ctx, uye: discord.Member = None):
    uye = uye or ctx.author
    if not uye.joined_at:
        await ctx.send("Katılım tarihi bilinmiyor.")
        return
    days = (_datetime.now(uye.joined_at.tzinfo) - uye.joined_at).days
    await ctx.send(f"📅 {uye.display_name} sunucuya **{days}** gün önce katıldı.", allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="hediyeoner")
async def hediye_oner(ctx):
    await ctx.send(f"🎁 Hediye fikri: **{random.choice(GIFT_IDEAS)}**")


# ---------- KISISEL NOTLAR ----------
@bot.command(name="notal", aliases=["notekle2"])
@commands.guild_only()
async def not_al(ctx, *, metin: str = None):
    if not metin:
        await ctx.send("Kullanım: `B!notal <metin>` (en fazla 300 karakter)")
        return
    cursor.execute(
        "INSERT INTO personal_notes (guild_id,user_id,note,created_at) VALUES (?,?,?,?)",
        (ctx.guild.id, ctx.author.id, metin[:300], time.time()),
    )
    db.commit()
    await ctx.send(f"📝 Notun kaydedildi. Numara: **#{cursor.lastrowid}**", delete_after=10)


@bot.command(name="notlarim", aliases=["kisiselnotlar"])
@commands.guild_only()
async def notlarim(ctx):
    cursor.execute(
        "SELECT id, note FROM personal_notes WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 10",
        (ctx.guild.id, ctx.author.id),
    )
    rows = cursor.fetchall()
    if not rows:
        await ctx.send("Kayıtlı notun yok. `B!notal <metin>` ile ekle.")
        return
    text = "\n".join(f"**#{nid}** · {note}" for nid, note in rows)
    await ctx.send(embed=discord.Embed(title="Notlarım (son 10)", description=text[:4000], color=discord.Color.yellow()))


@bot.command(name="notsil")
@commands.guild_only()
async def not_sil(ctx, not_id: int = None):
    if not_id is None:
        await ctx.send("Kullanım: `B!notsil <numara>` (numarayı `B!notlarim` ile görürsün)")
        return
    cursor.execute("DELETE FROM personal_notes WHERE id=? AND guild_id=? AND user_id=?", (not_id, ctx.guild.id, ctx.author.id))
    db.commit()
    if cursor.rowcount:
        await ctx.send(f"🗑️ #{not_id} numaralı not silindi.")
    else:
        await ctx.send("Bu numarada sana ait bir not bulunamadı.")


# ---------- EKONOMI EK KOMUTLAR ----------
@bot.command(name="kazi")
async def kazi(ctx):
    remaining = fun_cooldown_remaining(ctx.author.id, "scratch", 3600)
    if remaining:
        await ctx.send(f"Yeni kazıma için **{remaining // 60} dakika** beklemen lazım.")
        return
    set_fun_cooldown(ctx.author.id, "scratch")
    tier = random.choices(["yok", "az", "orta", "jackpot"], weights=[60, 30, 9, 1])[0]
    amount = {"yok": 0, "az": random.randint(100, 300), "orta": random.randint(500, 1000), "jackpot": 5000}[tier]
    if amount:
        add_balance(0, ctx.author.id, amount)
        await ctx.send(f"🎟️ Kazı kazan: **+{amount}** paklava! Güncel bakiye: **{balance_label(ctx.author.id, get_balance(0, ctx.author.id))}**")
    else:
        await ctx.send("🎟️ Kazı kazan boş çıktı.")


@bot.command(name="maas")
async def maas(ctx):
    remaining = fun_cooldown_remaining(ctx.author.id, "salary", 21600)
    if remaining:
        await ctx.send(f"Maaşın için **{remaining // 3600} saat** beklemen lazım.")
        return
    set_fun_cooldown(ctx.author.id, "salary")
    add_balance(0, ctx.author.id, 300)
    await ctx.send(f"💼 Maaş yattı: **+300** paklava.")


@bot.command(name="hirsiz", aliases=["çal"])
@commands.guild_only()
async def hirsiz(ctx, uye: discord.Member = None):
    if uye is None or uye.bot or uye.id == ctx.author.id:
        await ctx.send("Kullanım: `B!hirsiz @üye` (kendinden veya bottan çalamazsın)")
        return
    remaining = fun_cooldown_remaining(ctx.author.id, "steal", 3600)
    if remaining:
        await ctx.send(f"Yeni hırsızlık için **{remaining // 60} dakika** beklemen lazım.")
        return
    target_balance = get_balance(0, uye.id)
    if target_balance < 50:
        await ctx.send("Bu kişinin çalınacak kadar paklavası yok (50 altı).")
        return
    set_fun_cooldown(ctx.author.id, "steal")
    if random.random() < 0.4:
        loot = min(target_balance, max(1, int(target_balance * random.uniform(0.1, 0.2))))
        add_balance(0, uye.id, -loot)
        add_balance(0, ctx.author.id, loot)
        await ctx.send(f"🦹 {ctx.author.mention} {uye.mention} kullanıcısından **{loot}** paklava çaldı!",
                       allowed_mentions=discord.AllowedMentions.none())
    else:
        fine = min(100, get_balance(0, ctx.author.id))
        add_balance(0, ctx.author.id, -fine)
        await ctx.send(f"🚨 Yakalandın! **{fine}** paklava ceza ödedin.")


@bot.command(name="bakiyekarsilastir", aliases=["bakiyekiyas"])
@commands.guild_only()
async def bakiye_karsilastir(ctx, uye: discord.Member = None):
    if uye is None:
        await ctx.send("Kullanım: `B!bakiyekarsilastir @üye`")
        return
    mine = get_balance(0, ctx.author.id)
    theirs = get_balance(0, uye.id)
    await ctx.send(
        f"💰 {ctx.author.display_name}: **{balance_label(ctx.author.id, mine)}** · "
        f"{uye.display_name}: **{balance_label(uye.id, theirs)}**",
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.command(name="kutu")
async def kutu(ctx):
    cost = 200
    balance = get_balance(0, ctx.author.id)
    if ctx.author.id != OWNER_ID and balance < cost:
        await ctx.send(f"Kutu için **{cost}** paklava gerekiyor. Bakiyen **{balance_label(ctx.author.id, balance)}**.")
        return
    add_balance(0, ctx.author.id, -cost)
    prize = random.choices([0, 100, 300, 800, 2000], weights=[50, 30, 15, 4, 1])[0]
    if prize:
        add_balance(0, ctx.author.id, prize)
        await ctx.send(f"🎁 Kutudan **{prize}** paklava çıktı! Net: **{prize - cost:+d}** paklava.")
    else:
        await ctx.send(f"🎁 Kutu boş çıktı. **-{cost}** paklava.")


# -------------------- 3.6.4: OTOMATIK SISTEMLER (FILTRE, LOG, TEPKI ROLU, YILDIZ PANOSU) --------------------
cursor.execute("""CREATE TABLE IF NOT EXISTS automod_settings (
    guild_id INTEGER PRIMARY KEY, link_filter INTEGER NOT NULL DEFAULT 0, caps_filter INTEGER NOT NULL DEFAULT 0
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS reaction_roles (
    guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
    emoji TEXT NOT NULL, role_id INTEGER NOT NULL,
    PRIMARY KEY (message_id, emoji)
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS starboard_settings (
    guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL, threshold INTEGER NOT NULL DEFAULT 3
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS starboard_posts (
    guild_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, message_id)
)""")
db.commit()

LINK_PATTERN = re.compile(r"(https?://|www\.|discord\.gg/)", re.IGNORECASE)
STAR_EMOJI = "⭐"


def _automod_flags(guild_id):
    cursor.execute("SELECT link_filter, caps_filter FROM automod_settings WHERE guild_id=?", (guild_id,))
    row = cursor.fetchone()
    if not row:
        return {"link": False, "caps": False}
    return {"link": bool(row[0]), "caps": bool(row[1])}


def _is_mostly_caps(text):
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 12:
        return False
    return sum(1 for c in letters if c.isupper()) / len(letters) > 0.8


async def _automod_on_message(message):
    if not message.guild or message.author.bot or not message.content:
        return
    if message.author.guild_permissions.manage_messages:
        return
    flags = _automod_flags(message.guild.id)
    reason = None
    if flags["link"] and LINK_PATTERN.search(message.content):
        reason = "bağlantı paylaşımı"
    elif flags["caps"] and _is_mostly_caps(message.content):
        reason = "aşırı büyük harf kullanımı"
    if reason is None:
        return
    try:
        await message.delete()
    except discord.DiscordException:
        return
    await message.channel.send(f"{message.author.mention}, bu sunucuda {reason} engelli.", delete_after=8)
    await send_security_log(
        message.guild,
        f"Otomatik koruma: {message.author} kullanıcısının mesajı silindi ({reason}) · #{message.channel}",
    )


async def _log_message_edit(before, after):
    if not after.guild or after.author.bot or before.content == after.content:
        return
    old = discord.utils.escape_mentions(before.content or "(içerik yok)")[:300]
    new = discord.utils.escape_mentions(after.content or "(içerik yok)")[:300]
    await send_security_log(after.guild, f"Mesaj düzenlendi: {after.author} · #{after.channel}\nÖnce: {old}\nSonra: {new}")


_delete_log_times = defaultdict(deque)


async def _log_message_delete(message):
    if not message.guild or message.author.bot:
        return
    now = time.time()
    window = _delete_log_times[message.channel.id]
    while window and now - window[0] > 10:
        window.popleft()
    window.append(now)
    if len(window) > 5:
        if len(window) == 6:
            await send_security_log(message.guild, f"Kısa sürede çok sayıda mesaj silindi · #{message.channel} · ayrıntılar atlanıyor.")
        return
    content = discord.utils.escape_mentions(message.content or "(içerik yok)")[:300]
    await send_security_log(message.guild, f"Mesaj silindi: {message.author} · #{message.channel}\n{content}")


async def _starboard_on_reaction(payload):
    if not payload.guild_id or str(payload.emoji) != STAR_EMOJI:
        return
    cursor.execute("SELECT channel_id, threshold FROM starboard_settings WHERE guild_id=?", (payload.guild_id,))
    row = cursor.fetchone()
    if not row:
        return
    board_id, threshold = row
    cursor.execute("SELECT 1 FROM starboard_posts WHERE guild_id=? AND message_id=?", (payload.guild_id, payload.message_id))
    if cursor.fetchone():
        return
    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return
    try:
        channel = guild.get_channel(payload.channel_id) or await bot.fetch_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
    except discord.DiscordException:
        return
    stars = next((r.count for r in message.reactions if str(r.emoji) == STAR_EMOJI), 0)
    if stars < threshold or message.author.bot:
        return
    board = guild.get_channel(board_id)
    if board is None:
        return
    embed = discord.Embed(description=(message.content or "(ek)")[:500], color=discord.Color.gold(), timestamp=message.created_at)
    embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
    embed.add_field(name="Kaynak", value=f"[Mesaja git]({message.jump_url})", inline=False)
    try:
        await board.send(f"{STAR_EMOJI} **{stars}** · #{channel.name}", embed=embed)
    except discord.DiscordException:
        return
    cursor.execute("INSERT OR IGNORE INTO starboard_posts (guild_id,message_id) VALUES (?,?)", (payload.guild_id, payload.message_id))
    db.commit()


async def _reaction_role_change(payload, adding):
    if not payload.guild_id or payload.user_id == getattr(bot.user, "id", None):
        return
    cursor.execute("SELECT role_id FROM reaction_roles WHERE message_id=? AND emoji=?", (payload.message_id, str(payload.emoji)))
    row = cursor.fetchone()
    if not row:
        return
    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return
    role = guild.get_role(row[0])
    member = guild.get_member(payload.user_id)
    if role is None or member is None or member.bot:
        return
    try:
        if adding:
            await member.add_roles(role, reason="Tepki rolü")
        else:
            await member.remove_roles(role, reason="Tepki rolü kaldırıldı")
    except discord.DiscordException:
        return


async def _reaction_role_add(payload):
    await _reaction_role_change(payload, True)


async def _reaction_role_remove(payload):
    await _reaction_role_change(payload, False)


bot.add_listener(_automod_on_message, "on_message")
bot.add_listener(_log_message_edit, "on_message_edit")
bot.add_listener(_log_message_delete, "on_message_delete")
bot.add_listener(_starboard_on_reaction, "on_raw_reaction_add")
bot.add_listener(_reaction_role_add, "on_raw_reaction_add")
bot.add_listener(_reaction_role_remove, "on_raw_reaction_remove")


@bot.command(name="otomod", aliases=["otomoderasyon"])
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def otomod(ctx, ozellik: str = None, durum: str = None):
    flags = _automod_flags(ctx.guild.id)
    if ozellik is None:
        await ctx.send(
            f"Otomatik koruma · bağlantı filtresi: **{'açık' if flags['link'] else 'kapalı'}** · "
            f"büyük harf filtresi: **{'açık' if flags['caps'] else 'kapalı'}**\n"
            "Kullanım: `B!otomod <link|caps> <aç|kapat>`"
        )
        return
    column = {"link": "link_filter", "caps": "caps_filter"}.get(normalize_economy_key(ozellik))
    flag = _switch_value(durum)
    if column is None or flag is None:
        await ctx.send("Kullanım: `B!otomod <link|caps> <aç|kapat>`")
        return
    cursor.execute("INSERT OR IGNORE INTO automod_settings (guild_id) VALUES (?)", (ctx.guild.id,))
    cursor.execute(f"UPDATE automod_settings SET {column}=? WHERE guild_id=?", (1 if flag else 0, ctx.guild.id))
    db.commit()
    label = "bağlantı filtresi" if column == "link_filter" else "büyük harf filtresi"
    await ctx.send(f"✅ {label} **{'açıldı' if flag else 'kapatıldı'}**. Yetkililer bu filtreden muaf.")


@bot.command(name="tepkirol", aliases=["reactionrole"])
@commands.guild_only()
@commands.has_guild_permissions(manage_roles=True)
@commands.bot_has_guild_permissions(manage_roles=True, add_reactions=True)
async def tepki_rol(ctx, mesaj_id: int = None, emoji: str = None, rol: discord.Role = None):
    if mesaj_id is None or not emoji or rol is None:
        await ctx.send("Kullanım: `B!tepkirol <mesaj_id> <emoji> @rol` (mesaj aynı kanalda olmalı)")
        return
    if _role_too_high(ctx, rol):
        await ctx.send("Bu rolü dağıtmak için botun rolü daha üstte olmalı.")
        return
    try:
        msg = await ctx.channel.fetch_message(mesaj_id)
    except discord.DiscordException:
        await ctx.send("Bu kanalda bu ID ile mesaj bulunamadı.")
        return
    try:
        await msg.add_reaction(emoji)
    except discord.DiscordException:
        await ctx.send("Geçersiz emoji veya tepki eklenemedi.")
        return
    cursor.execute(
        "INSERT INTO reaction_roles (guild_id,channel_id,message_id,emoji,role_id) VALUES (?,?,?,?,?) "
        "ON CONFLICT(message_id,emoji) DO UPDATE SET role_id=excluded.role_id, channel_id=excluded.channel_id, guild_id=excluded.guild_id",
        (ctx.guild.id, ctx.channel.id, mesaj_id, emoji, rol.id),
    )
    db.commit()
    await ctx.send(f"✅ {emoji} tepkisi artık **{rol.name}** rolünü veriyor.", allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="tepkirolsil")
@commands.guild_only()
@commands.has_guild_permissions(manage_roles=True)
async def tepki_rol_sil(ctx, mesaj_id: int = None, emoji: str = None):
    if mesaj_id is None or not emoji:
        await ctx.send("Kullanım: `B!tepkirolsil <mesaj_id> <emoji>`")
        return
    cursor.execute("DELETE FROM reaction_roles WHERE guild_id=? AND message_id=? AND emoji=?", (ctx.guild.id, mesaj_id, emoji))
    db.commit()
    if cursor.rowcount:
        await ctx.send("🗑️ Tepki rolü kaldırıldı.")
    else:
        await ctx.send("Bu mesaj ve emoji için kayıtlı tepki rolü yok.")


@bot.command(name="tepkirolliste")
@commands.guild_only()
@commands.has_guild_permissions(manage_roles=True)
async def tepki_rol_liste(ctx):
    cursor.execute("SELECT message_id, emoji, role_id FROM reaction_roles WHERE guild_id=? LIMIT 25", (ctx.guild.id,))
    rows = cursor.fetchall()
    if not rows:
        await ctx.send("Kayıtlı tepki rolü yok.")
        return
    lines = [f"{emoji} → <@&{role_id}> · mesaj `{message_id}`" for message_id, emoji, role_id in rows]
    await ctx.send(embed=discord.Embed(title="Tepki rolleri", description="\n".join(lines), color=discord.Color.blurple()),
                   allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="yildizpano", aliases=["starboard"])
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def yildiz_pano(ctx, kanal: discord.TextChannel = None, adet: int = 3):
    if kanal is None or not 1 <= adet <= 50:
        await ctx.send("Kullanım: `B!yildizpano #kanal <1-50 yıldız>` · ⭐ tepkisi bu sayıya ulaşınca mesaj panoya düşer.")
        return
    cursor.execute(
        "INSERT INTO starboard_settings (guild_id,channel_id,threshold) VALUES (?,?,?) "
        "ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id, threshold=excluded.threshold",
        (ctx.guild.id, kanal.id, adet),
    )
    db.commit()
    await ctx.send(f"⭐ Yıldız panosu {kanal.mention} kanalına kuruldu. Eşik: **{adet}** yıldız.")


@bot.command(name="yildizpanokapat")
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def yildiz_pano_kapat(ctx):
    cursor.execute("DELETE FROM starboard_settings WHERE guild_id=?", (ctx.guild.id,))
    db.commit()
    await ctx.send("Yıldız panosu kapatıldı." if cursor.rowcount else "Bu sunucuda yıldız panosu zaten kurulu değil.")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("Bu komut icin gerekli yonetici izinlerin yok.")
    elif isinstance(error, commands.BotMissingPermissions):
        await ctx.send("Botun gerekli Discord izinleri eksik.")
    elif isinstance(error, commands.NoPrivateMessage):
        await ctx.send("Bu komut sadece sunucuda kullanilabilir.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Komut parametresi hatali. `B!yardim` komutuna bak.")
    elif isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"Bu komutu tekrar kullanmak için **{error.retry_after:.1f} saniye** bekle.")
    else:
        print(f"KOMUT HATASI: {type(error).__name__}: {error}")
        await ctx.send("Komut calisirken hata olustu. Render loglarini kontrol et.")


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN ortam degiskeni eksik. Render > Environment bolumune ekle.")
    if not GROQ_API_KEY:
        print("UYARI: GROQ_API_KEY eksik; bot acilir ancak AI cevaplari calismaz.")
    if not GITHUB_DATA_TOKEN:
        print("UYARI: BOT_DATA_GITHUB_TOKEN eksik; GitHub veritabanı yedeklemesi kapalı.")
    keep_alive()
    bot.run(DISCORD_TOKEN)
