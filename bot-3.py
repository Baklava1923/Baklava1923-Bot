import base64
import os
import random
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import discord
import requests
from discord.ext import commands
from keep_alive import keep_alive

# -------------------- AYARLAR --------------------
OWNER_ID = 1358430002508726276
STARTING_BALANCE = 1000
UNLIMITED_BALANCE = 10**15  # Sahip hesabinda sadece dahili hesaplamalar icin
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_TEXT_MODEL = "openai/gpt-oss-120b"
GROQ_VISION_MODEL = "qwen/qwen3.8-27b"

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

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

current_mode = "normal"
mode_prompts = {
    "normal": "Sen yardimsever ve dengeli bir Discord botusun. Turkce konus.",
    "komik": "Sen esprili ve sakaci bir Discord botusun. Turkce konus.",
    "ciddi": "Sen resmi, kisa ve oz cevaplar veren ciddi bir Discord botusun. Turkce konus.",
    "korkutucu": "Gizemli ve urkutucu bir uslupla konus ama gercek tehditlerde bulunma. Turkce konus.",
    "tartismaci": "Karsit goruslere sert ama hakaret etmeden cevap ver. Kucuk harfle yaz ve noktalama kullanma. Turkce konus.",
}

# requests cagrisinin Discord'un event loop'unu kilitlememesi icin ayri thread havuzu.
AI_EXECUTOR = ThreadPoolExecutor(max_workers=4)


# -------------------- VERITABANI --------------------
db = sqlite3.connect("aktiflik.db", check_same_thread=False)
cursor = db.cursor()

cursor.execute(
    """CREATE TABLE IF NOT EXISTS activity (
        guild_id INTEGER,
        user_id INTEGER,
        start_time REAL,
        end_time REAL
    )"""
)
cursor.execute(
    """CREATE TABLE IF NOT EXISTS economy (
        guild_id INTEGER,
        user_id INTEGER,
        balance INTEGER DEFAULT 1000,
        PRIMARY KEY (guild_id, user_id)
    )"""
)
cursor.execute(
    """CREATE TABLE IF NOT EXISTS inventory (
        guild_id INTEGER,
        user_id INTEGER,
        item_key TEXT,
        PRIMARY KEY (guild_id, user_id, item_key)
    )"""
)
db.commit()


def migrate_economy_to_global():
    """Eski sunucu bazli bakiyeleri global kayda tasir.

    Bir uye birden fazla sunucuda kayitliysa en yuksek eski bakiyesi korunur.
    Eski envanter esyalari da tek global envantere birlestirilir.
    """
    cursor.execute("SELECT user_id, MAX(balance) FROM economy GROUP BY user_id")
    balances = cursor.fetchall()

    cursor.execute("SELECT DISTINCT user_id, item_key FROM inventory")
    items = cursor.fetchall()

    cursor.execute("DELETE FROM economy")
    cursor.executemany(
        "INSERT INTO economy (guild_id, user_id, balance) VALUES (0, ?, ?)",
        [(user_id, max(0, int(balance or 0))) for user_id, balance in balances],
    )

    cursor.execute("DELETE FROM inventory")
    cursor.executemany(
        "INSERT OR IGNORE INTO inventory (guild_id, user_id, item_key) VALUES (0, ?, ?)",
        items,
    )
    db.commit()


migrate_economy_to_global()
active_users = {}


# -------------------- GROQ YAPAY ZEKA --------------------
def get_ai_response(user_message, image_data_urls=None):
    if not GROQ_API_KEY:
        return "Yapay zeka su an ayarli degil. Render ortam degiskenlerinde GROQ_API_KEY eksik."

    image_data_urls = image_data_urls or []
    model = GROQ_VISION_MODEL if image_data_urls else GROQ_TEXT_MODEL
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    if image_data_urls:
        user_content = [{"type": "text", "text": user_message or "Bu gorseli incele ve Turkce anlat."}]
        for data_url in image_data_urls[:3]:
            user_content.append({"type": "image_url", "image_url": {"url": data_url}})
    else:
        user_content = user_message

    data = {
        "model": model,
        "messages": [
            {"role": "system", "content": mode_prompts[current_mode]},
            {"role": "user", "content": user_content},
        ],
        "max_completion_tokens": 900,
        "temperature": 0.7,
    }

    try:
        response = requests.post(GROQ_URL, headers=headers, json=data, timeout=45)
        try:
            result = response.json()
        except ValueError:
            print(f"GROQ JSON HATASI: {response.text[:300]}")
            return "Yapay zeka beklenmedik bir yanit verdi. Birazdan tekrar dene."

        if response.status_code >= 400:
            print(f"GROQ API HATASI {response.status_code}: {result}")
            error_obj = result.get("error", {}) if isinstance(result, dict) else {}
            error_text = error_obj.get("message", "Bilinmeyen API hatasi")
            if image_data_urls:
                return "Gorseli su an analiz edemedim. Daha kucuk bir PNG, JPG veya WEBP gorseliyle tekrar dene."
            return f"Yapay zeka API hatasi: {error_text[:250]}"

        return result["choices"][0]["message"]["content"].strip()
    except requests.exceptions.RequestException as exc:
        print(f"GROQ ISTEK HATASI: {exc}")
        return "Su an yapay zeka servisine ulasamiyorum. Birazdan tekrar dene."
    except (KeyError, IndexError, TypeError) as exc:
        print(f"GROQ YANIT HATASI: {exc}")
        return "Yapay zeka yaniti okunamadi. Birazdan tekrar dene."


# -------------------- GLOBAL PAKLAVA EKONOMISI --------------------
MARKET_ITEMS = {
    "bakir_kasik": {"ad": "Bakir Kasik", "fiyat": 500, "bonus": 0.10, "aciklama": "Düello kazancini %10 artirir"},
    "gumus_catal": {"ad": "Gumus Catal", "fiyat": 1500, "bonus": 0.25, "aciklama": "Düello kazancini %25 artirir"},
    "altin_tepsi": {"ad": "Altin Tepsi", "fiyat": 4000, "bonus": 0.50, "aciklama": "Düello kazancini %50 artirir"},
    "elmas_firin": {"ad": "Elmas Firin", "fiyat": 10000, "bonus": 1.00, "aciklama": "Düello kazancini %100 artirir"},
    "paklava_tacı": {"ad": "Paklava Taci", "fiyat": 25000, "bonus": 2.00, "aciklama": "Düello kazancini %200 artirir, en nadir esya"},
}


def balance_label(user_id, amount=None):
    if user_id == OWNER_ID:
        return "Sinirsiz"
    if amount is None:
        amount = STARTING_BALANCE
    return f"{amount:,}".replace(",", ".")


def get_balance(guild_id, user_id):
    # guild_id geriye uyumluluk icin duruyor; ekonomik bakiye her yerde globadir.
    if user_id == OWNER_ID:
        return UNLIMITED_BALANCE
    cursor.execute("SELECT balance FROM economy WHERE guild_id = 0 AND user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row is None:
        cursor.execute(
            "INSERT OR IGNORE INTO economy (guild_id, user_id, balance) VALUES (0, ?, ?)",
            (user_id, STARTING_BALANCE),
        )
        db.commit()
        return STARTING_BALANCE
    return int(row[0])


def set_balance(guild_id, user_id, new_balance):
    if user_id == OWNER_ID:
        return UNLIMITED_BALANCE
    get_balance(guild_id, user_id)
    cursor.execute(
        "UPDATE economy SET balance = ? WHERE guild_id = 0 AND user_id = ?",
        (max(0, int(new_balance)), user_id),
    )
    db.commit()
    return get_balance(guild_id, user_id)


def add_balance(guild_id, user_id, amount):
    if user_id == OWNER_ID:
        return UNLIMITED_BALANCE
    current = get_balance(guild_id, user_id)
    return set_balance(guild_id, user_id, current + int(amount))


def get_inventory(guild_id, user_id):
    cursor.execute(
        "SELECT item_key FROM inventory WHERE guild_id = 0 AND user_id = ?",
        (user_id,),
    )
    return [row[0] for row in cursor.fetchall()]


def get_duel_bonus(guild_id, user_id):
    bonus = 0.0
    for item_key in get_inventory(guild_id, user_id):
        item = MARKET_ITEMS.get(item_key)
        if item:
            bonus += item["bonus"]
    return bonus


@bot.command(name="bakiye", aliases=["cuzdan", "cüzdan"])
async def bakiye(ctx, oyuncu: discord.Member = None):
    hedef = oyuncu or ctx.author
    miktar = get_balance(getattr(ctx.guild, "id", 0), hedef.id)
    metin = balance_label(hedef.id, miktar)
    embed = discord.Embed(
        title="Paklava Cuzdani",
        description=f"{hedef.mention} → **{metin}** paklava",
        color=discord.Color.orange(),
    )
    await ctx.send(embed=embed)


@bot.command(name="yazitura", aliases=["yazıtura"])
async def yazitura(ctx, secim: str = None):
    # Bahis veya bakiye degisikligi yapmayan, sadece eglence amacli tahmin oyunu.
    if secim is None:
        await ctx.send("Kullanim: `B!yazitura <yazi/tura>` — bu oyun bakiyeni etkilemez.")
        return

    secim = secim.lower().strip()
    if secim not in ("yazi", "yazı", "tura"):
        await ctx.send("Secimin `yazi` veya `tura` olmali.")
        return

    secim = "yazi" if secim in ("yazi", "yazı") else "tura"
    sonuc = random.choice(["yazi", "tura"])
    if secim == sonuc:
        await ctx.send(f"Para **{sonuc}** geldi. Dogru tahmin ettin.")
    else:
        await ctx.send(f"Para **{sonuc}** geldi. Bu sefer tahminin tutmadi.")

@bot.command(name="market")
async def market(ctx):
    embed = discord.Embed(
        title="Paklava Market",
        description="Satin almak icin: `B!satinal <esya adi>`",
        color=discord.Color.gold(),
    )
    for key, item in MARKET_ITEMS.items():
        embed.add_field(
            name=f"{item['ad']} — {item['fiyat']} paklava",
            value=f"{item['aciklama']}\nKomut: `B!satinal {key}`",
            inline=False,
        )
    await ctx.send(embed=embed)


@bot.command(name="satinal", aliases=["satınal"])
async def satinal(ctx, *, esya: str = None):
    if esya is None:
        await ctx.send("Hangi esyayi almak istiyorsun. `B!market` ile listeye bak.")
        return
    esya_key = esya.lower().strip().replace(" ", "_")
    if esya_key not in MARKET_ITEMS:
        await ctx.send("Boyle bir esya yok. `B!market` ile listeye bak.")
        return
    guild_id = getattr(ctx.guild, "id", 0)
    if esya_key in get_inventory(guild_id, ctx.author.id):
        await ctx.send("Bu esyaya zaten sahipsin.")
        return
    item = MARKET_ITEMS[esya_key]
    current = get_balance(guild_id, ctx.author.id)
    if current < item["fiyat"]:
        await ctx.send(
            f"Yeterli paklavan yok. {item['ad']} icin **{item['fiyat']}** gerekiyor. "
            f"Bakiyen: **{balance_label(ctx.author.id, current)}** paklava"
        )
        return
    add_balance(guild_id, ctx.author.id, -item["fiyat"])
    cursor.execute(
        "INSERT OR IGNORE INTO inventory (guild_id, user_id, item_key) VALUES (0, ?, ?)",
        (ctx.author.id, esya_key),
    )
    db.commit()
    kalan = balance_label(ctx.author.id, get_balance(guild_id, ctx.author.id))
    await ctx.send(f"{item['ad']} satin alindi. Kalan bakiyen: **{kalan}** paklava")


@bot.command(name="envanter")
async def envanter(ctx, oyuncu: discord.Member = None):
    hedef = oyuncu or ctx.author
    sahip_olunanlar = get_inventory(getattr(ctx.guild, "id", 0), hedef.id)
    embed = discord.Embed(title=f"{hedef.display_name} envanteri", color=discord.Color.purple())
    if not sahip_olunanlar:
        embed.description = "Hic esyasi yok. `B!market` ile bir seyler alabilir."
    else:
        satirlar = [MARKET_ITEMS[key]["ad"] for key in sahip_olunanlar if key in MARKET_ITEMS]
        toplam_bonus = get_duel_bonus(getattr(ctx.guild, "id", 0), hedef.id)
        embed.description = "\n".join(satirlar) + f"\n\nToplam duello kazanc bonusi: **+%{int(toplam_bonus * 100)}**"
    await ctx.send(embed=embed)


# -------------------- AKTIFLIK TAKIBI --------------------
def is_active(status):
    return status in (discord.Status.online, discord.Status.idle, discord.Status.dnd)


def start_tracking(guild_id, user_id):
    key = (guild_id, user_id)
    if key not in active_users:
        active_users[key] = time.time()


def stop_tracking(guild_id, user_id):
    key = (guild_id, user_id)
    if key in active_users:
        start_time = active_users.pop(key)
        cursor.execute(
            "INSERT INTO activity (guild_id, user_id, start_time, end_time) VALUES (?, ?, ?, ?)",
            (guild_id, user_id, start_time, time.time()),
        )
        db.commit()


@bot.event
async def on_presence_update(before, after):
    if after.bot or after.guild is None:
        return
    was_active = is_active(before.status)
    is_now_active = is_active(after.status)
    if not was_active and is_now_active:
        start_tracking(after.guild.id, after.id)
    elif was_active and not is_now_active:
        stop_tracking(after.guild.id, after.id)


@bot.event
async def on_ready():
    print(f"{bot.user} olarak giris yapildi.")
    for guild in bot.guilds:
        for member in guild.members:
            if not member.bot and is_active(member.status):
                start_tracking(guild.id, member.id)


def get_activity(guild_id, user_id, days):
    now = time.time()
    beginning = now - (days * 86400)
    total = 0
    cursor.execute(
        "SELECT start_time, end_time FROM activity WHERE guild_id = ? AND user_id = ? AND end_time >= ?",
        (guild_id, user_id, beginning),
    )
    for start, end in cursor.fetchall():
        real_start = max(start, beginning)
        real_end = min(end, now)
        if real_end > real_start:
            total += real_end - real_start
    key = (guild_id, user_id)
    if key in active_users:
        start = max(active_users[key], beginning)
        if now > start:
            total += now - start
    return total


def format_time(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours} saat {minutes} dakika"


async def show_activity(ctx, days):
    guild = getattr(ctx, "guild", None)
    if guild is None:
        await ctx.send("Bu komut sadece sunucularda kullanilabilir.")
        return
    results = []
    for member in guild.members:
        if member.bot:
            continue
        seconds = get_activity(guild.id, member.id, days)
        if seconds > 0:
            results.append((member, seconds))
    results.sort(key=lambda item: item[1], reverse=True)
    results = results[:15]
    embed = discord.Embed(
        title="Aktiflik Tablosu",
        description=f"Son {days} gun",
        color=discord.Color.blue(),
    )
    if not results:
        embed.description = f"Son {days} gun icinde kayitli aktiflik bulunamadi."
    else:
        lines = [f"**{i}.** {member.display_name} — {format_time(seconds)}" for i, (member, seconds) in enumerate(results, 1)]
        embed.add_field(name="En aktif uyeler", value="\n".join(lines), inline=False)
    embed.set_footer(text="Aktiflik; cevrimici, bosta ve rahatsiz etmeyin durumlarina gore hesaplanir.")
    await ctx.send(embed=embed)


class ActivityView(discord.ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=60)
        self.author_id = author_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Bu menu sadece komutu kullanan kisiye acik.", ephemeral=True)
            return False
        return True

    async def show_selected(self, interaction, days):
        await interaction.response.defer()
        await show_activity(interaction.channel, days)
        try:
            await interaction.message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    @discord.ui.button(label="7 Gun", style=discord.ButtonStyle.primary)
    async def seven_days(self, interaction, button):
        await self.show_selected(interaction, 7)

    @discord.ui.button(label="30 Gun", style=discord.ButtonStyle.primary)
    async def thirty_days(self, interaction, button):
        await self.show_selected(interaction, 30)

    @discord.ui.button(label="60 Gun", style=discord.ButtonStyle.primary)
    async def sixty_days(self, interaction, button):
        await self.show_selected(interaction, 60)

    @discord.ui.button(label="90 Gun", style=discord.ButtonStyle.primary)
    async def ninety_days(self, interaction, button):
        await self.show_selected(interaction, 90)


@bot.command(name="aktiflik")
async def aktiflik(ctx):
    embed = discord.Embed(
        title="Aktiflik Suresi",
        description="Hangi zaman araligindaki aktifligi gormek istiyorsun?\nAsagidan bir sure sec.",
        color=discord.Color.blue(),
    )
    await ctx.send(embed=embed, view=ActivityView(ctx.author.id))


# -------------------- NORMAL KOMUTLAR --------------------
# Merhaba komutu bilerek kaldirildi.


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
        return
    secim = secim.lower().strip()
    if secim in mode_prompts:
        current_mode = secim
        await ctx.send(f"Mod degistirildi: **{secim}**")
    else:
        await ctx.send("Gecerli modlar: normal, komik, ciddi, korkutucu, tartismaci")


@bot.command(name="soru")
async def soru(ctx, *, soru_metni: str = None):
    if soru_metni is None:
        await ctx.send("Bir soru yazmalisin. Ornek: `B!soru Bugun sansli miyim` ")
        return
    cevaplar = [
        "He, bunu nereden bildin?",
        "Abov, ilgincmis.",
        "Bence tekrar dusun.",
        "He kesin, kaynak: Paklava Akademisi.",
        "Su an tahmin motorum mola veriyor.",
        "Cevap biraz gizemli kaldi.",
        "Evet.",
        "Hayir gibi duruyor.",
        "Isaretler biraz belirsiz, tekrar sor.",
        "Simdi cevap veremem.",
        "Kaynaklarima gore hayir.",
        "Cok supheli.",
    ]
    embed = discord.Embed(title="Sihirli Paklava", color=discord.Color.purple())
    embed.add_field(name="Soru", value=soru_metni[:1024], inline=False)
    embed.add_field(name="Cevap", value=random.choice(cevaplar), inline=False)
    await ctx.send(embed=embed)


@bot.command(name="öneri", aliases=["oneri"])
async def oneri(ctx, *, oneri_metni: str = None):
    if oneri_metni is None:
        await ctx.send("Bir onerini yaz. Ornek: `B!oneri yeni bir komut eklensin`")
        return
    try:
        owner = await bot.fetch_user(OWNER_ID)
        embed = discord.Embed(title="Yeni Oneri", description=oneri_metni[:4000], color=discord.Color.gold())
        embed.add_field(name="Gonderen", value=f"{ctx.author} ({ctx.author.id})", inline=False)
        embed.add_field(name="Sunucu", value=ctx.guild.name if ctx.guild else "DM", inline=False)
        await owner.send(embed=embed)
        await ctx.send("Onerin iletildi, tesekkurler.")
    except discord.Forbidden:
        await ctx.send("Oneri iletilemedi. Botun sahibine DM gonderilemiyor.")
    except discord.HTTPException:
        await ctx.send("Oneri gonderilirken bir hata olustu.")


# -------------------- TAS KAGIT MAKAS --------------------
class TKMGameView(discord.ui.View):
    def __init__(self, player1, player2):
        super().__init__(timeout=120)
        self.player1 = player1
        self.player2 = player2
        self.choices = {}

    async def interaction_check(self, interaction):
        if interaction.user.id not in (self.player1.id, self.player2.id):
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

        p1_choice = self.choices[self.player1.id]
        p2_choice = self.choices[self.player2.id]
        if p1_choice == p2_choice:
            result = "Berabere"
            winner_id = None
        elif (
            (p1_choice == "Tas" and p2_choice == "Makas")
            or (p1_choice == "Kagit" and p2_choice == "Tas")
            or (p1_choice == "Makas" and p2_choice == "Kagit")
        ):
            result = f"{self.player1.mention} kazandi"
            winner_id = self.player1.id
        else:
            result = f"{self.player2.mention} kazandi"
            winner_id = self.player2.id

        if winner_id:
            guild_id = interaction.guild_id or 0
            reward = round(100 * (1 + get_duel_bonus(guild_id, winner_id)))
            add_balance(guild_id, winner_id, reward)
            result += f"\nKazananin odulu: **{reward} paklava**"

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=(
                "**Tas Kagit Makas Sonucu**\n\n"
                f"{self.player1.mention}: **{p1_choice}**\n"
                f"{self.player2.mention}: **{p2_choice}**\n\n"
                f"**{result}**"
            ),
            view=self,
        )
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
        self.challenger = challenger
        self.opponent = opponent

    @discord.ui.button(label="Kabul Et", style=discord.ButtonStyle.success)
    async def accept(self, interaction, button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("Bu daveti sadece etiketlenen oyuncu kabul edebilir.", ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"{self.challenger.mention} ve {self.opponent.mention}\n\nIkiniz de asagidan seciminizi yapin.",
            view=TKMGameView(self.challenger, self.opponent),
        )
        self.stop()

    @discord.ui.button(label="Reddet", style=discord.ButtonStyle.danger)
    async def decline(self, interaction, button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("Bu daveti sadece etiketlenen oyuncu reddedebilir.", ephemeral=True)
            return
        await interaction.response.edit_message(content=f"{self.opponent.mention} oyunu reddetti.", view=None)
        self.stop()


@bot.command(name="tkm")
async def tkm(ctx, oyuncu: discord.Member = None):
    if oyuncu is None:
        await ctx.send("Bir oyuncuyu etiketlemelisin. Ornek: `B!tkm @oyuncu`")
        return
    if oyuncu.bot:
        await ctx.send("Botlarla Tas Kagit Makas oynayamazsin.")
        return
    if oyuncu.id == ctx.author.id:
        await ctx.send("Kendinle Tas Kagit Makas oynayamazsin.")
        return
    await ctx.send(
        f"{oyuncu.mention}\n\n{ctx.author.mention} seninle Tas Kagit Makas oynamak istiyor. Kabul ediyor musun?",
        view=TKMInviteView(ctx.author, oyuncu),
    )


# -------------------- 1VS1 DUELLO --------------------
DUEL_MAX_HP = 500
DUEL_ULTRA_COST = 100
active_duels = {}


def make_hp_bar(hp, max_hp=DUEL_MAX_HP):
    filled = round((hp / max_hp) * 10)
    filled = max(0, min(10, filled))
    return "🟩" * filled + "⬛" * (10 - filled)


class DuelGameView(discord.ui.View):
    def __init__(self, player1, player2, channel_id):
        super().__init__(timeout=300)
        self.channel_id = channel_id
        self.order = [player1.id, player2.id]
        self.turn_index = 0
        self.players = {
            player1.id: {"member": player1, "hp": DUEL_MAX_HP, "power": 0, "shield": False},
            player2.id: {"member": player2, "hp": DUEL_MAX_HP, "power": 0, "shield": False},
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
        embed = discord.Embed(title="1vs1 Duello", color=discord.Color.red())
        if log_line:
            embed.description = log_line
        for user_id in self.order:
            player = self.players[user_id]
            value = (
                f"Can: {player['hp']}/{DUEL_MAX_HP}\n"
                f"{make_hp_bar(player['hp'])}\n"
                f"Guc: {player['power']}/{DUEL_ULTRA_COST}"
            )
            if player["shield"]:
                value += "\nSavunma kalkani aktif"
            embed.add_field(name=player["member"].display_name, value=value, inline=True)
        embed.set_footer(text=f"Sira: {self.players[self.current_player_id]['member'].display_name}")
        return embed

    async def end_duel(self, interaction, winner_id, loser_id, reason):
        active_duels.pop(self.channel_id, None)
        for item in self.children:
            item.disabled = True
        guild_id = interaction.guild.id if interaction.guild else 0
        base_reward = random.randint(50, 150)
        bonus = get_duel_bonus(guild_id, winner_id)
        reward = round(base_reward * (1 + bonus))
        add_balance(guild_id, winner_id, reward)

        embed = discord.Embed(title="Duello Bitti", description=reason, color=discord.Color.gold())
        embed.add_field(name="Kazanan", value=self.players[winner_id]["member"].mention)
        reward_text = f"+{reward} paklava"
        if bonus > 0:
            reward_text += f" (esya bonusi: +%{int(bonus * 100)})"
        embed.add_field(name="Odul", value=reward_text, inline=False)
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    async def resolve(self, interaction, action):
        actor_id = interaction.user.id
        target_id = self.other_id(actor_id)
        actor = self.players[actor_id]
        target = self.players[target_id]
        log = ""

        if action == "terlik":
            damage = random.randint(50, 99)
            blocked = ""
            if target["shield"]:
                damage = round(damage * 0.4)
                target["shield"] = False
                blocked = " Savunma kalkani hasari azaltti."
            target["hp"] = max(0, target["hp"] - damage)
            log = f"{actor['member'].mention}, {target['member'].mention} oyuncusuna {damage} hasar verdi.{blocked}"

        elif action == "topla":
            gain = random.randint(30, 60)
            actor["power"] = min(DUEL_ULTRA_COST, actor["power"] + gain)
            log = f"{actor['member'].mention} guc topladi (+{gain})."

        elif action == "ultra":
            if actor["power"] < DUEL_ULTRA_COST:
                log = f"{actor['member'].mention} icin yeterli guc yok. Once guc topla."
            else:
                damage = random.randint(200, 300)
                blocked = ""
                if target["shield"]:
                    damage = round(damage * 0.4)
                    target["shield"] = False
                    blocked = " Savunma kalkani hasari azaltti."
                actor["power"] = 0
                target["hp"] = max(0, target["hp"] - damage)
                log = f"{actor['member'].mention} guclu bir ultra saldiri yapti ve {damage} hasar verdi.{blocked}"

        elif action == "can":
            heal = random.randint(30, 100)
            old_hp = actor["hp"]
            actor["hp"] = min(DUEL_MAX_HP, actor["hp"] + heal)
            actual_heal = actor["hp"] - old_hp
            log = f"{actor['member'].mention} {actual_heal} can yeniledi."

        elif action == "seddi":
            actor["shield"] = True
            log = f"{actor['member'].mention} savunma kalkani kurdu. Sonraki saldiri daha az hasar verir."

        elif action == "pes":
            await self.end_duel(interaction, target_id, actor_id, f"{actor['member'].mention} pes etti.")
            return

        if target["hp"] <= 0:
            await self.end_duel(interaction, actor_id, target_id, f"{log}\n\n{target['member'].mention} yenildi.")
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
        self.challenger = challenger
        self.opponent = opponent

    @discord.ui.button(label="Kabul Et", style=discord.ButtonStyle.success)
    async def accept(self, interaction, button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("Bu daveti sadece etiketlenen oyuncu kabul edebilir.", ephemeral=True)
            return
        game_view = DuelGameView(self.challenger, self.opponent, interaction.channel.id)
        active_duels[interaction.channel.id] = game_view
        await interaction.response.edit_message(content=None, embed=game_view.build_embed("Duello basladi."), view=game_view)
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
    if oyuncu is None:
        await ctx.send("Bir oyuncuyu etiketlemelisin. Ornek: `B!1vs1 @oyuncu`")
        return
    if oyuncu.bot:
        await ctx.send("Bir botla duello yapamazsin.")
        return
    if oyuncu.id == ctx.author.id:
        await ctx.send("Kendinle duello yapamazsin.")
        return
    if ctx.channel.id in active_duels:
        await ctx.send("Bu kanalda zaten aktif bir duello var.")
        return
    await ctx.send(f"{oyuncu.mention}\n\n{ctx.author.mention} seni duelloya davet ediyor.", view=DuelInviteView(ctx.author, oyuncu))


# -------------------- EG LENCE VE YARDIM --------------------
@bot.command(name="eğlence", aliases=["eglence"])
async def eglence(ctx):
    embed = discord.Embed(title="Eglenceli Komutlar", description="Duello ve paklava ekonomisi komutlari", color=discord.Color.magenta())
    embed.add_field(name="Duello", value="`B!1vs1 @oyuncu` — can/hasar tabanli duello\n`B!tkm @oyuncu` — Tas Kagit Makas", inline=False)
    embed.add_field(
        name="Paklava Ekonomisi",
        value=(
            "`B!bakiye [@oyuncu]` — bakiyeyi gor\n"
            "`B!yazitura <miktar> <yazi/tura>` — sansta sanal paklava\n"
            "`B!market` — esyalari listele\n"
            "`B!satinal <esya>` — esya satin al\n"
            "`B!envanter [@oyuncu]` — esyalari ve bonuslari gor"
        ),
        inline=False,
    )
    embed.add_field(name="Diger", value="`B!soru <metin>` — Sihirli Paklava'ya soru sor", inline=False)
    embed.set_footer(text="Tum komutlar icin: B!yardim")
    if bot.user:
        embed.set_author(name=bot.user.name, icon_url=bot.user.display_avatar.url)
    await ctx.send(embed=embed)


@bot.command(name="yardim", aliases=["help", "yardım", "31", "yardim31", "yardım31"])
async def yardim_command(ctx):
    embed = discord.Embed(title="Paklava1923 Bot Yardim", color=discord.Color.green())
    fields = [
        ("B!yardim / B!31", "Yardim mesajini gosterir"),
        ("B!mod <isim>", "Konusma tarzi: normal, komik, ciddi, korkutucu, tartismaci"),
        ("B!soru <metin>", "Sihirli Paklava'ya bir soru sor"),
        ("B!aktiflik", "Sunucu aktiflik tablosu (7, 30, 60 veya 90 gun)"),
        ("B!öneri <metin>", "Bot sahibine onerini gonder"),
        ("B!tkm @oyuncu", "Bir oyuncuya Tas Kagit Makas daveti gonder"),
        ("B!1vs1 @oyuncu", "Bir oyuncuyla sira tabanli duello yap"),
        ("B!bakiye [@oyuncu]", "Global paklava bakiyesini gor"),
        ("B!yazitura <miktar> <yazi/tura>", "Sanal paklava ile yazi-tura"),
        ("B!market", "Satin alinabilir esyalari gosterir"),
        ("B!satinal <esya>", "Bir esya satin al"),
        ("B!envanter [@oyuncu]", "Global envanteri gosterir"),
        ("B!eğlence", "Eglence komutlarini listeler"),
        ("Yapay zeka sohbeti", "Beni etiketle veya mesajima yanit ver. Gorsel analiz icin de beni etiketleyip PNG, JPG veya WEBP gorseli gonder."),
    ]
    for name, value in fields:
        embed.add_field(name=name, value=value, inline=False)
    if bot.user:
        embed.set_author(name=bot.user.name, icon_url=bot.user.display_avatar.url)
    await ctx.send(embed=embed)


# -------------------- GORSEL EKLERINI OKU --------------------
MAX_IMAGE_BYTES = 7 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}


async def read_image_attachments(message):
    image_data_urls = []
    errors = []
    for attachment in message.attachments:
        mime = (attachment.content_type or "").split(";")[0].lower()
        if mime not in ALLOWED_IMAGE_TYPES:
            ext = os.path.splitext(attachment.filename.lower())[1]
            mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(ext, "")
        if not mime:
            continue
        if attachment.size > MAX_IMAGE_BYTES:
            errors.append(f"{attachment.filename}: dosya 7 MB sinirini asiyor")
            continue
        try:
            raw = await attachment.read()
            encoded = base64.b64encode(raw).decode("ascii")
            image_data_urls.append(f"data:{mime};base64,{encoded}")
        except (discord.HTTPException, OSError) as exc:
            print(f"Gorsel okunamadi: {exc}")
            errors.append(f"{attachment.filename}: dosya okunamadi")
        if len(image_data_urls) >= 3:
            break
    return image_data_urls, errors


# -------------------- YAPAY ZEKA SOHBETI --------------------
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    await bot.process_commands(message)

    # Komutlar yapay zeka sohbeti gibi de ele alinmasin.
    lowered = message.content.lstrip().lower()
    if lowered.startswith(("!", "b!")):
        return

    if bot.user is None:
        return

    is_mentioned = bot.user in message.mentions
    is_reply_to_bot = False
    if message.reference and message.reference.message_id:
        try:
            replied_msg = await message.channel.fetch_message(message.reference.message_id)
            is_reply_to_bot = replied_msg.author.id == bot.user.id
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    if not (is_mentioned or is_reply_to_bot):
        return

    image_data_urls, image_errors = await read_image_attachments(message)
    clean_content = re.sub(rf"<@!?{bot.user.id}>", "", message.content).strip()
    if not clean_content and not image_data_urls:
        clean_content = "Bana kisa bir selam ver."
    elif image_data_urls and not clean_content:
        clean_content = "Bu gorseli incele. Icindekileri Turkce ve anlasilir sekilde acikla."

    try:
        async with message.channel.typing():
            loop = bot.loop
            answer = await loop.run_in_executor(
                AI_EXECUTOR,
                get_ai_response,
                clean_content,
                image_data_urls,
            )
        if image_errors:
            answer += "\n\nNot: " + "; ".join(image_errors)
        if not answer:
            answer = "Su an cevap olusturamadim."
        await message.reply(answer[:1900], mention_author=False)
    except (discord.HTTPException, discord.Forbidden) as exc:
        print(f"Discord mesaj gonderme hatasi: {exc}")


# -------------------- BASLAT --------------------
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN ortam degiskeni eksik. Render > Environment bolumune ekle.")
    keep_alive()
    bot.run(DISCORD_TOKEN)
