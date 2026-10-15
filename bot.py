import base64
import io
import os
import random
import re
import sqlite3
import time
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict, deque

import discord
import requests
from PIL import Image, ImageDraw, ImageFont
from discord.ext import commands
from keep_alive import keep_alive

# Baklava1923 Bot 3.0
OWNER_ID = 1358430002508726276
STARTING_BALANCE = 1000
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_TEXT_MODEL = "openai/gpt-oss-120b"
# Vision modeli Groq hesabinda etkin degilse Render'daki GROQ_VISION_MODEL degiskeninden degistir.
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
BOT_VERSION = "3.0"

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
    "normal": "Sen yardimsever ve dengeli bir Discord botusun. Turkce konus.",
    "komik": "Sen esprili ve sakaci bir Discord botusun. Turkce konus.",
    "ciddi": "Sen resmi, kisa ve oz cevaplar veren ciddi bir Discord botusun. Turkce konus.",
    "korkutucu": "Gizemli ve urkutucu bir uslupla konus ama gercek tehditlerde bulunma. Turkce konus.",
    "tartismaci": "Karsit goruslere sert ama hakaret etmeden cevap ver. Kucuk harfle yaz ve noktalama kullanma. Turkce konus.",
}

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


# -------------------- YAPAY ZEKA --------------------
def get_ai_response(user_message, image_data_urls=None):
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
            {"role": "system", "content": mode_prompts[current_mode]},
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


# -------------------- GLOBAL PAKLAVA EKONOMISI --------------------
MARKET_ITEMS = {
    "bakir_kasik": {"ad": "Bakir Kasik", "fiyat": 500, "bonus": 0.10, "aciklama": "Duello kazancini %10 artirir"},
    "gumus_catal": {"ad": "Gumus Catal", "fiyat": 1500, "bonus": 0.25, "aciklama": "Duello kazancini %25 artirir"},
    "altin_tepsi": {"ad": "Altin Tepsi", "fiyat": 4000, "bonus": 0.50, "aciklama": "Duello kazancini %50 artirir"},
    "elmas_firin": {"ad": "Elmas Firin", "fiyat": 10000, "bonus": 1.00, "aciklama": "Duello kazancini %100 artirir"},
    "paklava_tacı": {"ad": "Paklava Taci", "fiyat": 25000, "bonus": 2.00, "aciklama": "Duello kazancini %200 artirir"},
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


@bot.command(name="sa")
async def sa(ctx):
    await ctx.send("Ben sizi selamlarim")


@bot.command(name="napıyonuz", aliases=["napiyonuz", "napionuz", "napıyon"])
async def napıyonuz(ctx):
    await ctx.send("Takiliyoruz abi sen napıyon")


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
    try:
        owner = bot.get_user(OWNER_ID) or await bot.fetch_user(OWNER_ID)
        embed = discord.Embed(title="Yeni Oneri", description=metin[:4000], color=discord.Color.gold())
        embed.add_field(name="Gonderen", value=f"{ctx.author} ({ctx.author.id})", inline=False)
        embed.add_field(name="Sunucu", value=ctx.guild.name if ctx.guild else "DM", inline=False)
        await owner.send(embed=embed)
        await ctx.send("Onerin iletildi.")
    except (discord.Forbidden, discord.HTTPException):
        await ctx.send("Oneri iletilemedi. Bot sahibine DM gonderilemiyor.")


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
        random.seed(frame_index + percent)
        for _ in range(38):
            x, y = random.randint(12, width-12), random.randint(12, height-12)
            r = random.choice((1, 2, 3))
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
    if kisi1.id == kisi2.id:
        percent = 100
    else:
        # Aynı ikili için aynı yüzde: sonucu her çalıştırmada değiştirmez.
        low, high = sorted((kisi1.id, kisi2.id))
        percent = random.Random(f"{low}:{high}").randint(1, 100)
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
    embed = discord.Embed(title="Eğlence Komutları", color=discord.Color.magenta())
    embed.add_field(name="Oyunlar", value="`B!1vs1 @oyuncu`\n`B!tkm @oyuncu`\n`B!yazitura <yazi/tura>`", inline=False)
    embed.add_field(name="Etkileşim", value="`B!ship @kişi1 @kişi2`\n`B!uyum @oyuncu`\n`B!kapışma @oyuncu`\n`B!bahane`\n`B!itiraf <metin>`\n`B!zar [yüz]`\n`B!soru <metin>`\n`B!sa`\n`B!napıyonuz`", inline=False)
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
    embed = discord.Embed(title="Global Paklava Ekonomisi", color=discord.Color.gold())
    embed.description = "Bakiyen tüm sunucularda ortaktır."
    embed.add_field(name="Komutlar", value="`B!bakiye [@oyuncu]`\n`B!market`\n`B!satinal <eşya>`\n`B!envanter [@oyuncu]`", inline=False)
    await ctx.send(embed=embed)


@bot.command(name="yapayzeka", aliases=["ai"])
async def yapayzeka(ctx):
    embed = discord.Embed(title="Yapay Zeka Komutları", color=discord.Color.blurple())
    embed.description = "Beni etiketle veya bir mesajıma yanıt vererek sohbet et. Mesaja PNG, JPG ya da WEBP görseli ekleyebilirsin."
    embed.add_field(name="Komutlar", value="`B!mod normal`\n`B!mod komik`\n`B!mod ciddi`\n`B!mod korkutucu`\n`B!mod tartışmacı`\n`B!öneri <mesaj>`", inline=False)
    await ctx.send(embed=embed)


@bot.command(name="yardim", aliases=["help", "yardım", "31", "yardim31", "yardım31"])
async def yardim_command(ctx):
    embed = discord.Embed(title=f"Baklava1923 Bot 3.0 Yardım", description="Ayrıntılı komutlar için aşağıdaki kategorileri kullan.", color=discord.Color.green())
    embed.add_field(name="Eğlence", value="`B!eğlence`", inline=True)
    embed.add_field(name="Sunucu yönetimi", value="`B!sunucu`", inline=True)
    embed.add_field(name="Sunucu koruma", value="`B!sunucukoruma`", inline=True)
    embed.add_field(name="Ekonomi", value="`B!ekonomi`", inline=True)
    embed.add_field(name="Yapay zeka", value="`B!yapayzeka`", inline=True)
    embed.add_field(name="Diğer", value="`B!aktiflik` · `B!sa` · `B!napıyonuz` · `B!yardım31`", inline=False)
    await ctx.send(embed=embed)


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
            "`B!sunucubilgi` sunucu bilgisi\n`B!sunucuoluştur` tam sunucu şablonu\n`B!sunucukur` temel kanal kurulumu\n"
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
                await guild.create_voice_channel(name, reason="Baklava1923 Bot 3.0 temel kurulum")
            else:
                await guild.create_text_channel(name, reason="Baklava1923 Bot 3.0 temel kurulum")
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


@bot.command(name="sunucuoluştur", aliases=["sunucuolustur", "sunucu30"])
@commands.guild_only()
async def sunucu_olustur(ctx):
    if not admin_check(ctx) or not ctx.author.guild_permissions.manage_channels or not ctx.author.guild_permissions.manage_roles:
        await ctx.send("Bu komut için yönetici izni ve kanal/rol yönetme izinleri gerekiyor.")
        return
    me = ctx.guild.me
    if me is None or not me.guild_permissions.manage_channels or not me.guild_permissions.manage_roles:
        await ctx.send("Botta Kanalları Yönet ve Rolleri Yönet izinlerini açmalısın.")
        return
    status = await ctx.send("Baklava1923 Bot 3.0 sunucu kurulumuna başladı. Var olan hiçbir kanal veya rol silinmeyecek.")
    role_created = channel_created = category_created = 0
    existing_roles = {r.name.casefold() for r in ctx.guild.roles}
    for name, color in SERVER_ROLES:
        if name.casefold() in existing_roles:
            continue
        try:
            # Güvenlik için yeni roller yönetici yetkisi almaz.
            await ctx.guild.create_role(name=name, colour=discord.Colour(color), permissions=discord.Permissions.none(), reason="Baklava1923 Bot 3.0 sunucu şablonu")
            role_created += 1
        except (discord.Forbidden, discord.HTTPException):
            break
    existing_categories = {c.name.casefold(): c for c in ctx.guild.categories}
    existing_channels = {(c.name.casefold(), c.category_id, c.type) for c in ctx.guild.channels}
    for category_name, channel_names in SERVER_LAYOUT:
        category = existing_categories.get(category_name.casefold())
        if category is None:
            try:
                category = await ctx.guild.create_category(category_name, reason="Baklava1923 Bot 3.0 sunucu şablonu")
                category_created += 1
                existing_categories[category_name.casefold()] = category
            except (discord.Forbidden, discord.HTTPException):
                continue
        for channel_name in channel_names:
            kind = discord.ChannelType.voice if any(x in channel_name for x in ("Ses", "Müzik", "Oyun Odası", "AFK")) else discord.ChannelType.text
            if (channel_name.casefold(), category.id, kind) in existing_channels:
                continue
            try:
                if kind == discord.ChannelType.voice:
                    await ctx.guild.create_voice_channel(channel_name, category=category, reason="Baklava1923 Bot 3.0 sunucu şablonu")
                else:
                    await ctx.guild.create_text_channel(channel_name, category=category, reason="Baklava1923 Bot 3.0 sunucu şablonu")
                channel_created += 1
                existing_channels.add((channel_name.casefold(), category.id, kind))
            except (discord.Forbidden, discord.HTTPException):
                continue
    await status.edit(content=(
        "**Baklava1923 Bot 3.0 kurulumu tamamlandı**\n"
        f"Oluşturulan kategoriler: **{category_created}**\n"
        f"Oluşturulan kanallar: **{channel_created}**\n"
        f"Oluşturulan roller: **{role_created}**\n"
        "Var olan kanallar ve roller korundu. Yeni roller güvenlik için yönetici yetkisi almadan oluşturuldu. "
        "Kurulum tekrar çalıştırılırsa mevcut eşleşen öğeler atlanır."
    ))


@bot.command(name="sunucu")
@commands.guild_only()
async def sunucu(ctx):
    if not admin_check(ctx):
        await ctx.send("Bu komut icin yonetici izni gerekiyor.")
        return
    embed = discord.Embed(title="Baklava1923 Bot 3.0 | Sunucu Yonetimi", description="Asagidaki butonlardan yonetim islemi sec. Ayrintili komutlar icin `B!yardim` yaz.", color=discord.Color.blurple())
    embed.add_field(name="Yonetim", value="Sunucu bilgisi, temel kanal kurulumu ve komut listesi", inline=False)
    embed.add_field(name="Guvenlik", value="Spam korumasi, timeout ve guvenlik kayitlari", inline=False)
    await ctx.send(embed=embed, view=ServerMenuView(ctx.guild.id, ctx.author.id))


@bot.command(name="sunucukoruma")
@commands.guild_only()
@commands.has_guild_permissions(administrator=True)
async def sunucukoruma(ctx, islem: str = None, miktar: int = None):
    settings = ensure_guild_settings(ctx.guild.id)
    if islem is None:
        embed = discord.Embed(title="Guvenlik Merkezi", color=discord.Color.red())
        embed.description = (
            f"Koruma: {'Acik' if settings['protection_enabled'] else 'Kapali'}\n"
            f"Spam esigi: {settings['spam_limit']} mesaj / {settings['spam_window']} saniye\n"
            f"Timeout: {settings['timeout_minutes']} dakika\n"
            f"Kayit kanali: {f'<#{settings['log_channel_id']}>' if settings['log_channel_id'] else 'Ayarlanmamis'}\n\n"
            "Komutlar:\n`B!sunucukoruma ac`\n`B!sunucukoruma kapat`\n"
            "`B!sunucukoruma esik 6`\n`B!sunucukoruma sure 5`\n"
            "`B!sunuculog #kanal`"
        )
        await ctx.send(embed=embed)
        return
    islem = islem.lower()
    if islem in ("ac", "aç", "on"):
        set_guild_setting(ctx.guild.id, "protection_enabled", 1)
        await ctx.send("Spam korumasi acildi.")
    elif islem in ("kapat", "off"):
        set_guild_setting(ctx.guild.id, "protection_enabled", 0)
        await ctx.send("Spam korumasi kapatildi.")
    elif islem in ("esik", "eşik"):
        if miktar is None or not 3 <= miktar <= 20:
            await ctx.send("Esik 3 ile 20 arasinda olmali. Ornek: `B!sunucukoruma esik 6`")
            return
        set_guild_setting(ctx.guild.id, "spam_limit", miktar)
        await ctx.send(f"Spam esigi {miktar} mesaj olarak ayarlandi.")
    elif islem in ("sure", "süre"):
        if miktar is None or not 1 <= miktar <= 1440:
            await ctx.send("Sure 1 ile 1440 dakika arasinda olmali.")
            return
        set_guild_setting(ctx.guild.id, "timeout_minutes", miktar)
        await ctx.send(f"Spam timeout suresi {miktar} dakika olarak ayarlandi.")
    else:
        await ctx.send("Kullanim: `B!sunucukoruma [ac/kapat/esik/sure] [miktar]`")


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


@bot.event
async def on_member_join(member):
    settings = ensure_guild_settings(member.guild.id)
    role_id = settings["autorole_id"]
    if role_id:
        role = member.guild.get_role(role_id)
        if role and member.guild.me and role < member.guild.me.top_role:
            try:
                await member.add_roles(role, reason="Baklava1923 Bot 3.0 otomatik rol")
            except (discord.Forbidden, discord.HTTPException):
                await send_security_log(member.guild, f"Otomatik rol verilemedi: {member.mention}")
    await send_security_log(member.guild, f"Sunucuya yeni uye katildi: {member.mention} ({member.id})")



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


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Once prefix komutlarini calistir.
    await bot.process_commands(message)

    if message.guild and not message.author.guild_permissions.administrator:
        settings = ensure_guild_settings(message.guild.id)
        if settings["protection_enabled"] and message.content.strip():
            key = (message.guild.id, message.author.id)
            now = time.time()
            queue = spam_messages[key]
            queue.append(now)
            while queue and now - queue[0] > settings["spam_window"]:
                queue.popleft()
            if len(queue) >= settings["spam_limit"]:
                if key not in spam_warned:
                    spam_warned.add(key)
                    try:
                        await message.channel.send(f"{message.author.mention} lutfen spam yapma. Devam edersen gecici olarak susturulacaksin.")
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                    await send_security_log(message.guild, f"Spam uyarisi: {message.author} ({message.author.id})")
                else:
                    member = message.author
                    if message.guild.me and message.guild.me.guild_permissions.moderate_members:
                        try:
                            await member.timeout(discord.utils.utcnow() + timedelta(minutes=settings["timeout_minutes"]), reason="Tekrarlanan spam")
                            await message.channel.send(f"{member.mention} tekrar spam yaptigi icin {settings['timeout_minutes']} dakika susturuldu.")
                            await send_security_log(message.guild, f"Spam timeout: {member} — {settings['timeout_minutes']} dakika")
                            await notify_owner(f"Baklava1923 Bot 3.0 spam islemi\nSunucu: {message.guild.name}\nUye: {member} ({member.id})\nSure: {settings['timeout_minutes']} dakika")
                        except (discord.Forbidden, discord.HTTPException) as exc:
                            await send_security_log(message.guild, f"Timeout uygulanamadi: {member} — {exc}")
                    else:
                        await send_security_log(message.guild, f"Spam tespit edildi ancak botta Uyelere Zaman Asimi Uygula izni yok: {member}")
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
            answer = await bot.loop.run_in_executor(AI_EXECUTOR, get_ai_response, clean, images)
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


# -------------------- HATALAR VE BASLATMA --------------------
@bot.event
async def on_ready():
    print(f"{bot.user} olarak giris yapildi. Bot surumu: {BOT_VERSION}")
    for guild in bot.guilds:
        ensure_guild_settings(guild.id)
        for member in guild.members:
            if not member.bot and is_active(member.status):
                start_tracking(guild.id, member.id)
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
    else:
        print(f"KOMUT HATASI: {type(error).__name__}: {error}")
        await ctx.send("Komut calisirken hata olustu. Render loglarini kontrol et.")


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN ortam degiskeni eksik. Render > Environment bolumune ekle.")
    if not GROQ_API_KEY:
        print("UYARI: GROQ_API_KEY eksik; bot acilir ancak AI cevaplari calismaz.")
    keep_alive()
    bot.run(DISCORD_TOKEN)
