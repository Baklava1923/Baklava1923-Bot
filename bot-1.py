import discord
from discord.ext import commands, tasks
import os
import random
import sqlite3
import time
from keep_alive import keep_alive
import requests

intents = discord.Intents.default()
intents.message_content = True
intents.presences = True
intents.members = True

bot = commands.Bot(
    command_prefix=("!", "B!", "b!"),
    intents=intents,
    case_insensitive=True
)

bot.remove_command("help")

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

OWNER_ID = 1358430002508726276

current_mode = "normal"

mode_prompts = {
    "normal": "Sen yardımsever ve dengeli bir Discord botusun. Türkçe konuş.",
    "komik": "Sen çok esprili, şakacı ve takılan bir Discord botusun. Cevaplarını komik yap. Türkçe konuş.",
    "ciddi": "Sen çok resmi, kısa ve öz cevaplar veren ciddi bir Discord botusun. Türkçe konuş.",
    "korkutucu": "Sen gizemli ve ürkütücü bir atmosferle konuşan bir Discord botusun. Hikaye anlatır gibi ürkütücü bir üslup kullan ama gerçek tehdit içerme. Türkçe konuş.",
    "tartışmacı": "Sen sert ve meydan okuyan bir Discord botusun. Karşı görüşlere sert şekilde itiraz et. Argo kullanabilirsin.Küçük harfle yaz ve noktalama işaretlerini sakın kullanma. Türkçe konuş."
}


def get_ai_response(user_message):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": mode_prompts[current_mode]},
            {"role": "user", "content": user_message}
        ]
    }

    try:
        response = requests.post(
            GROQ_URL,
            headers=headers,
            json=data,
            timeout=25  # Discord'un bağlantısını kilitlememesi için üst sınır
        )
    except requests.exceptions.RequestException as e:
        print(f"API İSTEK HATASI: {e}")
        return "Şu an cevap veremiyorum, Groq'a ulaşılamadı. Birazdan tekrar dene."

    try:
        result = response.json()
    except ValueError:
        print(f"API JSON HATASI: {response.text[:300]}")
        return "Şu an cevap veremiyorum, API'den beklenmedik bir yanıt geldi."

    try:
        return result["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        print(f"API HATASI: {result}")
        hata = result.get("error", {}).get("message", "Bilinmeyen hata")
        return f"Bir hata oluştu: {hata}"


# AKTİFLİK SİSTEMİ

db = sqlite3.connect("aktiflik.db", check_same_thread=False)
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS activity (
    guild_id INTEGER,
    user_id INTEGER,
    start_time REAL,
    end_time REAL
)
""")

db.commit()

active_users = {}


# PAKLAVA EKONOMİ SİSTEMİ

PAKLAVA_BASLANGIC = 1000

cursor.execute("""
CREATE TABLE IF NOT EXISTS economy (
    guild_id INTEGER,
    user_id INTEGER,
    balance INTEGER DEFAULT 1000,
    PRIMARY KEY (guild_id, user_id)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS inventory (
    guild_id INTEGER,
    user_id INTEGER,
    item_key TEXT,
    PRIMARY KEY (guild_id, user_id, item_key)
)
""")

db.commit()

# Market'teki eşyalar: düello kazanınca kazanılan paklavayı yüzdesel artırır
MARKET_ITEMS = {
    "bakir_kasik": {
        "ad": "🥄 Bakır Kaşık",
        "fiyat": 500,
        "bonus": 0.10,
        "aciklama": "Düello kazançlarını %10 artırır"
    },
    "gumus_catal": {
        "ad": "🍴 Gümüş Çatal",
        "fiyat": 1500,
        "bonus": 0.25,
        "aciklama": "Düello kazançlarını %25 artırır"
    },
    "altin_tepsi": {
        "ad": "🍽️ Altın Tepsi",
        "fiyat": 4000,
        "bonus": 0.50,
        "aciklama": "Düello kazançlarını %50 artırır"
    },
    "elmas_firin": {
        "ad": "💎 Elmas Fırın",
        "fiyat": 10000,
        "bonus": 1.00,
        "aciklama": "Düello kazançlarını %100 artırır"
    },
    "paklava_tacı": {
        "ad": "👑 Paklava Tacı",
        "fiyat": 25000,
        "bonus": 2.00,
        "aciklama": "Düello kazançlarını %200 artırır, en nadir eşya"
    },
}


def get_balance(guild_id, user_id):
    cursor.execute(
        "SELECT balance FROM economy WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id)
    )
    row = cursor.fetchone()

    if row is None:
        cursor.execute(
            "INSERT INTO economy (guild_id, user_id, balance) VALUES (?, ?, ?)",
            (guild_id, user_id, PAKLAVA_BASLANGIC)
        )
        db.commit()
        return PAKLAVA_BASLANGIC

    return row[0]


def set_balance(guild_id, user_id, new_balance):
    get_balance(guild_id, user_id)  # satır yoksa oluştur

    cursor.execute(
        "UPDATE economy SET balance = ? WHERE guild_id = ? AND user_id = ?",
        (max(0, new_balance), guild_id, user_id)
    )
    db.commit()


def add_balance(guild_id, user_id, amount):
    current = get_balance(guild_id, user_id)
    set_balance(guild_id, user_id, current + amount)
    return current + amount


def get_inventory(guild_id, user_id):
    cursor.execute(
        "SELECT item_key FROM inventory WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id)
    )
    return [row[0] for row in cursor.fetchall()]


def get_duel_bonus(guild_id, user_id):
    items = get_inventory(guild_id, user_id)
    bonus = 0.0

    for item_key in items:
        item = MARKET_ITEMS.get(item_key)
        if item:
            bonus += item["bonus"]

    return bonus


@bot.command(name="bakiye", aliases=["cuzdan", "cüzdan"])
async def bakiye(ctx, oyuncu: discord.Member = None):
    hedef = oyuncu or ctx.author
    bakiye_miktari = get_balance(ctx.guild.id, hedef.id)

    embed = discord.Embed(
        title="🥮 Paklava Cüzdanı",
        description=f"{hedef.mention} → **{bakiye_miktari}** paklava",
        color=discord.Color.orange()
    )

    await ctx.send(embed=embed)


@bot.command(name="yazitura", aliases=["yazıtura"])
async def yazitura(ctx, miktar: str = None, secim: str = None):
    if miktar is None or secim is None:
        await ctx.send(
            "Kullanım: `B!yazitura <miktar> <yazı/tura>`\n"
            "Örnek: `B!yazitura 100 yazı`"
        )
        return

    bakiye_miktari = get_balance(ctx.guild.id, ctx.author.id)

    if miktar.lower() == "hepsi":
        bahis = bakiye_miktari
    else:
        try:
            bahis = int(miktar)
        except ValueError:
            await ctx.send("Geçerli bir miktar gir (sayı ya da `hepsi`).")
            return

    if bahis <= 0:
        await ctx.send("Bahis miktarı 0'dan büyük olmalı.")
        return

    if bahis > bakiye_miktari:
        await ctx.send(
            f"Yeterli paklavan yok! Bakiyen: **{bakiye_miktari}** paklava"
        )
        return

    secim = secim.lower()

    if secim not in ("yazı", "yazi", "tura"):
        await ctx.send("Seçimin `yazı` veya `tura` olmalı.")
        return

    secim = "yazı" if secim in ("yazı", "yazi") else "tura"
    sonuc = random.choice(["yazı", "tura"])

    if secim == sonuc:
        add_balance(ctx.guild.id, ctx.author.id, bahis)
        yeni_bakiye = get_balance(ctx.guild.id, ctx.author.id)

        embed = discord.Embed(
            title="🪙 Yazı Tura",
            description=(
                f"Para **{sonuc}** geldi! Tahminin doğruydu 🎉\n\n"
                f"**+{bahis}** paklava kazandın\n"
                f"Yeni bakiyen: **{yeni_bakiye}** paklava"
            ),
            color=discord.Color.green()
        )

    else:
        add_balance(ctx.guild.id, ctx.author.id, -bahis)
        yeni_bakiye = get_balance(ctx.guild.id, ctx.author.id)

        embed = discord.Embed(
            title="🪙 Yazı Tura",
            description=(
                f"Para **{sonuc}** geldi! Tahminin yanlıştı 😢\n\n"
                f"**-{bahis}** paklava kaybettin\n"
                f"Yeni bakiyen: **{yeni_bakiye}** paklava"
            ),
            color=discord.Color.red()
        )

    await ctx.send(embed=embed)


@bot.command(name="market")
async def market(ctx):
    embed = discord.Embed(
        title="🥮 Paklava Market",
        description="Satın almak için: `B!satinal <eşya adı>`",
        color=discord.Color.gold()
    )

    for key, item in MARKET_ITEMS.items():
        embed.add_field(
            name=f"{item['ad']} — {item['fiyat']} paklava",
            value=f"{item['aciklama']}\nKomut: `B!satinal {key}`",
            inline=False
        )

    await ctx.send(embed=embed)


@bot.command(name="satinal", aliases=["satınal"])
async def satinal(ctx, *, esya: str = None):
    if esya is None:
        await ctx.send("Hangi eşyayı almak istiyorsun? `B!market` ile listeye bak.")
        return

    esya_key = esya.lower().strip().replace(" ", "_")

    if esya_key not in MARKET_ITEMS:
        await ctx.send("Böyle bir eşya yok. `B!market` ile mevcut eşyalara bak.")
        return

    sahip_olunanlar = get_inventory(ctx.guild.id, ctx.author.id)

    if esya_key in sahip_olunanlar:
        await ctx.send("Bu eşyaya zaten sahipsin.")
        return

    item = MARKET_ITEMS[esya_key]
    bakiye_miktari = get_balance(ctx.guild.id, ctx.author.id)

    if bakiye_miktari < item["fiyat"]:
        await ctx.send(
            f"Yeterli paklavan yok! {item['ad']} için **{item['fiyat']}** paklava gerekiyor, "
            f"bakiyen: **{bakiye_miktari}** paklava"
        )
        return

    add_balance(ctx.guild.id, ctx.author.id, -item["fiyat"])

    cursor.execute(
        "INSERT INTO inventory (guild_id, user_id, item_key) VALUES (?, ?, ?)",
        (ctx.guild.id, ctx.author.id, esya_key)
    )
    db.commit()

    yeni_bakiye = get_balance(ctx.guild.id, ctx.author.id)

    embed = discord.Embed(
        title="✅ Satın Alındı",
        description=(
            f"{item['ad']} satın aldın!\n\n"
            f"Kalan bakiyen: **{yeni_bakiye}** paklava"
        ),
        color=discord.Color.green()
    )

    await ctx.send(embed=embed)


@bot.command(name="envanter")
async def envanter(ctx, oyuncu: discord.Member = None):
    hedef = oyuncu or ctx.author
    sahip_olunanlar = get_inventory(ctx.guild.id, hedef.id)

    embed = discord.Embed(
        title=f"🎒 {hedef.display_name}'in Envanteri",
        color=discord.Color.purple()
    )

    if not sahip_olunanlar:
        embed.description = "Hiç eşyası yok. `B!market` ile bir şeyler al."
    else:
        toplam_bonus = get_duel_bonus(ctx.guild.id, hedef.id)
        satirlar = [MARKET_ITEMS[key]["ad"] for key in sahip_olunanlar if key in MARKET_ITEMS]
        embed.description = (
            "\n".join(satirlar)
            + f"\n\nToplam düello kazanç bonusu: **+%{int(toplam_bonus * 100)}**"
        )

    await ctx.send(embed=embed)


def is_active(status):
    return status in (
        discord.Status.online,
        discord.Status.idle,
        discord.Status.dnd
    )


def start_tracking(guild_id, user_id):
    key = (guild_id, user_id)

    if key not in active_users:
        active_users[key] = time.time()


def stop_tracking(guild_id, user_id):
    key = (guild_id, user_id)

    if key in active_users:
        start_time = active_users.pop(key)
        end_time = time.time()

        cursor.execute(
            "INSERT INTO activity VALUES (?, ?, ?, ?)",
            (guild_id, user_id, start_time, end_time)
        )

        db.commit()


@bot.event
async def on_presence_update(before, after):
    if after.bot:
        return

    guild_id = after.guild.id
    user_id = after.id

    was_active = is_active(before.status)
    is_now_active = is_active(after.status)

    if not was_active and is_now_active:
        start_tracking(guild_id, user_id)

    elif was_active and not is_now_active:
        stop_tracking(guild_id, user_id)


@bot.event
async def on_ready():
    print(f"{bot.user} olarak giriş yapıldı!")

    for guild in bot.guilds:
        for member in guild.members:
            if member.bot:
                continue

            if is_active(member.status):
                start_tracking(guild.id, member.id)


def get_activity(guild_id, user_id, days):
    now = time.time()
    beginning = now - (days * 86400)
    total = 0

    cursor.execute(
        """
        SELECT start_time, end_time
        FROM activity
        WHERE guild_id = ?
        AND user_id = ?
        AND end_time >= ?
        """,
        (guild_id, user_id, beginning)
    )

    sessions = cursor.fetchall()

    for start, end in sessions:
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
    guild = ctx.guild

    if guild is None:
        await ctx.send("Bu komut sadece sunucularda kullanılabilir")
        return

    results = []

    for member in guild.members:
        if member.bot:
            continue

        seconds = get_activity(
            guild.id,
            member.id,
            days
        )

        if seconds > 0:
            results.append((member, seconds))

    results.sort(
        key=lambda x: x[1],
        reverse=True
    )

    results = results[:15]

    embed = discord.Embed(
        title="Aktiflik Tablosu",
        description=f"Son {days} gün",
        color=discord.Color.blue()
    )

    if not results:
        embed.description = (
            f"Son {days} gün içinde kayıtlı aktiflik bulunamadı"
        )

    else:
        text = ""

        for index, (member, seconds) in enumerate(results, 1):
            text += (
                f"**{index}.** {member.display_name} — "
                f"{format_time(seconds)}\n"
            )

        embed.add_field(
            name="En Aktif Üyeler",
            value=text,
            inline=False
        )

    embed.set_footer(
        text="Aktiflik Discord çevrimiçi boşta ve rahatsız etmeyin durumlarına göre hesaplanır"
    )

    await ctx.send(embed=embed)


class ActivityView(discord.ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=60)
        self.author_id = author_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Bu menüyü sadece komutu kullanan kişi kullanabilir",
                ephemeral=True
            )
            return False

        return True

    @discord.ui.button(label="7 Gün", style=discord.ButtonStyle.primary)
    async def seven_days(self, interaction, button):
        await interaction.response.defer()
        await show_activity(interaction.channel, 7)
        await interaction.message.delete()

    @discord.ui.button(label="30 Gün", style=discord.ButtonStyle.primary)
    async def thirty_days(self, interaction, button):
        await interaction.response.defer()
        await show_activity(interaction.channel, 30)
        await interaction.message.delete()

    @discord.ui.button(label="60 Gün", style=discord.ButtonStyle.primary)
    async def sixty_days(self, interaction, button):
        await interaction.response.defer()
        await show_activity(interaction.channel, 60)
        await interaction.message.delete()

    @discord.ui.button(label="90 Gün", style=discord.ButtonStyle.primary)
    async def ninety_days(self, interaction, button):
        await interaction.response.defer()
        await show_activity(interaction.channel, 90)
        await interaction.message.delete()


@bot.command()
async def aktiflik(ctx):
    embed = discord.Embed(
        title="Aktiflik Süresi",
        description="Hangi zaman aralığındaki aktifliği görmek istiyorsun\n\nAşağıdan bir süre seç",
        color=discord.Color.blue()
    )

    view = ActivityView(ctx.author.id)

    await ctx.send(
        embed=embed,
        view=view
    )


# NORMAL KOMUTLAR

@bot.command()
async def merhaba(ctx):
    await ctx.send("Merhaba! Bot çalışıyor 🎉")


@bot.command()
async def mod(ctx, secim: str):
    global current_mode

    secim = secim.lower()

    if secim in mode_prompts:
        current_mode = secim
        await ctx.send(f"Mod değiştirildi: **{secim}**")
    else:
        await ctx.send(
            "Geçerli modlar: normal, komik, ciddi, korkutucu, tartışmacı"
        )


@bot.command()
async def soru(ctx, *, soru_metni: str = None):
    if soru_metni is None:
        await ctx.send(
            "Bir soru sormalısın! Örnek: `!soru sen ne yapmayı seversin`"
        )
        return

    cevaplar = [
        "he doğru nerden bildin oic.",
        "abov.",
        "cevap veremem purnaciyim.",
        "he kesin.",
        "şuanda bir adamı sömürüyom.",
        "yarrak.",
        "purna.",
        "yo doğru değil sen calismamissin 0 aldin.",
        "Evet.",
        "İşaretler biraz belirsiz, tekrar sor.",
        "Şimdi cevap veremem.",
        "Şu an tahmin etme.",
        "Buna güvenme.",
        "Cevabım hayır.",
        "Kaynaklarıma göre hayır.",
        "Görünüşe göre pek iyi değil.",
        "Çok şüpheli."
    ]

    cevap = random.choice(cevaplar)

    embed = discord.Embed(
        title="Sihirli Paklava",
        color=discord.Color.purple()
    )

    embed.add_field(
        name="Soru",
        value=soru_metni,
        inline=False
    )

    embed.add_field(
        name="Cevap",
        value=cevap,
        inline=False
    )

    await ctx.send(embed=embed)


@bot.command(name="öneri")
async def oneri(ctx, *, oneri_metni: str = None):
    if oneri_metni is None:
        await ctx.send(
            "Bir öneri yazmalısın! Örnek: `!öneri bence şu eklensin`"
        )
        return

    try:
        owner = await bot.fetch_user(OWNER_ID)

        embed = discord.Embed(
            title="📩 Yeni Öneri",
            description=oneri_metni,
            color=discord.Color.gold()
        )

        embed.add_field(
            name="Gönderen",
            value=f"{ctx.author} ({ctx.author.id})",
            inline=False
        )

        embed.add_field(
            name="Sunucu",
            value=ctx.guild.name if ctx.guild else "DM",
            inline=False
        )

        await owner.send(embed=embed)
        await ctx.send("Öneriniz iletildi, teşekkürler! ✅")

    except discord.Forbidden:
        await ctx.send("Öneri iletilemedi, bir hata oluştu.")


# TAŞ KAĞIT MAKAS

class TKMGameView(discord.ui.View):
    def __init__(self, player1, player2):
        super().__init__(timeout=120)

        self.player1 = player1
        self.player2 = player2
        self.choices = {}

    async def interaction_check(self, interaction):
        if interaction.user.id not in (
            self.player1.id,
            self.player2.id
        ):
            await interaction.response.send_message(
                "Bu oyunda değilsin",
                ephemeral=True
            )
            return False

        return True

    async def choose(self, interaction, choice):
        if interaction.user.id in self.choices:
            await interaction.response.send_message(
                "Zaten seçim yaptın",
                ephemeral=True
            )
            return

        self.choices[interaction.user.id] = choice

        await interaction.response.send_message(
            f"Seçimin kaydedildi: **{choice}**",
            ephemeral=True
        )

        if len(self.choices) == 2:
            p1_choice = self.choices[self.player1.id]
            p2_choice = self.choices[self.player2.id]

            if p1_choice == p2_choice:
                result = "Berabere"

            elif (
                (p1_choice == "Taş" and p2_choice == "Makas")
                or
                (p1_choice == "Kağıt" and p2_choice == "Taş")
                or
                (p1_choice == "Makas" and p2_choice == "Kağıt")
            ):
                result = f"{self.player1.mention} kazandı"

            else:
                result = f"{self.player2.mention} kazandı"

            for button in self.children:
                button.disabled = True

            await interaction.message.edit(
                content=(
                    "**Taş Kağıt Makas Sonucu**\n\n"
                    f"{self.player1.mention}: **{p1_choice}**\n"
                    f"{self.player2.mention}: **{p2_choice}**\n\n"
                    f"**{result}**"
                ),
                view=self
            )

            self.stop()

    @discord.ui.button(label="Taş", style=discord.ButtonStyle.primary)
    async def rock(self, interaction, button):
        await self.choose(interaction, "Taş")

    @discord.ui.button(label="Kağıt", style=discord.ButtonStyle.success)
    async def paper(self, interaction, button):
        await self.choose(interaction, "Kağıt")

    @discord.ui.button(label="Makas", style=discord.ButtonStyle.danger)
    async def scissors(self, interaction, button):
        await self.choose(interaction, "Makas")


class TKMInviteView(discord.ui.View):
    def __init__(self, challenger, opponent):
        super().__init__(timeout=60)

        self.challenger = challenger
        self.opponent = opponent

    @discord.ui.button(
        label="Kabul Et",
        style=discord.ButtonStyle.success
    )
    async def accept(self, interaction, button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message(
                "Bu daveti sadece etiketlenen oyuncu kabul edebilir",
                ephemeral=True
            )
            return

        for item in self.children:
            item.disabled = True

        game_view = TKMGameView(
            self.challenger,
            self.opponent
        )

        await interaction.response.edit_message(
            content=(
                f"{self.challenger.mention} ve {self.opponent.mention}\n\n"
                "İkiniz de aşağıdan seçiminizi yapın"
            ),
            view=game_view
        )

        self.stop()

    @discord.ui.button(
        label="Reddet",
        style=discord.ButtonStyle.danger
    )
    async def decline(self, interaction, button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message(
                "Bu daveti sadece etiketlenen oyuncu reddedebilir",
                ephemeral=True
            )
            return

        await interaction.response.edit_message(
            content=f"{self.opponent.mention} oyunu reddetti",
            view=None
        )

        self.stop()


@bot.command(name="tkm")
async def tkm(ctx, oyuncu: discord.Member = None):
    if oyuncu is None:
        await ctx.send(
            "Bir oyuncuyu etiketlemelisin. Örnek: `B!tkm @oyuncu`"
        )
        return

    if oyuncu.bot:
        await ctx.send("Botlarla taş kağıt makas oynayamazsın")
        return

    if oyuncu.id == ctx.author.id:
        await ctx.send("Kendinle taş kağıt makas oynayamazsın")
        return

    view = TKMInviteView(
        ctx.author,
        oyuncu
    )

    await ctx.send(
        f"{oyuncu.mention}\n\n"
        f"{ctx.author.mention} seninle Taş Kağıt Makas oynamak istiyor\n"
        "Kabul ediyor musun",
        view=view
    )


# 1vs1 DÜELLO SİSTEMİ

DUEL_MAX_HP = 500
DUEL_ULTRA_COST = 100

active_duels = {}  # channel_id -> DuelGameView


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
            await interaction.response.send_message(
                "Bu düelloda değilsin", ephemeral=True
            )
            return False

        if interaction.user.id != self.current_player_id:
            await interaction.response.send_message(
                "Sıra sende değil", ephemeral=True
            )
            return False

        return True

    def build_embed(self, log_line=""):
        embed = discord.Embed(
            title="⚔️ 1vs1 Düello",
            color=discord.Color.red()
        )

        if log_line:
            embed.description = log_line

        for user_id in self.order:
            p = self.players[user_id]
            value = (
                f"❤️ {p['hp']}/{DUEL_MAX_HP}\n"
                f"{make_hp_bar(p['hp'])}\n"
                f"⚡ Güç: {p['power']}/{DUEL_ULTRA_COST}"
            )

            if p["shield"]:
                value += "\n🧱 Çin Seddi aktif"

            embed.add_field(
                name=p["member"].display_name,
                value=value,
                inline=True
            )

        embed.set_footer(
            text=f"Sıra: {self.players[self.current_player_id]['member'].display_name}"
        )

        return embed

    async def end_duel(self, interaction, winner_id, loser_id, reason):
        active_duels.pop(self.channel_id, None)

        for item in self.children:
            item.disabled = True

        guild_id = interaction.guild.id
        taban_odul = random.randint(50, 150)
        bonus = get_duel_bonus(guild_id, winner_id)
        toplam_odul = round(taban_odul * (1 + bonus))
        add_balance(guild_id, winner_id, toplam_odul)

        embed = discord.Embed(
            title="🏆 Düello Bitti",
            description=reason,
            color=discord.Color.gold()
        )

        embed.add_field(
            name="Kazanan",
            value=self.players[winner_id]["member"].mention
        )

        odul_metni = f"🥮 **+{toplam_odul}** paklava"
        if bonus > 0:
            odul_metni += f" (eşya bonusu: +%{int(bonus * 100)})"

        embed.add_field(
            name="Ödül",
            value=odul_metni,
            inline=False
        )

        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    async def resolve(self, interaction, action):
        actor_id = interaction.user.id
        target_id = self.other_id(actor_id)
        actor = self.players[actor_id]
        target = self.players[target_id]
        log = ""

        if action == "terlik":
            dmg = random.randint(50, 99)
            blocked = ""

            if target["shield"]:
                dmg = round(dmg * 0.4)
                target["shield"] = False
                blocked = " (Çin Seddi hasarı azalttı!)"

            target["hp"] = max(0, target["hp"] - dmg)
            log = (
                f"👡 {actor['member'].mention}, {target['member'].mention}'e "
                f"anne terliğiyle **{dmg}** hasar verdi!{blocked}"
            )

        elif action == "topla":
            gain = random.randint(30, 60)
            actor["power"] = min(DUEL_ULTRA_COST, actor["power"] + gain)
            log = f"🔋 {actor['member'].mention} güç topladı! (+{gain} güç)"

        elif action == "ultra":
            if actor["power"] < DUEL_ULTRA_COST:
                log = f"❌ {actor['member'].mention}, yeterli gücün yok, git topla."
            else:
                dmg = random.randint(200, 300)
                blocked = ""

                if target["shield"]:
                    dmg = round(dmg * 0.4)
                    target["shield"] = False
                    blocked = " (Çin Seddi hasarı azalttı!)"

                actor["power"] = 0
                target["hp"] = max(0, target["hp"] - dmg)
                log = (
                    f"💀 {actor['member'].mention}, ULTRA PEZEVENG PİÇİ ile "
                    f"{target['member'].mention}'e **{dmg}** hasar verdi!{blocked}"
                )

        elif action == "can":
            heal = random.randint(30, 100)
            actor["hp"] = min(DUEL_MAX_HP, actor["hp"] + heal)
            log = f"💗 {actor['member'].mention}, purnacı kalbiyle **{heal}** can kazandı!"

        elif action == "seddi":
            actor["shield"] = True
            log = (
                f"🧱 {actor['member'].mention} Çin Seddi'ni ördü! "
                "Bir sonraki saldırıdan daha az hasar alacak."
            )

        elif action == "pes":
            await self.end_duel(
                interaction, target_id, actor_id,
                f"🏳️ {actor['member'].mention} pes etti!"
            )
            return

        if target["hp"] <= 0:
            await self.end_duel(
                interaction, actor_id, target_id,
                f"{log}\n\n{target['member'].mention} yenildi!"
            )
            return

        self.turn_index = 1 - self.turn_index
        embed = self.build_embed(log)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        active_duels.pop(self.channel_id, None)

        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="👡 Anne Terliği", style=discord.ButtonStyle.danger, row=0)
    async def terlik(self, interaction, button):
        await self.resolve(interaction, "terlik")

    @discord.ui.button(label="🔋 Topla", style=discord.ButtonStyle.secondary, row=0)
    async def topla(self, interaction, button):
        await self.resolve(interaction, "topla")

    @discord.ui.button(label="💀 Ultra Pezeveng Piçi", style=discord.ButtonStyle.danger, row=0)
    async def ultra(self, interaction, button):
        await self.resolve(interaction, "ultra")

    @discord.ui.button(label="💗 Purnacı Kalbi", style=discord.ButtonStyle.success, row=1)
    async def can(self, interaction, button):
        await self.resolve(interaction, "can")

    @discord.ui.button(label="🧱 Çin Seddi", style=discord.ButtonStyle.primary, row=1)
    async def seddi(self, interaction, button):
        await self.resolve(interaction, "seddi")

    @discord.ui.button(label="🏳️ Pes Et", style=discord.ButtonStyle.secondary, row=1)
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
            await interaction.response.send_message(
                "Bu daveti sadece etiketlenen oyuncu kabul edebilir",
                ephemeral=True
            )
            return

        game_view = DuelGameView(
            self.challenger, self.opponent, interaction.channel.id
        )
        active_duels[interaction.channel.id] = game_view

        embed = game_view.build_embed("Düello başladı!")
        await interaction.response.edit_message(
            content=None, embed=embed, view=game_view
        )

        self.stop()

    @discord.ui.button(label="Reddet", style=discord.ButtonStyle.danger)
    async def decline(self, interaction, button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message(
                "Bu daveti sadece etiketlenen oyuncu reddedebilir",
                ephemeral=True
            )
            return

        await interaction.response.edit_message(
            content=f"{self.opponent.mention} daveti reddetti.",
            view=None
        )

        self.stop()

    async def on_timeout(self):
        pass


@bot.command(name="1vs1")
async def duel_command(ctx, oyuncu: discord.Member = None):
    if oyuncu is None:
        await ctx.send(
            "Bir oyuncuyu etiketlemelisin. Örnek: `B!1vs1 @oyuncu`"
        )
        return

    if oyuncu.bot:
        await ctx.send("Bir bot ile düello yapamazsın.")
        return

    if oyuncu.id == ctx.author.id:
        await ctx.send("Kendinle düello yapamazsın.")
        return

    if ctx.channel.id in active_duels:
        await ctx.send("Bu kanalda zaten aktif bir düello var, önce o bitsin.")
        return

    view = DuelInviteView(ctx.author, oyuncu)

    await ctx.send(
        f"{oyuncu.mention}\n\n"
        f"{ctx.author.mention} seni düelloya davet ediyor!",
        view=view
    )


# EĞLENCE MENÜSÜ

@bot.command(name="eğlence", aliases=["eglence"])
async def eglence(ctx):
    embed = discord.Embed(
        title="🎉 Eğlenceli Komutlar",
        description="Düello, kumar ve paklava ekonomisiyle ilgili komutlar",
        color=discord.Color.magenta()
    )

    embed.add_field(
        name="⚔️ Düello",
        value=(
            "`!1vs1 @oyuncu` — can/hasar tabanlı düello, kazanan paklava ödülü alır\n"
            "`!tkm @oyuncu` — taş kağıt makas düellosu"
        ),
        inline=False
    )

    embed.add_field(
        name="🥮 Paklava Ekonomisi",
        value=(
            "`!bakiye [@oyuncu]` — paklava bakiyeni gör\n"
            "`!yazitura <miktar> <yazı/tura>` — kumar oyna, kazanırsan ikiye katlanır\n"
            "`!market` — satılık eşyaları listele\n"
            "`!satinal <eşya>` — eşya satın al\n"
            "`!envanter [@oyuncu]` — sahip olunan eşyaları ve bonusları gör"
        ),
        inline=False
    )

    embed.add_field(
        name="🔮 Diğer",
        value="`!soru <soru metni>` — Sihirli Paklava'ya bir soru sor",
        inline=False
    )

    embed.set_footer(text="Tüm komutlar için: !yardim")

    embed.set_author(
        name=bot.user.name,
        icon_url=bot.user.display_avatar.url
    )

    await ctx.send(embed=embed)


# YARDIM

@bot.command(
    name="yardim",
    aliases=["help", "yardım"]
)
async def yardim_command(ctx):
    embed = discord.Embed(
        title="📖 Piçlik Komutları",
        color=discord.Color.green()
    )

    embed.add_field(
        name="!merhaba",
        value="Botun çalıştığını test et",
        inline=False
    )

    embed.add_field(
        name="!mod <isim>",
        value="Botun konuşma tarzını değiştir (normal, komik, ciddi, korkutucu, tartışmacı)",
        inline=False
    )

    embed.add_field(
        name="!soru <soru metni>",
        value="Sihirli Paklava'ya bir soru sor",
        inline=False
    )

    embed.add_field(
        name="!aktiflik",
        value="Sunucudaki en aktif üyeleri gör (7/30/60/90 gün)",
        inline=False
    )

    embed.add_field(
        name="!öneri <öneri metni>",
        value="Bota bir öneri gönder",
        inline=False
    )

    embed.add_field(
        name="!tkm @oyuncu",
        value="Bir oyuncuya Taş Kağıt Makas düellosu gönder",
        inline=False
    )

    embed.add_field(
        name="!1vs1 @oyuncu",
        value=(
            "Bir oyuncuyla can/hasar tabanlı düello yap "
            "(Anne Terliği, Ultra Pezeveng Piçi, Topla, Purnacı Kalbi, Çin Seddi, Pes Et). "
            "Kazanan paklava ödülü alır!"
        ),
        inline=False
    )

    embed.add_field(
        name="🥮 Paklava Ekonomisi",
        value=(
            "`!bakiye [@oyuncu]` — paklava bakiyeni gör\n"
            "`!yazitura <miktar> <yazı/tura>` — kumar oyna\n"
            "`!market` — satılık eşyaları listele\n"
            "`!satinal <eşya>` — eşya satın al\n"
            "`!envanter [@oyuncu]` — sahip olunan eşyaları gör"
        ),
        inline=False
    )

    embed.add_field(
        name="!eğlence",
        value="Düello, kumar ve paklava ekonomisi komutlarını listeler",
        inline=False
    )

    embed.add_field(
        name="!help",
        value="Bu mesajı gösterir",
        inline=False
    )

    embed.add_field(
        name="Sohbet",
        value="Beni etiketleyerek veya mesajıma reply atarak benimle sohbet edebilirsin",
        inline=False
    )

    embed.set_author(
        name=bot.user.name,
        icon_url=bot.user.display_avatar.url
    )

    await ctx.send(embed=embed)


# AI SOHBETİ

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    await bot.process_commands(message)

    is_mentioned = bot.user in message.mentions

    is_reply_to_bot = False

    if message.reference:
        try:
            replied_msg = await message.channel.fetch_message(
                message.reference.message_id
            )

            if replied_msg.author == bot.user:
                is_reply_to_bot = True

        except:
            pass

    if is_mentioned or is_reply_to_bot:
        if not message.content.startswith("!"):
            clean_content = message.content.replace(
                f"<@{bot.user.id}>",
                ""
            ).strip()

            has_attachment = len(message.attachments) > 0

            if clean_content:
                async with message.channel.typing():
                    # get_ai_response bloklayan (senkron) bir istek attığı için
                    # doğrudan await edilirse Discord'un heartbeat'i kilitlenip
                    # bot "çevrimdışı" görünebiliyordu. Bu yüzden ayrı bir thread'e
                    # taşıyıp event loop'u serbest bırakıyoruz.
                    cevap = await bot.loop.run_in_executor(
                        None, get_ai_response, clean_content
                    )
                    await message.reply(cevap)

            elif has_attachment:
                async with message.channel.typing():
                    cevap = await bot.loop.run_in_executor(
                        None,
                        get_ai_response,
                        "Kullanıcı sana bir resim veya gif gönderdi ama yazı yazmadı. "
                        "Buna kısa doğal bir tepki ver ve görseli gerçekten göremediğini belirt."
                    )

                    await message.reply(cevap)


keep_alive()
bot.run(os.environ["DISCORD_TOKEN"])
