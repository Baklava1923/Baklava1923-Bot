import asyncio
import base64
import io
import json
import os
import random
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict, deque

import discord
from PIL import Image, ImageDraw, ImageFont
from discord.ext import commands
from keep_alive import keep_alive

# Baklava1923 Bot 3.6
OWNER_ID = 1358430002508726276
STARTING_BALANCE = 1000
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
BOT_VERSION = "3.6"

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

# -------------------- VERITABANI --------------------
db = sqlite3.connect("aktiflik.db", check_same_thread=False)
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
db.commit()


def migrate_economy_to_global():
    # Eski sunucu bakiyelerinden kullanici basina en yuksek bakiye korunur.
    cursor.execute("SELECT user_id, MAX(balance) FROM economy GROUP BY user_id")
    balances = cursor.fetchall()
    cursor.execute("SELECT DISTINCT user_id, item_key FROM inventory")
    items = cursor.fetchall()
    cursor.execute("DELETE FROM economy")
    cursor.executemany(
        "INSERT INTO economy (guild_id, user_id, balance) VALUES (0, ?, ?)",
        [(uid, max(0, int(balance or 0))) for uid, balance in balances],
    )
    cursor.execute("DELETE FROM inventory")
    cursor.executemany(
        "INSERT OR IGNORE INTO inventory (guild_id, user_id, item_key) VALUES (0, ?, ?)",
        items,
    )
    db.commit()


migrate_economy_to_global()
active_users = {}
spam_messages = defaultdict(deque)
spam_warned = set()
join_bursts = defaultdict(deque)
raid_alert_cooldowns = {}
ai_histories = defaultdict(lambda: deque(maxlen=60))
# Bot sohbeti kanal bazinda tutulur; !botsohbet kapat komutuna kadar devam eder.
bot_chat_histories = defaultdict(lambda: deque(maxlen=60))
xp_last_awarded = {}


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


# -------------------- BAKLAVAI --------------------
# Kendi icinde calisan cevap motoru: dis servis veya API anahtari kullanmaz.
# Bota etiket atinca veya mesajina yanit verilince her zaman Baklavai cevap verir.
# 3.6+: az kufur, genis hafiza (konusma gecmisi + kalici bilgiler), tekrar engeli,
# konuya ve kullanicinin soyledigine gore cevap.
import ast as _ast
import operator as _operator

cursor.execute("""CREATE TABLE IF NOT EXISTS baklavai_memory (
    user_key TEXT NOT NULL, fact_key TEXT NOT NULL,
    fact_value TEXT NOT NULL, updated_at REAL NOT NULL,
    PRIMARY KEY (user_key, fact_key)
)""")
db.commit()

# Kufur sadece ara sira ve cumle basina en fazla bir tane gelir.
_BAK_KUFURLER = ["amk", "aq", "oç", "lan", "ulan", "siktir", "salak", "hıyar", "dangalak", "piç"]
_BAK_HAFIF = ["abi", "kanka", "dostum", "reis", "koçum", "usta", "şampiyon"]
_BAK_KUFUR_ORANI = 0.2   # her {k} yerinde kufur gelme ihtimali
_BAK_HAFIF_ORANI = 0.25  # kufur gelmezse hafif hitap gelme ihtimali

_BAK_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002300-\U000023FF"
    "\U00002B00-\U00002BFF\U0001F1E6-\U0001F1FF‍️⃣]"
    r"|<a?:\w+:\d+>"
)
_BAK_TR = str.maketrans({
    "ı": "i", "ş": "s", "ğ": "g", "ü": "u", "ö": "o", "ç": "c",
    "İ": "i", "I": "i", "Ş": "s", "Ğ": "g", "Ü": "u", "Ö": "o", "Ç": "c",
})
_BAK_RECENT = defaultdict(deque)
_BAK_GUNLER = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
_BAK_AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]

_BAK_STOP = {
    "neden", "niye", "nasil", "nicin", "kimdir", "nedir", "gibi", "icin", "ama", "fakat", "benim",
    "senin", "bunu", "sunu", "onu", "cok", "daha", "degil", "bile", "hani", "yani", "bence",
    "sence", "tamam", "evet", "hayir", "misin", "musun", "miyim", "bana", "sana", "beni", "seni",
    "olan", "olarak", "diye", "kadar", "sonra", "once", "zaten", "artik", "hala", "bunun",
    "bunlar", "sunlar", "ondan", "benden", "senden", "burada", "orada", "simdi", "bugun",
    "yarin", "dun", "ayni", "nerede", "hangi", "baklavai", "herkes", "sadece", "belki", "falan",
    "filan", "gercekten", "sey", "seyi", "seyler", "var", "yok", "olur", "olmaz", "lazim",
}

_BAK_SAYGI = (
    "Dur bir saniye, şaka yapmayı bırakıyorum çünkü yazdığın şeyi ciddiye alıyorum. "
    "Şu an yalnız olma, yakınındaki güvendiğin biriyle, ailenle ya da bir arkadaşınla hemen konuş. "
    "Kendine zarar verme düşüncen varsa veya şu an tehlikedeysen 112'yi ara. "
    "İstersen burada yazmaya devam et, seni dinliyorum."
)

_BAK_SABLONLAR = {
    "selam": [
        "Selam {k}, hoş geldin, ne var ne yok?",
        "Aleyküm selam {k}, yine mi sen?",
        "Hoş geldin {k}, bir şey söyle de cevap vereyim.",
        "Ooo kimleri görüyorum, naber {k}?",
        "Buyur {k}, ne lazım?",
        "Selam {k}, bu saatte ne arıyorsun burada?",
        "Merhaba {k}, tam sen yazınca aklıma geldin.",
        "Gel bakalım {k}, otur, anlat.",
        "Hey {k}, sunucunun en meraklısı yine buradaymış.",
        "Selamlar, bugün neyi kurcalıyoruz {k}?",
        "Geldin demek {k}, ortam şenlendi.",
        "Selam {k}, hazırım, sen hazırsan başlayalım.",
    ],
    "selam_isim": [
        "Selam {ad}, hoş geldin, seni unutmadım {k}.",
        "Oo {ad} geldi, ne haber {k}?",
        "Hoş geldin {ad}, geçen konuştuklarımız hâlâ aklımda.",
        "Merhaba {ad}, bugün ne yapıyoruz {k}?",
        "{ad} sen misin, özlettin kendini {k}.",
    ],
    "nasilsin": [
        "İyiyim {k}, sen nasılsın?",
        "Ne olacak, 7/24 çalışıyorum, sen anlat bakalım.",
        "Süperim, işlemci fanım uçuyor.",
        "Bomba gibiyim, sen de iyisindir umarım {k}.",
        "Fena değilim, sunucuda herkesi izliyorum, bayağı eğleniyorum.",
        "Şikayetçi değilim {k}, bugün kimse beni kapatmadı.",
        "Gayet iyi, az önce RAM'imi temizledim, taptaze hissediyorum.",
        "Her zamanki gibi {k}, sen nasılsın, neler oluyor?",
        "İdare ediyoruz, sen nasılsın asıl?",
        "Çok iyiyim, birileri sohbet açınca ruhum şenleniyor {k}.",
    ],
    "tesekkur": [
        "Rica ederim {k}, bir şey değil.",
        "Önemli değil, bir dahaki sefere de sor.",
        "Sağ ol deme, utandırma beni {k}.",
        "Tamam tamam, fazla abartma.",
        "Ne demek, her zaman buradayım.",
        "Estağfurullah, görevimiz sohbet {k}.",
        "Bir şey değil {k}, bana sorman yeter.",
        "Rica ederim, yine beklerim.",
    ],
    "hakaret": [
        "Sen kimsin lan {k}, bana mı laf atıyorsun?",
        "Ağzını topla {k}, bana bulaşma.",
        "Kendi hâline bak önce {k}, bana laf atıyorsun.",
        "Siktir git {k}, benimle uğraşma.",
        "Bu kadar mı {k}, daha iyisini bekliyordum senden.",
        "Ooo sinirler gergin {k}, bir su iç sonra gel.",
        "Hakaret etmeyi bilmiyorsun {k}, bir de bana ders anlatacaksın.",
        "Aynısını sana iade ediyorum {k}, kargo ücretsiz.",
        "Küfür kütüphanen bu kadar mı {k}? Yazık.",
    ],
    "kimsin": [
        "Ben Baklavai {k}, Baklava1923 botunun beyniyim, hiçbir dış servise bağlı değilim.",
        "Adım Baklavai, sunucunun küfürbaz muhabbet botuyum.",
        "Baklavai'yim, kafam tamamen kendi sistemim, bilmediğime de bir şey uydururum.",
        "Ben Baklavai, seninle sohbet etmek için buradayım, geri kalanını da zamanla öğrenirsin {k}.",
        "Baklava1923'ün sohbet botuyum, işlem yaparım, seçim yaparım, seni de hatırlarım.",
    ],
    "yardim": [
        "İşlem çözerim, saati ve tarihi söylerim, iki seçenek arasında seçerim, şaka anlatırım, adını ve sevdiklerini aklımda tutarım.",
        "Hesap makinesiyim, falcıyım, şakacıyım, hafızam da var, söylediğin şeyleri unutmam.",
        "Sohbet ederim, hesap yaparım, seçim yaparım. `B!hafıza` yazarsan seni ne kadar tanıdığımı görürsün.",
    ],
    "saka": [
        "Bilgisayar neden doktora gitmiş? Çok virüs kapmış.",
        "Öğretmen: neden geç kaldın? Öğrenci: zaman önümden koştu hocam.",
        "Pingim o kadar yüksek ki mesajım geçmişten geliyor.",
        "Sunucu o kadar sessiz ki bot kendi kendine AFK kaldı.",
        "Temel neden bilgisayar almış? Fare ile oynamak istemiş.",
        "Bir yazılımcıya sormuşlar: hayatında en çok neyi seviyorsun? Cevap: yorum satırlarını, çünkü kimse okumuyor.",
        "Telefonum bana dedi ki: şarjım yüzde bir, ben de ona dedim ki: ben senden daha uzun yaşarım.",
        "İki bit buluşmuş, biri demiş: sen bana sıfır ilgi gösteriyorsun. Diğeri: sen de bir gösteriyorsun.",
        "Wi-Fi şifresini unuttum, o yüzden internetten şifre kurtarma sitesine girmeye çalışıyorum.",
        "Kahve içmeden çalışmam diyen bota bak, ben elektrikle çalışıyorum {k}.",
    ],
    "evet": [
        "Evet, bunu bilmeyen mi var.",
        "Tabii ki, sorulur mu bu {k}.",
        "Evet, hem de nasıl.",
        "Bence evet, ama kafana göre takıl.",
        "Kesinlikle, yüzde yüz.",
        "Aynen öyle {k}.",
        "Evet derim, hiç düşünmeden.",
    ],
    "hayir": [
        "Hayır, nereden çıkardın bunu {k}.",
        "Yok, boşuna umutlanma.",
        "Asla, ağzından yel alsın.",
        "Sanmıyorum, rüya görmüşsün.",
        "Hiç sanmam {k}, tersini düşünmek zor.",
        "Hayır, bunu kimse senin için yapmaz.",
        "Olmaz öyle şey, boş ver.",
    ],
    "belki": [
        "Belki {k}, bugün kararsızım.",
        "Yüzde elli yüzde elli, yazı tura at bence.",
        "Olabilir, olmayabilir, ben kâhin değilim.",
        "Belki, ama bana güvenme çok {k}.",
        "Tam emin değilim, biraz daha anlat bakalım.",
    ],
    "soru": [
        "Bilmiyorum {k}, google diye bir şey var.",
        "Çok sorarsın, kafamı şişirdin.",
        "Bence sen de cevabını biliyorsun, bana ne soruyorsun.",
        "Güzel soru, ama cevabı bende yok.",
        "Hıh, bunu düşünmem lazım, sonra gel.",
        "Bana mı sordun bunu, ben botum, bu işlerden ne anlarım.",
        "İyi soru {k}, ama bu sefer tahmin yürütmeyeceğim.",
        "Cevabını bilsem söylerdim, bilmiyorum, dürüst olayım.",
        "Bu soruyu bir daha sorarsan tahmin yürütürüm, şimdilik pas.",
        "Bu soru için bir uzman lazım, ben sadece muhabbet botuyum.",
    ],
    "soru_kelime": [
        "{w} konusunu soruyorsun ha, ben o konuda ne diyeyim {k}.",
        "{w} hakkında fikrim yok ama merak ettim, sen ne düşünüyorsun?",
        "{w} mı? Bunu bana sorma {k}, sen anlat ben dinleyeyim.",
        "Ya {w} meselesi karışık, sen önce kendi fikrini söyle.",
        "{w} üzerine bir şey bilmiyorum ama ilgimi çekti, devam et.",
    ],
    "secim": [
        "Bence {c} {k}, düşünmeye bile gerek yok.",
        "{c}, tartışmaya gerek yok.",
        "Kesin {c}, başka seçenek yok.",
        "{c} derim, içimden öyle geldi.",
        "Yazı tura attım, {c} çıktı {k}.",
        "{c}, ama beni dinlemek zorunda değilsin.",
        "Bana sorarsan {c}, sonrası senin sorunun.",
    ],
    "matematik": [
        "Sonuç {r}, bunu da mı hesaplayamıyorsun {k}.",
        "{r} çıkıyor, hesap makinen yok muydu?",
        "Cevap {r}, kafanı çalıştır biraz.",
        "{r} eder, kolay işti {k}.",
        "Hesapladım: {r}. Teşekkür beklemiyorum.",
        "Bana kalırsa sonuç {r}, kontrol etmek istersen buyur.",
    ],
    "saat": [
        "Saat {t}, saati bile bilmiyor musun {k}?",
        "Şu an saat {t}, duvarda saat yok mu?",
        "{t} olmuş, zaman su gibi akıyor.",
        "Saat tam {t} {k}, bir yere mi yetişeceksin?",
    ],
    "tarih": [
        "Bugün {d}, takvim almayı düşünsene.",
        "Tarih {d}, günleri karıştırma artık {k}.",
        "{d}, bugünü iyi değerlendir.",
        "Takvime göre {d}, ona göre plan yap.",
    ],
    "resim": [
        "Resme bakamıyorum, gözüm yok benim, ne olduğunu yazıyla anlat.",
        "Görsel göremiyorum, yazı yaz da cevap vereyim.",
        "Resim güzel olabilir ama ben göremiyorum, kelimelerle anlat {k}.",
    ],
    "yedek": [
        '"{s}" diyorsun ha, ne diyeyim şimdi buna {k}.',
        "Bunu ben de anlamadım, daha açık yaz.",
        "Tamam, devam et, dinliyorum.",
        '"{s}" mu? Çok enteresan {k}, devam et.',
        "Ee, sonra ne oldu?",
        "Aynen, bence de öyle {k}.",
        "Anlatmaya devam et, canım sıkılıyordu zaten.",
        "Hmm, ilginç, biraz daha detay ver.",
        "Valla bu konuda ne diyeceğimi bilemedim {k}.",
        "Bir saniye, hafızamı yokluyorum, yine de sen devam et.",
        "Peki, ben bunu not aldım, sıradaki ne?",
    ],
    "yedek_kelime": [
        "{w} demişsin, bu konuda daha çok anlatmak ister misin?",
        "{w} olayı ilginç, bunu ben de merak ettim {k}.",
        "{w} dedin de aklıma takıldı, sen bu işi nasıl görüyorsun?",
        "Ya {w} hakkında konuşmayı seviyorum aslında, devam et.",
        "{w} mi? Hıh, bunu bir daha anlat bakayım.",
        "Tamam {w} konusu açıldı, sen ne düşünüyorsun {k}?",
        "{w} lafı geçince kulaklarım dikiliyor, anlat bakalım.",
    ],
    "matematik_hata": [
        "Sıfıra bölemezsin {k}, matematikten mi kaldın?",
        "Bunu hesaplayamadım, daha basit bir şey yaz.",
        "Sıfıra bölme diye bir şey yok, evren çöker {k}.",
    ],
    "tekrar": [
        "Aynı şeyi yazdın {k}, kayıt mı çalıyor?",
        "Bunu az önce de söyledin, hafızam o kadar kötü değil.",
        "Yine aynısı mı? Yeni bir şey söyle {k}.",
        "Tekrar tekrar yazarsan cevabım değişmez, farklı bir şey dene.",
    ],
    "takip": [
        "Az önce \"{p}\" demiştin, onu mu soruyorsun {k}?",
        "Konu hâlâ \"{p}\" mi, yoksa değişti mi?",
        "\"{p}\" demiştin ya, ona geliyorum şimdi {k}.",
        "Önceki mesajına bakıyorum, \"{p}\" yazmışsın, devam et.",
        "Senin dediğin \"{p}\" hâlâ aklımda, sonrasını anlat.",
    ],
    "tanisma": [
        "Memnun oldum {ad}, adını aklımda tuttum {k}.",
        "Selam {ad}, artık seni unutmam, söz.",
        "{ad} demek, güzel isim, hafızama kazıdım.",
        "Tamam {ad}, bundan sonra sana adınla hitap ederim.",
        "Hoş geldin {ad}, tanıştığımıza memnun oldum.",
    ],
    "sevdi_not": [
        "Not aldım, {f} diye kaydettim aklıma {k}.",
        "{f} demek, bunu unutmam, söz.",
        "{f} konusunu hafızama yazdım, sonra hatırlatırım {k}.",
        "Tamamdır, {f} bende kayıtlı artık.",
    ],
    "hatirla": [
        "Sen {f} seviyorsun diye biliyorum, değişti mi?",
        "Geçen {f} dediğini unutmadım, hâlâ aynı mı?",
        "{f} bahsini hatırlıyorum {k}, hâlâ geçerli mi?",
    ],
    "ad_biliyorum": [
        "Adın {ad}, unutur muyum hiç {k}.",
        "{ad} değil mi? Hafızam sağlam, merak etme.",
        "Sen {ad} olarak kayıtlısın bende, bunu unutan bot mu olurmuş {k}.",
    ],
    "ad_bilmiyorum": [
        "Adını hiç söylemedin {k}, `adım ...` yaz da aklımda tutayım.",
        "Henüz tanışmadık sayılır, adını söyle de kaydedeyim.",
        "Adını bilmiyorum, ama merak ediyorum, söyle bakalım.",
    ],
    "sevdiklerini_say": [
        "Bildiğim kadarıyla {f} seviyorsun {k}.",
        "Bana şunları söylemiştin: {f}.",
        "Hafızamda şunlar var: {f}.",
    ],
    "sevdik_yok": [
        "Bana henüz bir şey söylemedin {k}, `... severim` yaz da aklımda tutayım.",
        "Seninle ilgili çok az şey biliyorum, biraz anlat.",
    ],
    "fav_cevap": [
        "Sen {v} demiştin, unutmadım {k}.",
        "{v}, değil mi? Bende kayıtlı.",
        "En sevdiğin {v} diye biliyorum, yanlışsa düzelt.",
    ],
    "fav_yok": [
        "Bunu bana hiç söylemedin {k}, söyle de kaydedeyim.",
        "Bu konuda bir şey bilmiyorum, anlat bakalım.",
    ],
    "gulme": [
        "Güldüğün şeye ben de güleyim {k}, ne oldu?",
        "Ne bu kahkaha, anlat bari.",
        "Gülme krizine girdin galiba, iyi gelsin.",
        "Neye gülüyoruz, ben de katılayım {k}.",
        "Bu kadar komik olan ne, bana da anlat.",
    ],
    "ozur": [
        "Önemli değil {k}, bir dahaki sefere dikkat et.",
        "Tamam, affettim, ama defterime yazdım.",
        "Özür kabul, ama yine yaparsan bilirsin {k}.",
        "Boş ver, olur öyle şeyler.",
    ],
    "vedalas": [
        "Görüşürüz {k}, yine gel.",
        "Hoşça kal, ben buradayım, kaçmıyorum.",
        "Tamam git bakalım, ben burada sunucuyu beklerim.",
        "İyi geceler, iyi günler, hangisi uyuyorsa {k}.",
        "Kendine iyi bak, yine konuşuruz.",
    ],
    "napiyon": [
        "Seninle konuşuyorum {k}, başka ne yapayım.",
        "Sunucuyu izliyorum, sen ne yapıyorsun?",
        "Boş boş duruyorum, sen gelince hareketlendim.",
        "Mesajlara bakıyorum, sen ne yapıyorsun?",
        "Her zamanki gibi, çalışıyorum. Senin planın ne?",
    ],
    "bot_konusu": [
        "Evet botum {k}, ama kafam senden iyi çalışıyor.",
        "Bot olmak güzel, yorulmuyorsun, uyumuyorsun.",
        "Gerçek insan değilim, ama sohbet etmeyi seviyorum.",
        "Ben bir yazılımım ama seninle konuşmaktan keyif alıyorum {k}.",
        "İnsan değilim, ama hafızam senden iyi, bunu kabul et.",
    ],
    "sunucu_konu": [
        "Bu sunucu bence güzel, ortamı seviyorum.",
        "Yetkililer bakıyor, ben de yardımcı oluyorum {k}.",
        "Sunucuda neler dönüyor, ben her şeyi görüyorum.",
        "Bu sunucu benim evim gibi, sen de öylesin sanırım.",
    ],
    "sikildim": [
        "Sıkıldıysan `B!eğlence` yaz, oyun çok {k}.",
        "Sıkıntı mı bastı, bir şaka isteyelim mi?",
        "Sıkılma be, `B!1vs1` ile birini düelloya davet et.",
        "Canın sıkıldıysa `B!sayıtahmin` oyna, bir de ödül var.",
        "Sıkıntını bana anlat, ben de dinleyeyim.",
        "Boş boş durma, `B!zar` at, bir şey olsun.",
    ],
    "uzgun": [
        "Canın sıkkın mı, anlat bakalım ne oldu?",
        "Üzülme {k}, her şey düzelir, anlat istersen.",
        "Moralin bozuksa konuşalım, dinliyorum.",
        "Kötü hissettiysen buradayım, ne oldu anlat.",
        "Yalnız değilsin, burada konuşabilirsin.",
        "Bazen böyle günler oluyor, ben dinlerim {k}.",
    ],
    "sinirli": [
        "Sakin ol {k}, bir derin nefes al.",
        "Sinirlendin demek, kim yaptı bunu?",
        "Öfken geçsin, sonra konuşuruz, ben buradayım.",
        "Kızma be, değmez, anlat bakayım.",
        "Birine mi sinirlendin, yoksa genel olarak mı?",
    ],
    "sevinc": [
        "Harika {k}, sevindim senin adına!",
        "Süper, kutlamak lazım, ne oldu anlat.",
        "İyi haber mi, hemen anlat, çok merak ettim.",
        "Mutlu olduğuna sevindim {k}, devamını da duyalım.",
        "Bu enerjiyi sevdim, böyle devam.",
    ],
    "uyku": [
        "Uyku vakti mi, git yat artık {k}.",
        "Uyku bölme, git yat, sabaha bakarız.",
        "Ben uyumam ama sen uyumalısın, vücudun şikayetçi olur.",
        "İyi uykular, tatlı rüyalar {k}.",
        "Uykun mu geldi, kahve içmeyi düşünsene.",
        "Uyku düzeni çok önemli, bak bilgi verdim.",
    ],
    "yemek": [
        "Acıktın mı {k}, bir şeyler ye de gel.",
        "Yemek lafı geçince benim de canım paklava çekti.",
        "Ne yiyeceksin, ben sadece bit yerim, tatları bilmem.",
        "Yemek konusunda uzman değilim ama baklava her zaman iyidir {k}.",
        "Bugün ne yedin bakalım, anlat.",
        "Karnını doyur, sonra gel, enerji lazım.",
        "Aç karnına konuşmak zor, bir şey yesen {k}.",
    ],
    "yemek_kisisel": [
        "Sen {f} seviyorsun diye biliyorum, yine mi onu yiyeceksin {k}?",
        "Yemek dedin, aklıma {f} geldi, doğru mu hatırlıyorum?",
        "{f} dediğin yemek bende kayıtlı, yine ona mı gidiyorsun?",
    ],
    "okul": [
        "Ders çalış {k}, sonra oyun oynarsın.",
        "Okul mu, sınav mı, ödev mi, hangisi seni bunalttı?",
        "Çalışmak lazım ama bence mola vermeyi de unutma.",
        "Hocalara selam söyle, bana not versin {k}.",
        "Sınavın varsa şansı sana bırakıyorum.",
        "Ödevini yap, sonra gel, bekliyorum.",
    ],
    "para": [
        "Para konusu açıldı, `B!bakiye` bak bakalım ne durumdasın {k}.",
        "Zengin olmak istiyorsan `B!çalış` yaz.",
        "Paranı bankaya yatır, `B!banka` var.",
        "Para her şey değil ama paklava her şey {k}.",
        "Maaş mı bekliyorsun, bende o iş yok.",
        "Paran azsa `B!günlük` komutunu dene.",
    ],
    "ask": [
        "Aşk mı, konu derinleşiyor, anlat bakalım {k}.",
        "Sevgilin mi var, yoksa crush mu, söyle.",
        "Aşk işlerinde bana danışma, ben sadece botum {k}.",
        "Ee kim bu şanslı, anlat bakalım.",
        "Kalp işleri zor, sen kendi kalbini dinle.",
        "Aşk acısı çekiyorsan bir arkadaşınla konuş {k}.",
    ],
    "muzik": [
        "Müzik mi dinliyorsun, hangi türden anlat {k}.",
        "Bugün ne dinliyorsun, ben sadece bit sesi duyuyorum.",
        "Şarkı lafı geçince dans etmek istiyorum ama bacağım yok.",
        "Müzik zevkini merak ettim, anlat bakalım.",
        "Rap mi, pop mu, rock mı, hangisi {k}?",
    ],
    "muzik_kisisel": [
        "Sen {f} dinliyorsun diye biliyorum, hâlâ aynı mı {k}?",
        "Müzik deyince {f} aklıma geldi, doğru mu hatırlıyorum?",
    ],
    "futbol": [
        "Futbol mu, hangi takımdansın anlat {k}.",
        "Maç lafı geçti, skor kaç oldu?",
        "Hakem hep yanlış karar verir, bunu herkes bilir {k}.",
        "Transferler karışık, kim gitti kim geldi bilmiyorum.",
        "Gol atılınca ben de sevinirim, kodum titriyor.",
    ],
    "futbol_kisisel": [
        "Sen {f} tarafındasın diye biliyorum, maç nasıl gitti {k}?",
        "{f} taraftarı olduğunu hatırlıyorum, bu hafta ne olmuş?",
    ],
    "oyun": [
        "Hangi oyundan bahsediyorsun {k}, anlat bakalım.",
        "Oyun mu, hemen `B!1vs1` ile düello yapalım.",
        "Ben oyun oynayamam ama izlemeyi severim.",
        "En son ne oynadın, anlat {k}.",
        "Oyun konuşmayı seviyorum, hele kaybedenin sinirlenmesini {k}.",
        "{w} demişsin, bunu oynayan çok var sunucuda.",
    ],
    "oyun_kisisel": [
        "Sen {f} oynuyorsun diye biliyorum, hâlâ onda mısın {k}?",
        "Oyun deyince aklıma {f} geliyor, doğru mu?",
    ],
    "hava": [
        "Havaya bakmadım, pencerem yok benim {k}.",
        "Hava nasıl olursa olsun ben sunucuda kalırım.",
        "Yağmur mu yağıyor, şemsiye almayı unutma {k}.",
        "Sıcak mı soğuk mu, giyinirken dikkat et.",
    ],
    "is": [
        "İş mi var, çalışırken bana yazma {k}.",
        "Çalışmak güzel şey, ama dinlenmeyi unutma.",
        "Patronun sinirini bozmasın, sabırlı ol {k}.",
        "İş yoğunsa mola ver, bir çay iç.",
        "Ben 7/24 çalışıyorum, şikayet etmiyorum {k}.",
    ],
}

_BAK_KONULAR = [
    ("sikildim", ("sikildim", "sikiliyorum", "sikici", "bored", "canim sikk"), ("canim sikil", "canim sikk")),
    ("uzgun", ("uzgun", "mutsuz", "aglad", "agliyorum", "yalniz", "moralsiz"), ("kotu hissed", "moralim bozuk", "canim yan")),
    ("sinirli", ("sinir", "kizgin", "kizdim", "delirdim", "ofke", "bikti"), ("sinirim bozuk",)),
    ("sevinc", ("mutlu", "sevindim", "harika", "kazandim", "gectim", "basardim"), ("cok iyi oldu", "cok sevindim")),
    ("ask", ("ask", "sevgili", "crush", "askim", "evlen", "opucuk", "asik"), ("seni seviyorum",)),
    ("uyku", ("uyku", "uyuyacagim", "uyuyorum", "yatiyorum", "yatacagim", "uykum"), ("iyi geceler",)),
    ("yemek", ("yemek", "aciktim", "karnim", "pizza", "kebap", "baklava", "paklava", "lahmacun", "burger", "doner", "kahve", "tatli", "corba", "cay"), ()),
    ("okul", ("okul", "ders", "sinav", "odev", "ogretmen", "hoca", "universite", "lise", "final", "vize"), ()),
    ("para", ("para", "zengin", "fakir", "maas", "bakiye", "parasiz", "borc"), ()),
    ("muzik", ("muzik", "sarki", "sarkici", "rap", "album", "konser", "spotify"), ()),
    ("futbol", ("futbol", "mac", "gol", "galatasaray", "fenerbahce", "besiktas", "trabzonspor", "transfer", "hakem"), ()),
    ("oyun", ("oyun", "minecraft", "roblox", "valorant", "fortnite", "csgo", "pubg", "lol", "among", "gta", "fifa"), ()),
    ("hava", ("hava", "yagmur", "kar", "sicak", "soguk", "gunes"), ()),
    ("is", ("is", "calisiyorum", "patron", "mesai"), ()),
    ("sunucu_konu", ("sunucu", "discord", "admin", "moderator", "yetkili"), ()),
]
_BAK_KISISEL = {"yemek": "fav_yemek", "oyun": "oyun", "futbol": "takim", "muzik": "fav_muzik"}

_BAK_MATH_OPS = {
    _ast.Add: _operator.add, _ast.Sub: _operator.sub, _ast.Mult: _operator.mul,
    _ast.Div: _operator.truediv, _ast.Mod: _operator.mod,
    _ast.FloorDiv: _operator.floordiv, _ast.Pow: _operator.pow,
}
_BAK_MATH_RE = re.compile(r"[0-9\s\.\,\+\-\*\/\(\)\%\^x×÷]+")


def _bak_norm(text):
    return (text or "").translate(_BAK_TR).lower()


def _bak_temiz_deger(value):
    return re.sub(r"[@<>`*_~|\\]", "", value or "").strip()[:30]


def _bak_fill(template, swear_rate=None, **values):
    for key, value in values.items():
        template = template.replace("{" + key + "}", str(value))
    rate = _BAK_KUFUR_ORANI if swear_rate is None else swear_rate
    swore = False

    def repl(_match):
        nonlocal swore
        roll = random.random()
        if not swore and roll < rate:
            swore = True
            return " " + random.choice(_BAK_KUFURLER)
        if roll < rate + _BAK_HAFIF_ORANI:
            return " " + random.choice(_BAK_HAFIF)
        return ""

    return re.sub(r"\s?\{k\}", repl, template)


def _bak_pick(category, swear_rate=None, **values):
    options = _BAK_SABLONLAR[category]
    recent = _BAK_RECENT[category]
    fresh = [i for i in range(len(options)) if i not in recent]
    index = random.choice(fresh or list(range(len(options))))
    recent.append(index)
    keep = max(1, int(len(options) * 0.7))
    while len(recent) > keep:
        recent.popleft()
    values.setdefault("w", "bu")
    values.setdefault("ad", "dostum")
    return _bak_fill(options[index], swear_rate, **values)


def _bak_math_eval(node):
    if isinstance(node, _ast.Expression):
        return _bak_math_eval(node.body)
    if isinstance(node, _ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, _ast.UnaryOp) and isinstance(node.op, (_ast.UAdd, _ast.USub)):
        value = _bak_math_eval(node.operand)
        return value if isinstance(node.op, _ast.UAdd) else -value
    if isinstance(node, _ast.BinOp) and type(node.op) in _BAK_MATH_OPS:
        left, right = _bak_math_eval(node.left), _bak_math_eval(node.right)
        if isinstance(node.op, _ast.Pow) and (abs(right) > 64 or abs(left) > 1e6):
            raise ValueError("cok buyuk")
        return _BAK_MATH_OPS[type(node.op)](left, right)
    raise ValueError("gecersiz")


def _bak_math(raw):
    """Mesaj sadece bir islemse sonucu, hesaplanamazsa None, sifira bolunurse 'HATA' doner."""
    text = raw.lower()
    for word in ("kaç eder", "kaç yapar", "kaçtır", "eşittir", "hesapla", "="):
        text = text.replace(word, " ")
    text = text.replace("?", " ").strip()
    if not text or len(text) > 60 or not _BAK_MATH_RE.fullmatch(text):
        return None
    if not any(ch.isdigit() for ch in text) or not any(op in text for op in "+-*/x×÷^%"):
        return None
    expr = text.replace("^", "**").replace("×", "*").replace("÷", "/").replace("x", "*").replace(",", ".")
    try:
        value = _bak_math_eval(_ast.parse(expr.strip(), mode="eval"))
    except ZeroDivisionError:
        return "HATA"
    except (ValueError, SyntaxError, OverflowError, RecursionError, MemoryError, TypeError):
        return None
    if isinstance(value, complex):
        return None
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        value = int(value) if value == int(value) and abs(value) < 1e15 else round(value, 6)
    result = str(value)
    return result if len(result) <= 40 else None


def _bak_choice(raw):
    parts = re.split(r"\s+(?:yoksa|veya|ya da)\s+", raw.strip(), flags=re.IGNORECASE)
    if len(parts) != 2:
        match = re.fullmatch(r"(.+?)\s+(?:mi|mı|mu|mü)\s+(.+?)\s+(?:mi|mı|mu|mü)\s*\??", raw.strip(), flags=re.IGNORECASE)
        parts = list(match.groups()) if match else []
    if len(parts) != 2:
        match = re.fullmatch(r"(.+?)\s+ya\s+(.+?)\s+(?:mi|mı|mu|mü)\s*\??", raw.strip(), flags=re.IGNORECASE)
        parts = list(match.groups()) if match else []
    cleaned = []
    for part in parts:
        part = re.sub(r"\s+(?:mi|mı|mu|mü)\s*\??$", "", part.strip().rstrip("?!. "), flags=re.IGNORECASE).strip()
        part = _bak_temiz_deger(part)
        if part and len(part) <= 60:
            cleaned.append(part)
    return random.choice(cleaned) if len(cleaned) == 2 else None


def _bak_anahtar(raw):
    """Mesajdaki en anlamli kelimeyi bulur; cevap bu kelimeye gore sekillenir."""
    words = re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü0-9]{4,20}", raw)
    words = [w for w in words if not w.isdigit() and _bak_norm(w) not in _BAK_STOP]
    if not words:
        return None
    words.sort(key=len, reverse=True)
    return random.choice(words[:2]).lower()


# -------------------- BAKLAVAI KALICI HAFIZA --------------------
_BAK_ISIM_STOP = {"ne", "nedir", "neydi", "ya", "bu", "su", "o", "bil", "biliyor", "hatirla", "soyle", "unut"}
_BAK_ISIM_RE = re.compile(
    r"(?:\bad[ıi]m|\bismim|\bbana)\s+([A-Za-zÇĞİÖŞÜçğıöşü]{2,20})\b(?:\s+de(?:yin|yebilirsin)?\b)?",
    re.IGNORECASE,
)
_BAK_SEV_RE = re.compile(r"(?:^|[\s,])(?:ben\s+)?([^,.!?]{2,30}?)\s+(?:seviyorum|severim|çok severim)\b", re.IGNORECASE)
_BAK_SEVME_RE = re.compile(r"(?:^|[\s,])(?:ben\s+)?([^,.!?]{2,30}?)\s+(?:sevmem|sevmiyorum|nefret ediyorum)\b", re.IGNORECASE)
_BAK_FAV_RE = re.compile(r"en sevdi[ğg]im\s+([A-Za-zÇĞİÖŞÜçğıöşü]{2,15})\s+([^,.!?]{2,30})", re.IGNORECASE)
_BAK_TAKIM_RE = re.compile(r"\b(galatasaray|fenerbah[çc]e|be[sş]ikta[sş]|trabzonspor)(?:l[ıi]y[ıi]m|\s+taraftar[ıi]y[ıi]m)", re.IGNORECASE)
_BAK_OYUN_RE = re.compile(r"(?:^|[\s,])(?:ben\s+)?([^,.!?]{2,25}?)\s+oynuyorum\b", re.IGNORECASE)


def _bak_baslik(word):
    word = word.strip()
    if not word:
        return word
    first = "İ" if word[0] == "i" else word[0].upper()
    return first + word[1:].lower()


def _bak_deger_kirp(value):
    value = re.sub(r"^(?:ben\s+|çok\s+|en çok\s+|hep\s+|şu an\s+|şimdi\s+)+", "", value.strip(), flags=re.IGNORECASE)
    return _bak_temiz_deger(value)


def bak_extract_facts(text):
    """Mesajdan kalici bilgileri cikarir (sadece kullanicinin acikca soyledikleri)."""
    found = {}
    raw = (text or "").strip()
    if not raw or len(raw) > 300:
        return found
    norm = _bak_norm(raw)
    if "?" not in raw:
        m = _BAK_ISIM_RE.search(raw)
        if m and _bak_norm(m.group(1)) not in _BAK_ISIM_STOP:
            found["isim"] = _bak_baslik(_bak_temiz_deger(m.group(1)))
        m = _BAK_FAV_RE.search(raw)
        if m and _bak_norm(m.group(2).split()[0]) not in ("ne", "neydi", "nedir"):
            found["fav_" + _bak_norm(m.group(1))] = _bak_deger_kirp(m.group(2))
        m = _BAK_SEV_RE.search(raw)
        if m:
            value = _bak_deger_kirp(m.group(1))
            if value and _bak_norm(value) not in ("seni", "sizi", "ben", "bunu", "onu", "hepsini", "ne", "neyi", "neleri", "neler", "kimi") and "seni" not in _bak_norm(value):
                found["sevdigi"] = value
        m = _BAK_SEVME_RE.search(raw)
        if m:
            value = _bak_deger_kirp(m.group(1))
            if value and _bak_norm(value) not in ("seni", "sizi", "ben", "ne", "neyi", "neleri", "neler", "kimi"):
                found["sevmedigi"] = value
        m = _BAK_TAKIM_RE.search(raw)
        if m:
            found["takim"] = _bak_baslik(_bak_temiz_deger(m.group(1)))
        m = _BAK_OYUN_RE.search(raw)
        if m and "?" not in raw:
            value = _bak_deger_kirp(m.group(1))
            if value and len(value.split()) <= 3:
                found["oyun"] = value
    return {k: v for k, v in found.items() if v}


def bak_load_facts(user_key):
    cursor.execute("SELECT fact_key,fact_value FROM baklavai_memory WHERE user_key=?", (str(user_key),))
    return {key: value for key, value in cursor.fetchall()}


def bak_remember(user_key, text):
    """Yeni bilgileri kaydeder ve kullanicinin tum bilinen bilgilerini dondurur."""
    user_key = str(user_key)
    found = bak_extract_facts(text)
    if found:
        existing = bak_load_facts(user_key)
        for key, value in found.items():
            if key in ("sevdigi", "sevmedigi"):
                items = [i for i in existing.get(key, "").split("|") if i]
                if value.lower() not in [i.lower() for i in items]:
                    items.append(value)
                value = "|".join(items[-6:])
            cursor.execute(
                "INSERT INTO baklavai_memory (user_key,fact_key,fact_value,updated_at) VALUES (?,?,?,?) "
                "ON CONFLICT(user_key,fact_key) DO UPDATE SET fact_value=excluded.fact_value,updated_at=excluded.updated_at",
                (user_key, key, value, time.time()),
            )
        db.commit()
    return bak_load_facts(user_key)


def _bak_son_kullanici_mesaji(history):
    for item in reversed(history or []):
        if item.get("role") == "user":
            return (item.get("content") or "").strip()
    return ""


def get_baklavai_response(user_message, image_data_urls=None, history=None, facts=None):
    """Baklavai cevabini uretir; emojiyi temizler, son cevaplarla ayni olmasin diye tekrar dener."""
    history = list(history or [])
    facts = facts or {}
    seen = {_bak_norm(item.get("content", "")).strip() for item in history if item.get("role") == "assistant"}
    answer = ""
    for _ in range(12):
        candidate = _bak_cevap(user_message, image_data_urls, history, facts)
        candidate = re.sub(r"[ \t]{2,}", " ", _BAK_EMOJI_RE.sub("", candidate)).strip()
        name = facts.get("isim")
        if (candidate != _BAK_SAYGI and name and random.random() < 0.10
                and name.lower() not in candidate.lower()):
            candidate = f"{name}, {candidate[:1].lower()}{candidate[1:]}"
        answer = candidate
        if _bak_norm(candidate).strip() not in seen:
            break
    return answer


def _bak_cevap(user_message, image_data_urls=None, history=None, facts=None):
    """Baklavai'nin cevap motoru: konuya, gecmise ve hafizaya gore cevap verir."""
    raw = (user_message or "").strip()
    norm = _bak_norm(raw)
    token_list = re.findall(r"[a-z0-9]+", norm)
    tokens = set(token_list)
    history = history or []
    facts = facts or {}

    def word(*words):
        return any(w in tokens for w in words)

    def phrase(*phrases):
        return any(p in norm for p in phrases)

    def topic(stems, phrases=()):
        for t in token_list:
            for s in stems:
                if t == s or (len(s) >= 4 and t.startswith(s)):
                    return True
        return any(p in norm for p in phrases)

    if image_data_urls:
        return _bak_pick("resim")

    # Ciddi bir sey anlatiyorsa kufur ve saka yok.
    if phrase("intihar", "olmek istiyorum", "olmek istiyom", "kendimi olduru", "kendime zarar",
              "yasamak istemiyorum", "yasamak istemiyom", "canima kiy", "kendimi kes",
              "hayatima son", "bilegimi", "olsem daha iyi", "depresyon"):
        return _BAK_SAYGI

    result = _bak_math(raw)
    if result == "HATA":
        return _bak_pick("matematik_hata")
    if result is not None:
        return _bak_pick("matematik", r=result)

    now = datetime.now(timezone(timedelta(hours=3)))
    if phrase("saat kac", "saat ne", "saati soyle"):
        return _bak_pick("saat", t=now.strftime("%H:%M"))
    if phrase("bugun gunlerden", "hangi gun", "bugun ayin kaci", "tarih ne", "bugunun tarihi"):
        return _bak_pick("tarih", d=f"{now.day} {_BAK_AYLAR[now.month - 1]} {now.year} {_BAK_GUNLER[now.weekday()]}")

    # Hafizadan cevap: "adim ne", "ne seviyorum", "en sevdigim renk ne".
    if phrase("adim ne", "ismim ne", "adimi biliyor", "ismimi biliyor", "beni taniyor", "beni hatirl", "adimi hatirl", "kim oldugumu"):
        if facts.get("isim"):
            return _bak_pick("ad_biliyorum", ad=facts["isim"])
        return _bak_pick("ad_bilmiyorum")
    if phrase("ne seviyorum", "neleri seviyorum", "ne sevdigimi", "neyi severim", "ne severim"):
        liked = [i for i in facts.get("sevdigi", "").split("|") if i]
        if liked:
            return _bak_pick("sevdiklerini_say", f=", ".join(liked))
        return _bak_pick("sevdik_yok")
    fav_match = re.search(r"en sevdigim\s+([a-z]+)\s+ne", norm)
    if fav_match:
        value = facts.get("fav_" + fav_match.group(1))
        if value:
            return _bak_pick("fav_cevap", v=value)
        return _bak_pick("fav_yok")

    # Kullanici kendiyle ilgili yeni bir sey soylediyse karsilik ver.
    new_facts = bak_extract_facts(raw)
    if "isim" in new_facts:
        return _bak_pick("tanisma", ad=new_facts["isim"])
    for key, value in new_facts.items():
        if key != "isim":
            return _bak_pick("sevdi_not", f=value)

    # Ayni mesaji tekrar eden kullaniciya farkli cevap.
    previous = _bak_son_kullanici_mesaji(history)
    if previous and len(norm) > 3 and _bak_norm(previous).strip() == norm.strip():
        return _bak_pick("tekrar")

    choice = _bak_choice(raw)
    if choice:
        return _bak_pick("secim", c=choice)

    if re.fullmatch(r"\s*(?:[ah]{4,}|(?:ha){2,}h?|(?:he){2,}h?|x+d+|kek+e*|lmao|lol)\s*[!.]*", norm):
        return _bak_pick("gulme")

    if phrase("nasilsin", "naber", "ne haber", "nasil gidiyor", "iyi misin") or word("nbr"):
        return _bak_pick("nasilsin")
    if word("tesekkur", "sagol", "eyv", "eyvallah", "tskler", "tesekkurler") or phrase("sag ol", "tesekkur"):
        return _bak_pick("tesekkur")
    if len(tokens) <= 6 and word("selam", "merhaba", "slm", "mrb", "sa", "as", "hey", "gunaydin", "iyiaksamlar", "hosgeldin"):
        if facts.get("isim") and random.random() < 0.6:
            return _bak_pick("selam_isim", ad=facts["isim"])
        return _bak_pick("selam")
    if word("aptal", "salak", "gerizekali", "mal", "dangalak", "siktir", "amk", "aq", "orospu", "pic", "got", "bok"):
        return _bak_pick("hakaret", swear_rate=0.6)
    if phrase("kimsin", "sen kimsin", "adin ne", "ismin ne", "nesin sen"):
        return _bak_pick("kimsin")
    if phrase("ne yapabilirsin", "neler yapabilirsin", "ne ise yarar", "yardim", "komutlar"):
        return _bak_pick("yardim")
    if word("saka", "fikra", "espri") or phrase("komik bir sey"):
        return _bak_pick("saka")
    if word("ozur", "pardon", "affet") or phrase("kusura bakma"):
        return _bak_pick("ozur")
    if word("gorusuruz", "hoscakal", "bye", "bb", "bay") or phrase("iyi geceler", "kaciyorum", "ben gittim", "ben cikiyorum"):
        return _bak_pick("vedalas")
    if phrase("ne yapiyorsun", "naptin", "napiyosun", "napiyon", "ne yapiyon", "ne yapiyosun"):
        return _bak_pick("napiyon")
    if phrase("bot musun", "gercek misin", "insan misin", "yapay zeka", "robot musun"):
        return _bak_pick("bot_konusu")

    # Konuya gore cevap (oyun, yemek, okul, aski, futbol...). Hafizada bilgi varsa kisisellestir.
    keyword = _bak_anahtar(raw)
    for category, stems, phrases in _BAK_KONULAR:
        if topic(stems, phrases):
            fact_key = _BAK_KISISEL.get(category)
            if fact_key and facts.get(fact_key) and random.random() < 0.45:
                return _bak_pick(category + "_kisisel", f=facts[fact_key])
            return _bak_pick(category, w=keyword or "bu")

    # Kisa takip cumleleri: onceki mesaja gonderme yap.
    if len(tokens) <= 2 and tokens & {"neden", "niye", "nicin", "sonra", "peki", "ee", "eee", "yani", "nasil", "ne"} and previous:
        return _bak_pick("takip", p=discord.utils.escape_mentions(previous).replace("`", "")[:40])

    if tokens & {"mi", "mu"} or raw.endswith("?"):
        # Evet/hayir sorusu mu, acik uclu soru mu?
        if tokens & {"mi", "mu"} and not tokens & {"neden", "niye", "nasil", "nicin", "kim", "kac", "nerede", "nereye", "hangi", "ne", "nedir"}:
            return _bak_pick(random.choice(["evet", "hayir", "evet", "hayir", "belki"]))
        if keyword and random.random() < 0.5:
            return _bak_pick("soru_kelime", w=keyword)
        return _bak_pick("soru")
    if tokens & {"neden", "niye", "nasil", "nicin", "kim", "kac", "nerede", "nereye", "hangi", "nedir", "kimdir"}:
        if keyword and random.random() < 0.5:
            return _bak_pick("soru_kelime", w=keyword)
        return _bak_pick("soru")

    # Hafizada sevdikleri varsa ara sira bunlari hatirla.
    liked = [i for i in facts.get("sevdigi", "").split("|") if i]
    if liked and random.random() < 0.12:
        return _bak_pick("hatirla", f=random.choice(liked))
    if keyword and random.random() < 0.65:
        return _bak_pick("yedek_kelime", w=keyword)
    snippet = discord.utils.escape_mentions(raw[:40])
    return _bak_pick("yedek", s=snippet)


@bot.command(name="hafıza", aliases=["hafiza", "baklavaihafiza"])
async def hafiza(ctx, *, islem: str = None):
    """Baklavai'nin senin hakkında hatırladıklarını gösterir veya siler."""
    user_key = str(ctx.author.id)
    if islem and islem.strip().lower() in {"sil", "unut", "temizle", "reset"}:
        cursor.execute("DELETE FROM baklavai_memory WHERE user_key=?", (user_key,))
        db.commit()
        for key in [k for k in list(ai_histories) if len(k) == 3 and k[2] == ctx.author.id]:
            ai_histories.pop(key, None)
        await ctx.send("Senin hakkında hatırladığım her şeyi sildim.")
        return
    facts = bak_load_facts(user_key)
    if not facts:
        await ctx.send("Henüz senin hakkında bir şey bilmiyorum. Bana `adım ...`, `... severim` veya `en sevdiğim renk ...` yazabilirsin.")
        return
    labels = {"isim": "Adın", "sevdigi": "Sevdiklerin", "sevmedigi": "Sevmediklerin", "takim": "Takımın", "oyun": "Oynadığın oyun"}
    lines = []
    for key, value in facts.items():
        label = labels.get(key) or ("En sevdiğin " + key[4:] if key.startswith("fav_") else key)
        lines.append(f"**{label}:** {discord.utils.escape_mentions(value.replace('|', ', '))}")
    embed = discord.Embed(title="Baklavai Hafızası", description="\n".join(lines), color=discord.Color.orange())
    embed.set_footer(text="Silmek için: B!hafıza sil")
    await ctx.send(embed=embed)


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
    remaining = fun_cooldown_remaining(ctx.author.id, "daily", 86400)
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
    remaining = fun_cooldown_remaining(ctx.author.id, "robbery", 3600)
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
    remaining = fun_cooldown_remaining(ctx.author.id, "weekly_economy", 7 * 86400)
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
    await ctx.send(embed=discord.Embed(title="Meslek Panosu", description="\n".join(lines) + "\n\nMeslek seçmek için `B!meslek <isim>` yaz. Meslek değişimi 12 saatte bir yapılabilir.", color=discord.Color.teal()))


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
    if row and time.time() - float(row[1]) < 12 * 3600:
        remaining = int(12 * 3600 - (time.time() - float(row[1])))
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
    remaining = fun_cooldown_remaining(ctx.author.id, "job_work", 3600)
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
        lines.append(f"**{item['ad']}** — Kurulum: {item['fiyat']:,} paklava · 12 saatte bir gelir: {item['gelir']:,}".replace(",", ".") + f"\nKomut: `B!işletmekur {key}`")
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
    await ctx.send(f"Tebrikler! **{business['ad']}** işletmesini kurdun. Her 12 saatte bir `B!işletmetopla` ile **{business['gelir']:,} paklava** kazanabilirsin.".replace(",", "."))


@bot.command(name="işletme", aliases=["isletme"])
async def isletme(ctx):
    cursor.execute("SELECT business_key,purchased_at FROM economy_businesses WHERE user_id=?", (ctx.author.id,))
    row = cursor.fetchone()
    if not row or row[0] not in BUSINESSES:
        await ctx.send("Henüz işletmen yok. `B!işletmeler` ile bir işletme seçebilirsin.")
        return
    business = BUSINESSES[row[0]]
    remaining = fun_cooldown_remaining(ctx.author.id, "business_collect", 12 * 3600)
    status = "Gelir toplamaya hazır" if not remaining else f"Sonraki gelir için yaklaşık {remaining // 3600} saat {(remaining % 3600) // 60} dakika"
    embed = discord.Embed(title=business["ad"], description=f"12 saatlik gelir: **{business['gelir']:,} paklava**\nDurum: **{status}**".replace(",", "."), color=discord.Color.gold())
    await ctx.send(embed=embed)


@bot.command(name="işletmetopla", aliases=["isletmetopla", "işletmegelir", "isletmegelir"])
async def isletmetopla(ctx):
    cursor.execute("SELECT business_key FROM economy_businesses WHERE user_id=?", (ctx.author.id,))
    row = cursor.fetchone()
    if not row or row[0] not in BUSINESSES:
        await ctx.send("Gelir toplamak için önce `B!işletmekur` ile işletme kurmalısın.")
        return
    remaining = fun_cooldown_remaining(ctx.author.id, "business_collect", 12 * 3600)
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
    embed.add_field(name="Troll ve etkileşim", value="`B!meme` · `B!şaka` · `B!tersle @oyuncu` · `B!roast @oyuncu` · `B!ego`\\n`B!ship @kişi1 @kişi2` · `B!uyum @oyuncu` · `B!kapışma @oyuncu` · `B!iltifat @oyuncu` · `B!kimdaha @oyuncu`\\n`B!bahane` · `B!itiraf <metin>` · `B!soru <metin>`", inline=False)
    embed.add_field(name="Paklava ödülleri", value="`B!günlük` · `B!soygun` · `B!hediye @oyuncu <miktar>` · `B!bakiye` · `B!market` · `B!envanter`", inline=False)
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
    embed = discord.Embed(title="Global Paklava Ekonomisi 3.6", color=discord.Color.gold())
    embed.description = "Bakiyen tüm sunucularda ortaktır. Cüzdanını, bankanı, mesleğini ve işletmeni yönet."
    embed.add_field(name="Bakiye ve banka", value="`B!bakiye [@oyuncu]` · `B!banka`\n`B!yatır <miktar>` · `B!çek <miktar>`", inline=False)
    embed.add_field(name="Kazanç", value="`B!günlük` · `B!haftalık` · `B!gorev`\n`B!meslekler` · `B!meslek <isim>` · `B!çalış`", inline=False)
    embed.add_field(name="İşletme", value="`B!işletmeler` · `B!işletmekur <isim>`\n`B!işletme` · `B!işletmetopla`", inline=False)
    embed.add_field(name="Market ve ticaret", value="`B!market` · `B!satinal <eşya>` · `B!envanter`\n`B!hediye @oyuncu <miktar>` · `B!ticaret @oyuncu <eşya>`\n`B!ekonomisiralamasi`", inline=False)
    await ctx.send(embed=embed)


@bot.command(name="yapayzeka", aliases=["ai"])
async def yapayzeka(ctx):
    embed = discord.Embed(title="Yapay Zeka Komutları", color=discord.Color.blurple())
    embed.description = "Beni etiketle veya bir mesajıma yanıt vererek Baklavai ile sohbet et."
    embed.add_field(name="Komutlar", value="`B!öneri <mesaj>`", inline=False)
    await ctx.send(embed=embed)


HELP_CATEGORIES = {
    "Başlangıç": ("Temel kullanım", "`!` · `B!` · `b!` önekleri kullanılabilir.\n`B!yardım` kategorili yardım menüsü\n`B!botdurum` çalışma durumu\n`/durum` ayrıntılı bot durumu\n`B!sunucubilgi` sunucu istatistikleri"),
    "Yapay zeka": ("Sohbet", "Botu etiketleyerek veya botun mesajına yanıt vererek Baklavai ile sohbet et.\n`B!hafıza` Baklavai'nin seni ne kadar tanıdığını gösterir (`B!hafıza sil` ile silinir).\n`B!botsohbet @bot` sunucuda botlarla kontrollü sohbet (Yönetici).\n`B!botsohbet kapat` sohbeti kapatır."),
    "Eğlence ve oyunlar": ("Mini oyunlar, GIF ve simülasyon", "`B!eğlence`\n`B!fakemesaj @oyuncu <mesaj>` avatarli mesaj önizlemesi\n`B!ship @kişi1 @kişi2`\n`B!gayradar24`\n`B!tkm @oyuncu`\n`B!1vs1 @oyuncu`\n`B!zar [yüz]` · `B!yazitura yazı/tura`\n`B!sayıtahmin` · `B!tahmin <sayı>`\n`B!refleks` · `B!kapışma @oyuncu`\n`B!hype [@oyuncu]` · `B!zafer [@oyuncu]`\n`B!şaka` · `B!meme` · `B!soru <metin>`\n`B!tersle` · `B!roast` · `B!iltifat` · `B!uyum @oyuncu`"),
    "Ekonomi": ("Paklava, banka, meslek, işletme ve ticaret", "`B!ekonomi`\n`B!bakiye [@oyuncu]` · `B!banka`\n`B!yatır <miktar>` · `B!çek <miktar>`\n`B!günlük` · `B!haftalık` · `B!gorev`\n`B!meslekler` · `B!meslek <isim>` · `B!çalış`\n`B!işletmeler` · `B!işletmekur <isim>`\n`B!işletme` · `B!işletmetopla`\n`B!market` · `B!satinal <eşya>` · `B!envanter [@oyuncu]`\n`B!hediye @oyuncu <miktar>`\n`B!ticaret @oyuncu <eşya_anahtarı>`\n`B!ekonomisiralamasi`"),
    "Profil ve seviye": ("XP, seviye, başarımlar", "`B!profil [@oyuncu]`\n`B!seviye [@oyuncu]`\n`B!liderlik`\n`B!basarim [@oyuncu]`\n`B!gorev`\nYöneticiler: `B!seviyerol <seviye> @rol`"),
    "Sunucu yönetimi": ("Kurulum ve kanallar", "`B!sunucu` yönetim paneli\n`B!sunucuoluştur` şablon menüsü\n`B!sunucukur` temel kanallar\n`B!kanaloluştur <isim>`\n`B!rololuştur <isim>`\n`B!oto-rol @rol`\n`B!gelengiden #kanal`\n`B!kilit` · `B!kilitac`\n`B!ayarlar` · `B!yedekle` · `B!yedekler` · `B!gerial <id>`"),
    "Güvenlik ve moderasyon": ("Koruma ve kayıt", "`B!sunucukoruma`\n`B!sunuculog #kanal`\n`B!uyar @üye <sebep>`\n`B!uyarilar @üye`\n`B!uyarisil @üye`\n`B!sustur @üye <dakika> <sebep>`\n`B!susturac @üye`"),
    "Topluluk ve etkinlik": ("Bilet, öneri, çekiliş, oylama", "`B!öneri <metin>`\n`B!önerikanal #kanal`\n`B!bilet [konu]` · `B!biletkapat`\n`B!cekilis <dakika> <ödül>`\n`B!turnuva <dakika> <başlık>`\n`B!oylama <dakika> <soru> | <seçenek 1> | <seçenek 2>`\n`B!hatirlat <dakika> <metin>`\n`B!duyuru #kanal <metin>`"),
    "Aktiflik ve araçlar": ("Ek yardımcılar", "`B!aktiflik` 7/30/60/90 günlük tablo\n`B!itiraf <metin>`\n`B!bahane`\n`B!yapayzeka`\n`B!öneri <metin>`"),
}


def make_bot_banner_gif():
    """Yardım menüsünün üzerinde gösterilen hareketli Baklava1923-Bot başlığı."""
    width, height = 720, 190
    frames = []
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 43)
        small_font = ImageFont.truetype("DejaVuSans.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
        small_font = ImageFont.load_default()

    for index in range(24):
        image = Image.new("RGB", (width, height), (16, 20, 42))
        draw = ImageDraw.Draw(image)
        # Yavaş kayan ışık çizgileri ve nabız halkaları.
        for x in range(-180, width + 180, 90):
            offset = (index * 8) % 90
            draw.line((x + offset, 0, x - 70 + offset, height), fill=(27, 45, 78), width=2)
        pulse = (index % 12) / 11
        radius = int(42 + pulse * 14)
        draw.ellipse((62-radius, 88-radius, 62+radius, 88+radius), outline=(232, 170, 75), width=3)
        draw.ellipse((62-25, 88-25, 62+25, 88+25), fill=(150, 75, 30), outline=(255, 215, 135), width=3)
        draw.text((48, 75), "B", font=font, fill=(255, 240, 205))
        title = "Baklava1923-Bot"
        box = draw.textbbox((0, 0), title, font=font)
        title_width = box[2] - box[0]
        text_x = 110 + (width - 135 - title_width) / 2
        glow = int(160 + 80 * (0.5 + 0.5 * __import__("math").sin(index * __import__("math").pi / 6)))
        draw.text((text_x, 55), title, font=font, fill=(255, glow, 125))
        subtitle = "DISCORD BOT  |  VERSION 3.6"
        sub_box = draw.textbbox((0, 0), subtitle, font=small_font)
        draw.text((text_x + (title_width - (sub_box[2] - sub_box[0])) / 2, 118), subtitle, font=small_font, fill=(185, 205, 235))
        bar_width = int(430 * ((index % 12) / 11))
        draw.rounded_rectangle((145, 158, 575, 166), radius=4, fill=(48, 58, 86))
        if bar_width:
            draw.rounded_rectangle((145, 158, 145 + bar_width, 166), radius=4, fill=(235, 165, 75))
        frames.append(image)

    output = io.BytesIO()
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:], duration=85, loop=0, optimize=True)
    output.seek(0)
    return output


def make_help_embed(category="Genel"):
    if category == "Genel":
        embed = discord.Embed(title=f"Baklava1923 Bot {BOT_VERSION} | Yardım Merkezi", description="Bir kategori seçerek komutları ve kullanım örneklerini görüntüle. Eski komut önekleri korunmuştur.", color=discord.Color.green())
        for name, (summary, _) in HELP_CATEGORIES.items():
            embed.add_field(name=name, value=summary, inline=True)
        embed.set_footer(text="Kategori listesini aşağıdaki açılır menüden seç")
        return embed
    summary, commands_text = HELP_CATEGORIES.get(category, HELP_CATEGORIES["Başlangıç"])
    embed = discord.Embed(title=f"Yardım | {category}", description=commands_text, color=discord.Color.green())
    embed.set_footer(text=f"Baklava1923 Bot {BOT_VERSION} · Başka kategori seçebilirsin")
    return embed


class HelpCategorySelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label="Genel Bakış", value="Genel", description="Bütün yardım kategorileri", default=True)]
        options.extend(discord.SelectOption(label=name, value=name, description=summary[:100]) for name, (summary, _) in HELP_CATEGORIES.items())
        super().__init__(placeholder="Yardım kategorisi seç", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.author_id and not getattr(getattr(interaction.user, "guild_permissions", None), "administrator", False):
            await interaction.response.send_message("Bu yardım menüsünü başlatan kişi kullanabilir.", ephemeral=True)
            return
        await interaction.response.edit_message(embed=make_help_embed(self.values[0]), view=self.view)


class HelpCategoryView(discord.ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.add_item(HelpCategorySelect())


@bot.command(name="yardim", aliases=["help", "yardım", "31", "yardim31", "yardım31"])
async def yardim_command(ctx):
    # GIF ayrı mesaj olarak gönderilir; böylece yardım menüsünün hemen üstünde görünür.
    banner = make_bot_banner_gif()
    await ctx.send(file=discord.File(banner, filename="baklava1923_bot.gif"))
    await ctx.send(embed=make_help_embed(), view=HelpCategoryView(ctx.author.id))


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
    while joins and now - joins[0] > 20:
        joins.popleft()

    account_age = discord.utils.utcnow() - member.created_at
    age_days = max(0, account_age.days)
    if age_days < 3:
        await send_security_log(member.guild, f"Guvenlik uyarisi: yeni acilmis hesap katildi ({age_days} gunluk): {member} ({member.id})")

    # 20 saniyede 8 veya daha fazla katilim olursa raid dalgasi tespit edilir.
    # Otomatik toplu atma yapilmaz; yanlis pozitifleri onlemek icin yetkililere alarm gider.
    cooldown_until = raid_alert_cooldowns.get(guild_id, 0)
    if len(joins) >= 8 and now >= cooldown_until:
        raid_alert_cooldowns[guild_id] = now + 300
        alert = f"RAID UYARISI: {member.guild.name} sunucusuna 20 saniyede {len(joins)} uye katildi. Yeni katilimlari ve guvenlik loglarini kontrol edin. Bot kimseyi otomatik atmadı."
        await send_security_log(member.guild, alert)
        await notify_owner(alert)

    settings = ensure_guild_settings(guild_id)
    role_id = settings["autorole_id"]
    if role_id:
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
            peer_facts = bak_remember(f"bot:{message.author.id}", clean)
            answer = await bot.loop.run_in_executor(AI_EXECUTOR, get_baklavai_response, clean, [], history, peer_facts)
        if not answer:
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

    # Once prefix komutlarini calistir.
    await bot.process_commands(message)

    # Gercek sohbet mesajlarindan XP ve gorev ilerlemesi; komutlar XP kazandirmasin.
    if message.guild and message.content.strip() and not message.content.lstrip().lower().startswith(("!", "b!")):
        await process_member_activity(message)

    if message.guild and message.content.strip():
        member = message.author
        settings = ensure_guild_settings(message.guild.id)
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
            facts = bak_remember(message.author.id, clean)
            answer = await bot.loop.run_in_executor(AI_EXECUTOR, get_baklavai_response, clean, images, history, facts)
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
    embed.add_field(name="Baklavai", value="Hazır", inline=True)
    await ctx.send(embed=embed)


# -------------------- HATALAR VE BASLATMA --------------------
@bot.event
async def on_ready():
    print(f"{bot.user} olarak giris yapildi. Bot surumu: {BOT_VERSION}")
    for guild in bot.guilds:
        ensure_guild_settings(guild.id)
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
    keep_alive()
    bot.run(DISCORD_TOKEN)
