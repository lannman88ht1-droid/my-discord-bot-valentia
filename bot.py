import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
import random
import re
from threading import Thread

import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask

# ---------------------------------------------------------
# 1. KEEP-ALIVE FLASK SUNUCUSU (RENDER WEB SERVICE)
# ---------------------------------------------------------
app = Flask('')


@app.route('/')
def home():
    return 'Bot Aktif ve 7/24 Calisiyor!'


def run():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)


def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()


# ---------------------------------------------------------
# 2. VERİTABANI VE AYAR YÖNETİMİ (JSON)
# ---------------------------------------------------------
WHITELIST_FILE = 'whitelist_data.json'


def load_whitelist():
    if not os.path.exists(WHITELIST_FILE):
        return {}
    try:
        with open(WHITELIST_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_whitelist(data):
    with open(WHITELIST_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def is_whitelisted(
    guild_id: int, user: discord.Member | discord.User, category: str
) -> bool:
    data = load_whitelist()
    guild_data = data.get(str(guild_id), {})

    user_perms = guild_data.get('users', {}).get(str(user.id), [])
    if 'All' in user_perms or category in user_perms:
        return True

    if isinstance(user, discord.Member):
        roles_data = guild_data.get('roles', {})
        for role in user.roles:
            r_perms = roles_data.get(str(role.id), [])
            if 'All' in r_perms or category in r_perms:
                return True

    return False


# ---------------------------------------------------------
# 3. BOT INTENTLERİ VE DEĞİŞKENLERİ
# ---------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.invites = True

bot = commands.Bot(command_prefix='!', intents=intents)

BOT_SAHIBI_ID = 1451263034541604997

guvenlik_aktif = True
sunucu_ayarlari = {}
davet_hafizasi = {}

kanal_silme_gecmisi = {}
kanal_olusturma_gecmisi = {}
rol_silme_gecmisi = {}
rol_olusturma_gecmisi = {}
perm_cekme_gecmisi = {}
etiket_spam_gecmisi = {}
link_spam_gecmisi = {}
mesaj_gecmisi = {}

TICKET_KANAL_KEYWORDS = [
    'ekip-alim',
    'partner',
    'merge-ally',
    'reklam',
    'genel-destek',
    'ticket',
    'log',
]

LINK_REGEX = re.compile(
    r'(https?:\/\/(?:www\.|(?!www))[a-zA-Z0-9][a-zA-Z0-9-]+[a-zA-Z0-9]\.[^\s]{2,}|'
    r'www\.[a-zA-Z0-9][a-zA-Z0-9-]+[a-zA-Z0-9]\.[^\s]{2,}|'
    r'https?:\/\/[a-zA-Z0-9]+\.[^\s]{2,}|'
    r'discord\.(?:gg|io|me|li)\/[a-zA-Z0-9]+|'
    r'discord\.com\/invite\/[a-zA-Z0-9]+|'
    r't\.me\/[a-zA-Z0-9_]+)'
)


def ayar_getir(guild_id: int):
    data = load_whitelist()
    g_id = str(guild_id)
    if g_id not in data:
        data[g_id] = {
            'users': {},
            'roles': {},
            'log_channel_id': None,
            'gelen_giden_kanal': None,
            'otorol_id': None,
        }
        save_whitelist(data)
    return data[g_id]


# ---------------------------------------------------------
# 4. ERENSİ TARZI MERKEZİ LOG GÖNDERİCİ
# ---------------------------------------------------------
async def erensi_log_gonder(guild: discord.Guild, embed: discord.Embed):
    if not guild:
        return

    data = load_whitelist()
    log_channel_id = data.get(str(guild.id), {}).get('log_channel_id')

    target_channel = None
    if log_channel_id:
        target_channel = guild.get_channel(log_channel_id)

    if not target_channel:
        target_channel = discord.utils.find(
            lambda c: 'log' in c.name.lower()
            and isinstance(c, discord.TextChannel),
            guild.channels,
        )

    if target_channel:
        try:
            await target_channel.send(embed=embed)
        except Exception as e:
            print(f'Log atılamadı: {e}')


async def ceza_uygula(
    guild: discord.Guild,
    user: discord.Member,
    sebep: str,
    action_category: str = 'All',
    target_name: str = 'Sunucu Yapısı',
):
    if (
        user == guild.owner
        or user.bot
        or user == bot.user
        or user.id == BOT_SAHIBI_ID
        or is_whitelisted(guild.id, user, action_category)
    ):
        return

    alinacak_roller = [
        role
        for role in user.roles
        if role.permissions.administrator
        or role.permissions.manage_roles
        or role.permissions.manage_channels
    ]

    if alinacak_roller:
        try:
            await user.remove_roles(
                *alinacak_roller, reason=f'GUARD/ANTI-RAID: {sebep}'
            )
        except Exception as e:
            print(f'Rol çekme hatası: {e}')

    try:
        await guild.ban(
            user, reason=f'🚨 Anti-Nuke: {sebep}', delete_message_days=1
        )
    except Exception as e:
        print(f'Banlama yetkisi yetersiz: {e}')

    embed = discord.Embed(
        title='🚨 • Anti-Nuke Güvenlik Müdahalesi!',
        color=0x992D22,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(
        name='Kullanıcı:',
        value=f'{user.mention} (`{user.name}` - `{user.id}`)',
        inline=False,
    )
    embed.add_field(
        name='İhlal Edilen Kategori:',
        value=f'`{action_category}`',
        inline=True,
    )
    embed.add_field(name='Sebep:', value=f'```{sebep}```', inline=False)
    embed.add_field(name='Hedef:', value=f'`{target_name}`', inline=True)
    embed.add_field(
        name='Yapılan İşlem:',
        value='🔴 **YETKİLERİ ALINDI VE BANLANDI**',
        inline=False,
    )
    embed.set_footer(text=f'Kullanıcı ID: {user.id} • Guard Sentinel')
    await erensi_log_gonder(guild, embed)


def whitelist_yetki_ve_hiyerarsi_kontrolu(
    interaction: discord.Interaction, target_role: discord.Role
) -> tuple[bool, str]:
    user = interaction.user
    guild = interaction.guild

    is_bot_owner = user.id == BOT_SAHIBI_ID
    is_server_owner = user.id == guild.owner_id
    is_admin = user.guild_permissions.administrator
    has_role_cmd_perm = is_whitelisted(
        guild.id, user, 'Role-Command'
    ) or is_whitelisted(guild.id, user, 'All')

    if not (is_bot_owner or is_server_owner or is_admin or has_role_cmd_perm):
        return (
            False,
            '❌ Bu komutu kullanabilmek için `/whitelist` listesinde `Rol'
            ' Verme/Alma Komutu (Role-Command)` yetkisine sahip olman gerekir!',
        )

    if guild.me.top_role <= target_role:
        return (
            False,
            '❌ Bu rol benim en yüksek rolümden üstte veya eşit olduğu için işlem'
            ' yapamam! Bot rolünü yukarı taşıyın.',
        )

    if not (is_bot_owner or is_server_owner) and user.top_role <= target_role:
        return (
            False,
            '❌ Kendi en yüksek rolünüzden daha yüksek veya eşit bir rolü'
            ' veremezsiniz/alamazsınız!',
        )

    return True, ''


def zaman_donustur(zaman_str: str) -> int:
    birimler = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
    match = re.match(r'^(\d+)([smhd])$', zaman_str.lower())
    if not match:
        return None
    sure, birim = match.groups()
    return int(sure) * birimler[birim]


async def davetleri_guncelle(guild: discord.Guild):
    try:
        invites = await guild.invites()
        davet_hafizasi[guild.id] = {invite.code: invite.uses for invite in invites}
    except Exception as e:
        print(f'Davetler alınamadı ({guild.name}): {e}')


# ---------------------------------------------------------
# 5. TICKET VIEWS
# ---------------------------------------------------------
class TicketKapatView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label='🔒 Talebi Kapat',
        style=discord.ButtonStyle.red,
        custom_id='ticket_kapat_buton',
    )
    async def ticket_kapat(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.send_message(
            'Destek kanalı 5 saniye içinde siliniyor...'
        )
        embed = discord.Embed(
            title='🎫 • Ticket Kapatıldı!',
            color=0xFFA500,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name='Kanal:', value=f'`#{interaction.channel.name}`', inline=True
        )
        embed.add_field(
            name='Kapatan Yetkili:',
            value=interaction.user.mention,
            inline=True,
        )
        embed.set_footer(text=f'Kullanıcı ID: {interaction.user.id}')
        await erensi_log_gonder(interaction.guild, embed)
        await asyncio.sleep(5)
        await interaction.channel.delete()


class TicketView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    async def kanal_olustur(
        self, interaction: discord.Interaction, kategori_adi: str
    ):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass

        try:
            guild = interaction.guild
            user = interaction.user
            kanal_adi = f'{kategori_adi}-{user.name.lower()}'

            existing_channel = discord.utils.get(guild.channels, name=kanal_adi)
            if existing_channel:
                return await interaction.followup.send(
                    f'Zaten açık bir **{kategori_adi}** biletiniz var:'
                    f' {existing_channel.mention}',
                    ephemeral=True,
                )

            CATEGORY_ID = 1479876130323431616
            category = guild.get_channel(CATEGORY_ID)

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                user: discord.PermissionOverwrite(
                    read_messages=True, send_messages=True
                ),
                guild.me: discord.PermissionOverwrite(
                    read_messages=True, send_messages=True
                ),
            }

            if category:
                ticket_channel = await category.create_text_channel(
                    name=kanal_adi, overwrites=overwrites
                )
            else:
                ticket_channel = await guild.create_text_channel(
                    name=kanal_adi, overwrites=overwrites
                )

            await interaction.followup.send(
                f'**{kategori_adi.upper()}** talebiniz oluşturuldu:'
                f' {ticket_channel.mention}',
                ephemeral=True,
            )

            rol_etiketleri = ''
            if kategori_adi == 'ekip-alim':
                rol_etiketleri = '<@&1545087594575175780>'
            elif kategori_adi == 'merge-ally':
                rol_etiketleri = '<@&1545087593211887659> <@&1545087322809438258>'
            elif kategori_adi in ['partner', 'genel-destek', 'reklam']:
                rol_etiketleri = '<@&1545087590460293120>'

            mesaj_icerigi = (
                f'Merhaba {user.mention} {rol_etiketleri},'
                f' **{kategori_adi.upper()}** talebiniz alındı. Yetkililer en kısa'
                ' sürede sizinle ilgilenecektir.\n\n'
            )

            if kategori_adi == 'ekip-alim':
                mesaj_icerigi += (
                    '📋 **Lütfen başvuru formunu doldurun:**\n```\nİsim: \nYaş: \nTier:'
                    ' \nGünlük Aktiflik Süren: \nMobil/Pc: \nÖnceki Ulusun: \nNeden'
                    ' Ayrıldın: \nNeden Biz:\n```\n'
                )
            elif kategori_adi == 'partner':
                mesaj_icerigi += '📋 **Sunucunuz kaç kişi?**\n'
            elif kategori_adi == 'merge-ally':
                mesaj_icerigi += (
                    '📋 **Günlük kaç aktif çekiyorsunuz?**\n **Hangi Sunucuları'
                    ' Oynuyorsunuz?**\n'
                )
            elif kategori_adi == 'reklam':
                mesaj_icerigi += '📋 **Hangi paketi alacaksınız?**\n'
            elif kategori_adi == 'genel-destek':
                mesaj_icerigi += '📋 **Sorununuz nedir?**\n'

            mesaj_icerigi += (
                '\nTalebi sonlandırmak için aşağıdaki butona basabilirsiniz.'
            )
            await ticket_channel.send(mesaj_icerigi, view=TicketKapatView())

            embed = discord.Embed(
                title='🎫 • Yeni Bir Ticket Açıldı!',
                color=0x2ECC71,
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name='Kullanıcı:', value=user.mention, inline=True)
            embed.add_field(
                name='Kategori:', value=f'`{kategori_adi.upper()}`', inline=True
            )
            embed.add_field(name='Kanal:', value=ticket_channel.mention, inline=True)
            embed.set_footer(text=f'Kullanıcı ID: {user.id}')
            await erensi_log_gonder(guild, embed)

        except Exception as e:
            print(f'❌ Ticket oluşturulurken hata: {e}')

    @discord.ui.button(
        label='⚔️ Ekip Alım',
        style=discord.ButtonStyle.red,
        custom_id='btn_ekip_alim',
    )
    async def ekip_alim(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self.kanal_olustur(interaction, 'ekip-alim')

    @discord.ui.button(
        label='🛡️ Merge / Ally',
        style=discord.ButtonStyle.green,
        custom_id='btn_merge_ally',
    )
    async def merge_ally(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self.kanal_olustur(interaction, 'merge-ally')

    @discord.ui.button(
        label='🤝 Partner',
        style=discord.ButtonStyle.blurple,
        custom_id='btn_partner',
    )
    async def partner(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self.kanal_olustur(interaction, 'partner')

    @discord.ui.button(
        label='💸 Reklam',
        style=discord.ButtonStyle.blurple,
        custom_id='btn_reklam',
    )
    async def reklam(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self.kanal_olustur(interaction, 'reklam')

    @discord.ui.button(
        label='💠 Genel Destek',
        style=discord.ButtonStyle.secondary,
        custom_id='btn_genel_destek',
    )
    async def genel_destek(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self.kanal_olustur(interaction, 'genel-destek')


# ---------------------------------------------------------
# 6. TAM TÜRKÇE WHITELIST SİSTEMİ
# ---------------------------------------------------------
class WhitelistGroup(app_commands.Group):

    def __init__(self):
        super().__init__(
            name='whitelist',
            description='Anti-nuke ve işlem whitelist yetki yönetimi',
        )

    WHITELIST_CHOICES = [
        app_commands.Choice(
            name='Kullanıcı/Rol: Tüm Yetkiler (Tam Güven)', value='All'
        ),
        app_commands.Choice(name='İşlem: Bağlantı/Link Paylaşma', value='Link'),
        app_commands.Choice(
            name='İşlem: Etiket Yetkisi (@everyone/@here)', value='Mention'
        ),
        app_commands.Choice(
            name='İşlem: Rol Verme/Alma Komutu (/rolver /rolal)',
            value='Role-Command',
        ),
        app_commands.Choice(name='İşlem: Üye Yasaklama (Ban)', value='Ban'),
        app_commands.Choice(name='İşlem: Üye Atma (Kick)', value='Kick'),
        app_commands.Choice(name='İşlem: Bot Ekleme', value='Bot-Add'),
        app_commands.Choice(
            name='İşlem: Rol Güncelleme/Verme', value='Role-Update'
        ),
        app_commands.Choice(name='İşlem: Rol Ekleme/Verme', value='Role-Add'),
        app_commands.Choice(
            name='İşlem: Kanal Oluşturma', value='Channel-Create'
        ),
        app_commands.Choice(name='İşlem: Kanal Silme', value='Channel-Delete'),
        app_commands.Choice(name='İşlem: Rol Oluşturma', value='Role-Create'),
        app_commands.Choice(name='İşlem: Rol Silme', value='Role-Delete'),
        app_commands.Choice(name='İşlem: Davet Linki Açma', value='Invite-Link'),
        app_commands.Choice(
            name='İşlem: Toplu Üye Çıkarma (Prune)', value='Prune'
        ),
    ]

    @app_commands.command(
        name='add_user',
        description='Kullanıcıya belirli bir eylem için yetki tanımlar.',
    )
    @app_commands.describe(
        user='Yetkilendirilecek üye', category='Yetki kategorisi'
    )
    @app_commands.choices(category=WHITELIST_CHOICES)
    async def add_user(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        category: app_commands.Choice[str],
    ):
        if (
            interaction.user.id != interaction.guild.owner_id
            and interaction.user.id != BOT_SAHIBI_ID
        ):
            return await interaction.response.send_message(
                '❌ Bu komutu yalnızca Sunucu Sahibi kullanabilir.', ephemeral=True
            )

        data = load_whitelist()
        g_id = str(interaction.guild_id)
        u_id = str(user.id)

        if g_id not in data:
            data[g_id] = {
                'users': {},
                'roles': {},
                'log_channel_id': None,
                'gelen_giden_kanal': None,
                'otorol_id': None,
            }
        if 'users' not in data[g_id]:
            data[g_id]['users'] = {}
        if u_id not in data[g_id]['users']:
            data[g_id]['users'][u_id] = []

        if category.value in data[g_id]['users'][u_id]:
            return await interaction.response.send_message(
                f'⚠️ {user.mention} zaten `{category.name}` yetkisine sahip.',
                ephemeral=True,
            )

        data[g_id]['users'][u_id].append(category.value)
        save_whitelist(data)

        embed = discord.Embed(
            title='🛡️ • Whitelist Güncellendi!',
            description=(
                f'{user.mention} kullanıcısına **`{category.name}`** izni verildi.'
            ),
            color=0x2ECC71,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f'Yetki Veren: {interaction.user.name}')
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name='add_role',
        description='Bir role toplu yetki tanımlar (Örn: Link izni).',
    )
    @app_commands.describe(role='Yetkilendirilecek rol', category='Yetki türü')
    @app_commands.choices(category=WHITELIST_CHOICES)
    async def add_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        category: app_commands.Choice[str],
    ):
        if (
            interaction.user.id != interaction.guild.owner_id
            and interaction.user.id != BOT_SAHIBI_ID
        ):
            return await interaction.response.send_message(
                '❌ Bu komutu yalnızca Sunucu Sahibi kullanabilir.', ephemeral=True
            )

        data = load_whitelist()
        g_id = str(interaction.guild_id)
        r_id = str(role.id)

        if g_id not in data:
            data[g_id] = {
                'users': {},
                'roles': {},
                'log_channel_id': None,
                'gelen_giden_kanal': None,
                'otorol_id': None,
            }
        if 'roles' not in data[g_id]:
            data[g_id]['roles'] = {}
        if r_id not in data[g_id]['roles']:
            data[g_id]['roles'][r_id] = []

        if category.value in data[g_id]['roles'][r_id]:
            return await interaction.response.send_message(
                f'⚠️ {role.mention} rolü zaten `{category.name}` yetkisine sahip.',
                ephemeral=True,
            )

        data[g_id]['roles'][r_id].append(category.value)
        save_whitelist(data)

        embed = discord.Embed(
            title='🛡️ • Role Whitelist Yetkisi Verildi!',
            description=(
                f'{role.mention} rolüne sahip herkes için **`{category.name}`**'
                ' izni aktif edildi.'
            ),
            color=0x2ECC71,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f'Yetki Veren: {interaction.user.name}')
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name='remove_user',
        description='Kullanıcının whitelist yetkilerini kaldırır.',
    )
    async def remove_user(
        self, interaction: discord.Interaction, user: discord.Member
    ):
        if (
            interaction.user.id != interaction.guild.owner_id
            and interaction.user.id != BOT_SAHIBI_ID
        ):
            return await interaction.response.send_message(
                '❌ Bu komutu yalnızca Sunucu Sahibi kullanabilir.', ephemeral=True
            )

        data = load_whitelist()
        g_id = str(interaction.guild_id)
        u_id = str(user.id)

        if (
            g_id in data
            and 'users' in data[g_id]
            and u_id in data[g_id]['users']
        ):
            del data[g_id]['users'][u_id]
            save_whitelist(data)
            return await interaction.response.send_message(
                f'🗑️ {user.mention} kullanıcısının tüm whitelist yetkileri'
                ' kaldırıldı.'
            )

        await interaction.response.send_message(
            '❌ Bu kullanıcı whitelist listesinde bulunamadı.', ephemeral=True
        )

    @app_commands.command(
        name='remove_role', description='Bir rolün whitelist yetkilerini kaldırır.'
    )
    async def remove_role(
        self, interaction: discord.Interaction, role: discord.Role
    ):
        if (
            interaction.user.id != interaction.guild.owner_id
            and interaction.user.id != BOT_SAHIBI_ID
        ):
            return await interaction.response.send_message(
                '❌ Bu komutu yalnızca Sunucu Sahibi kullanabilir.', ephemeral=True
            )

        data = load_whitelist()
        g_id = str(interaction.guild_id)
        r_id = str(role.id)

        if (
            g_id in data
            and 'roles' in data[g_id]
            and r_id in data[g_id]['roles']
        ):
            del data[g_id]['roles'][r_id]
            save_whitelist(data)
            return await interaction.response.send_message(
                f'🗑️ {role.mention} rolünün tüm whitelist yetkileri kaldırıldı.'
            )

        await interaction.response.send_message(
            '❌ Bu rol whitelist listesinde bulunamadı.', ephemeral=True
        )

    @app_commands.command(
        name='list',
        description='Sunucudaki whitelist kullanıcı ve rollerini listeler.',
    )
    async def list_users(self, interaction: discord.Interaction):
        data = load_whitelist()
        g_id = str(interaction.guild_id)
        users = data.get(g_id, {}).get('users', {})
        roles = data.get(g_id, {}).get('roles', {})

        if not users and not roles:
            return await interaction.response.send_message(
                'ℹ️ Whitelist listesinde hiç yetkili kullanıcı veya rol yok.',
                ephemeral=True,
            )

        desc = '### 👤 Yetkili Kullanıcılar:\n'
        if users:
            for u_id, perms in users.items():
                perm_str = ', '.join([f'`{p}`' for p in perms])
                desc += f'• <@{u_id}>: {perm_str}\n'
        else:
            desc += '*Ekli kullanıcı yok.*\n'

        desc += '\n### 🛡️ Yetkili Roller:\n'
        if roles:
            for r_id, perms in roles.items():
                perm_str = ', '.join([f'`{p}`' for p in perms])
                desc += f'• <@&{r_id}>: {perm_str}\n'
        else:
            desc += '*Ekli rol yok.*\n'

        embed = discord.Embed(
            title='📜 • Whitelist Yetki Listesi',
            description=desc,
            color=0x3498DB,
            timestamp=datetime.now(timezone.utc),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


bot.tree.add_command(WhitelistGroup())


# ---------------------------------------------------------
# 7. LOG KANALI VE YÖNETİM KOMUTLARI
# ---------------------------------------------------------
@bot.tree.command(
    name='set_log_channel',
    description='Tüm sunucu ve güvenlik loglarının gönderileceği kanalı ayarlar.',
)
@app_commands.default_permissions(administrator=True)
async def set_log_channel(
    interaction: discord.Interaction, channel: discord.TextChannel
):
    if (
        interaction.user.id != interaction.guild.owner_id
        and not interaction.user.guild_permissions.administrator
    ):
        return await interaction.response.send_message(
            '❌ Bu ayarı sadece yöneticiler değiştirebilir.', ephemeral=True
        )

    data = load_whitelist()
    g_id = str(interaction.guild_id)

    if g_id not in data:
        data[g_id] = {
            'users': {},
            'roles': {},
            'log_channel_id': None,
            'gelen_giden_kanal': None,
            'otorol_id': None,
        }

    data[g_id]['log_channel_id'] = channel.id
    save_whitelist(data)

    await interaction.response.send_message(
        f'📋 **Erensi Tarzı Sunucu Log Kanalı Ayarlandı:** {channel.mention}',
        ephemeral=True,
    )


# --- YENİ EKLENEN: TOPLU DM KOMUTU (/duyuru-dm) ---
@bot.tree.command(
    name='duyuru-dm',
    description='Sunucudaki tüm üyelere özel mesaj (DM) olarak duyuru iletir.',
)
@app_commands.describe(mesaj='Gönderilecek duyuru metni')
@app_commands.default_permissions(administrator=True)
async def duyuru_dm(interaction: discord.Interaction, mesaj: str):
    if (
        interaction.user.id != interaction.guild.owner_id
        and interaction.user.id != BOT_SAHIBI_ID
    ):
        return await interaction.response.send_message(
            '❌ Bu komutu yalnızca Sunucu Sahibi kullanabilir.', ephemeral=True
        )

    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send(
        '🚀 **Toplu DM Gönderimi Başlatıldı!**\nÜye sayısına göre işlem biraz'
        ' sürebilir, tamamlandığında bilgi verilecektir.',
        ephemeral=True,
    )

    basarili = 0
    basarisiz = 0

    for member in interaction.guild.members:
        if member.bot:
            continue
        try:
            embed = discord.Embed(
                title=f'📢 • {interaction.guild.name} Duyurusu',
                description=mesaj,
                color=0x3498DB,
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_footer(
                text=f'Gönderen Yetkili: {interaction.user.name}'
            )
            await member.send(embed=embed)
            basarili += 1
            await asyncio.sleep(1.5)  # Discord Rate-Limit koruması
        except Exception:
            basarisiz += 1

    log_embed = discord.Embed(
        title='📬 • Toplu DM Gönderimi Tamamlandı!',
        color=0x2ECC71,
        timestamp=datetime.now(timezone.utc),
    )
    log_embed.add_field(
        name='Gönderen:', value=interaction.user.mention, inline=True
    )
    log_embed.add_field(name='Başarılı:', value=f'`{basarili}`', inline=True)
    log_embed.add_field(
        name='Başarısız (DM Kapalı):', value=f'`{basarisiz}`', inline=True
    )
    log_embed.add_field(
        name='Duyuru İçeriği:', value=f'```{mesaj[:500]}```', inline=False
    )
    await erensi_log_gonder(interaction.guild, log_embed)


# ---------------------------------------------------------
# 8. YEDEKLEME VE GERİ YÜKLEME SİSTEMİ (XENON TARZI)
# ---------------------------------------------------------
@bot.command()
@commands.has_permissions(administrator=True)
async def yedek_al(ctx):
    guild = ctx.guild
    status_msg = await ctx.send(
        '⏳ Sunucu yapısı, roller ve kanal izinleri yedekleniyor...'
    )

    backup_data = {
        'roles': [],
        'categories': [],
        'text_channels': [],
        'voice_channels': [],
    }

    for role in reversed(guild.roles):
        if role.is_default():
            backup_data['roles'].append({
                'name': role.name,
                'permissions': role.permissions.value,
                'color': role.color.value,
                'hoist': role.hoist,
                'mentionable': role.mentionable,
                'is_default': True,
            })
        elif not role.managed:
            backup_data['roles'].append({
                'name': role.name,
                'permissions': role.permissions.value,
                'color': role.color.value,
                'hoist': role.hoist,
                'mentionable': role.mentionable,
                'is_default': False,
            })

    def serialize_overwrites(overwrites):
        serialized = []
        for target, overwrite in overwrites.items():
            serialized.append({
                'name': target.name,
                'is_role': isinstance(target, discord.Role),
                'allow': overwrite.pair()[0].value,
                'deny': overwrite.pair()[1].value,
            })
        return serialized

    for cat in guild.categories:
        backup_data['categories'].append({
            'name': cat.name,
            'position': cat.position,
            'overwrites': serialize_overwrites(cat.overwrites),
        })

    for tc in guild.text_channels:
        backup_data['text_channels'].append({
            'name': tc.name,
            'topic': tc.topic,
            'nsfw': tc.nsfw,
            'position': tc.position,
            'category': tc.category.name if tc.category else None,
            'overwrites': serialize_overwrites(tc.overwrites),
        })

    for vc in guild.voice_channels:
        backup_data['voice_channels'].append({
            'name': vc.name,
            'user_limit': vc.user_limit,
            'bitrate': vc.bitrate,
            'position': vc.position,
            'category': vc.category.name if vc.category else None,
            'overwrites': serialize_overwrites(vc.overwrites),
        })

    file_path = f'backup_{guild.id}.json'
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(backup_data, f, ensure_ascii=False, indent=4)

    try:
        await ctx.author.send(
            '🛡️ **Sunucu Yedeğiniz Alındı!**\nDosyayı güvenli bir yerde'
            ' saklayabilirsiniz:',
            file=discord.File(file_path),
        )
        await status_msg.edit(
            content='✅ Yedek başarıyla alındı ve özel mesajınıza (DM) gönderildi!'
        )
    except discord.Forbidden:
        await status_msg.edit(
            content='✅ Yedek oluşturuldu fakat DM kutunuz kapalı olduğu için iletilemedi.'
        )


@bot.command()
@commands.has_permissions(administrator=True)
async def yedek_yukle(ctx):
    guild = ctx.guild
    file_path = f'backup_{guild.id}.json'

    if not os.path.exists(file_path):
        return await ctx.send(
            '❌ Bu sunucuya ait yerel bir yedek dosyası (`backup.json`)'
            ' bulunamadı!'
        )

    confirm_msg = await ctx.send(
        '⚠️ **DİKKAT:** Mevcut kanallar silinip roller yedeğe göre'
        ' yapılandırılacaktır. Devam etmek için 30 saniye içinde `onayla` yazın.'
    )

    def check(m):
        return (
            m.author == ctx.author
            and m.channel == ctx.channel
            and m.content.lower() == 'onayla'
        )

    try:
        await bot.wait_for('message', timeout=30.0, check=check)
    except asyncio.TimeoutError:
        return await confirm_msg.edit(
            content='❌ Süre dolduğu için yedek yükleme iptal edildi.'
        )

    await ctx.send('🚀 Geri yükleme başlatıldı...')

    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    for ch in guild.channels:
        try:
            await ch.delete()
            await asyncio.sleep(0.4)
        except Exception:
            pass

    created_roles = {}
    for r_data in data['roles']:
        if r_data.get('is_default'):
            everyone_role = guild.default_role
            await everyone_role.edit(
                permissions=discord.Permissions(r_data['permissions'])
            )
            created_roles['@everyone'] = everyone_role
        else:
            try:
                new_role = await guild.create_role(
                    name=r_data['name'],
                    permissions=discord.Permissions(r_data['permissions']),
                    color=discord.Color(r_data['color']),
                    hoist=r_data['hoist'],
                    mentionable=r_data['mentionable'],
                )
                created_roles[new_role.name] = new_role
                await asyncio.sleep(0.4)
            except Exception:
                pass

    def deserialize_overwrites(serialized_list):
        overwrites = {}
        for item in serialized_list:
            target = created_roles.get(item['name'])
            if target:
                overwrites[target] = discord.PermissionOverwrite.from_pair(
                    discord.Permissions(item['allow']),
                    discord.Permissions(item['deny']),
                )
        return overwrites

    created_categories = {}
    for cat_data in data['categories']:
        try:
            cat = await guild.create_category(
                name=cat_data['name'],
                position=cat_data['position'],
                overwrites=deserialize_overwrites(cat_data['overwrites']),
            )
            created_categories[cat.name] = cat
            await asyncio.sleep(0.4)
        except Exception:
            pass

    for tc_data in data['text_channels']:
        try:
            cat = created_categories.get(tc_data['category'])
            await guild.create_text_channel(
                name=tc_data['name'],
                topic=tc_data['topic'],
                nsfw=tc_data['nsfw'],
                position=tc_data['position'],
                category=cat,
                overwrites=deserialize_overwrites(tc_data['overwrites']),
            )
            await asyncio.sleep(0.4)
        except Exception:
            pass

    for vc_data in data['voice_channels']:
        try:
            cat = created_categories.get(vc_data['category'])
            await guild.create_voice_channel(
                name=vc_data['name'],
                user_limit=vc_data['user_limit'],
                bitrate=vc_data['bitrate'],
                position=vc_data['position'],
                category=cat,
                overwrites=deserialize_overwrites(vc_data['overwrites']),
            )
            await asyncio.sleep(0.4)
        except Exception:
            pass


# ---------------------------------------------------------
# 9. AYAR VE YARDIMCI KOMUTLAR (OTOROL DAHİL)
# ---------------------------------------------------------
ayarla_grubu = app_commands.Group(
    name='ayarla', description='Bot sistem ayarlarını yönetir.'
)


@ayarla_grubu.command(
    name='gelengiden', description='Gelen-Giden kanalını belirler.'
)
@app_commands.default_permissions(administrator=True)
async def ayarla_gelengiden(
    interaction: discord.Interaction, kanal: discord.TextChannel
):
    data = load_whitelist()
    g_id = str(interaction.guild_id)
    if g_id not in data:
        data[g_id] = {
            'users': {},
            'roles': {},
            'log_channel_id': None,
            'gelen_giden_kanal': None,
            'otorol_id': None,
        }

    data[g_id]['gelen_giden_kanal'] = kanal.id
    save_whitelist(data)
    await interaction.response.send_message(
        f'✅ Gelen-Giden kanalı {kanal.mention} olarak ayarlandı!', ephemeral=True
    )


@ayarla_grubu.command(
    name='otorol',
    description='Sunucuya yeni girenlere otomatik verilecek rolü ayarlar.',
)
@app_commands.describe(rol='Otomatik verilecek rol')
@app_commands.default_permissions(administrator=True)
async def ayarla_otorol(interaction: discord.Interaction, rol: discord.Role):
    if interaction.guild.me.top_role <= rol:
        return await interaction.response.send_message(
            '❌ Bu rol benim en yüksek rolümden üstte veya eşit olduğu için otomatik'
            ' veremem! Bot rolünü yukarı taşıyın.',
            ephemeral=True,
        )

    data = load_whitelist()
    g_id = str(interaction.guild_id)
    if g_id not in data:
        data[g_id] = {
            'users': {},
            'roles': {},
            'log_channel_id': None,
            'gelen_giden_kanal': None,
            'otorol_id': None,
        }

    data[g_id]['otorol_id'] = rol.id
    save_whitelist(data)
    await interaction.response.send_message(
        f'✅ **Otorol Ayarlandı:** Sunucuya yeni katılanlara otomatik olarak'
        f' {rol.mention} rolü verilecek!',
        ephemeral=True,
    )


bot.tree.add_command(ayarla_grubu)


@bot.tree.command(
    name='rolver',
    description='Bir kullanıcıya rol verir (Sadece Whitelist/Yetkililer).',
)
@app_commands.default_permissions(send_messages=True)
async def rol_ver_komut(
    interaction: discord.Interaction, member: discord.Member, role: discord.Role
):
    await interaction.response.defer(ephemeral=True)
    izin_var, hata_mesaji = whitelist_yetki_ve_hiyerarsi_kontrolu(
        interaction, role
    )
    if not izin_var:
        return await interaction.followup.send(hata_mesaji, ephemeral=True)

    try:
        await member.add_roles(
            role, reason=f'Yetkili Komutu: {interaction.user.name}'
        )
        await interaction.followup.send(
            f'✅ **{member.mention}** kullanıcısına **{role.name}** rolü verildi!',
            ephemeral=True,
        )
    except Exception as e:
        await interaction.followup.send(f'❌ Hata: {e}', ephemeral=True)


@bot.tree.command(
    name='rolal',
    description='Bir kullanıcıdan rol alır (Sadece Whitelist/Yetkililer).',
)
@app_commands.default_permissions(send_messages=True)
async def rol_al_komut(
    interaction: discord.Interaction, member: discord.Member, role: discord.Role
):
    await interaction.response.defer(ephemeral=True)
    if role not in member.roles:
        return await interaction.followup.send(
            f'⚠️ **{member.mention}** kullanıcısında zaten **{role.name}** rolü'
            ' yok!',
            ephemeral=True,
        )

    izin_var, hata_mesaji = whitelist_yetki_ve_hiyerarsi_kontrolu(
        interaction, role
    )
    if not izin_var:
        return await interaction.followup.send(hata_mesaji, ephemeral=True)

    try:
        await member.remove_roles(
            role, reason=f'Yetkili Komutu: {interaction.user.name}'
        )
        await interaction.followup.send(
            f'🗑️ **{member.mention}** kullanıcısından **{role.name}** rolü alındı!',
            ephemeral=True,
        )
    except Exception as e:
        await interaction.followup.send(f'❌ Hata: {e}', ephemeral=True)


@bot.tree.command(name='cekilis', description='Yeni bir çekiliş başlatır.')
@app_commands.describe(
    sure='Örn: 30s, 10m, 2h, 1d',
    kazanan_sayisi='Kaç kişi kazanacak?',
    odul='Çekiliş ödülü',
)
@app_commands.default_permissions(manage_events=True)
async def cekilis_baslat(
    interaction: discord.Interaction, sure: str, kazanan_sayisi: int, odul: str
):
    saniye = zaman_donustur(sure)
    if not saniye:
        return await interaction.response.send_message(
            '❌ Geçersiz süre! Örnek: `10m`, `1h`, `30s`.', ephemeral=True
        )

    embed = discord.Embed(
        title=f'🎉 ÇEKİLİŞ: {odul}',
        description=(
            f'Katılmak için aşağıdaki 🎉 tepkisine tıklayın!\n\n⏱️ **Süre:**'
            f' {sure}\n👑 **Kazanan Sayısı:** {kazanan_sayisi}\n📝'
            f' **Düzenleyen:** {interaction.user.mention}'
        ),
        color=discord.Color.gold(),
    )
    embed.timestamp = datetime.now(timezone.utc) + timedelta(seconds=saniye)
    embed.set_footer(text='Bitiş Zamanı')

    await interaction.response.send_message(
        '🎉 Çekiliş başlatıldı!', ephemeral=True
    )
    mesaj = await interaction.channel.send(embed=embed)
    await mesaj.add_reaction('🎉')

    await asyncio.sleep(saniye)

    yeni_mesaj = await interaction.channel.fetch_message(mesaj.id)
    reaction = discord.utils.get(yeni_mesaj.reactions, emoji='🎉')
    users = [user async for user in reaction.users() if not user.bot]

    if len(users) < kazanan_sayisi:
        return await interaction.channel.send(
            f'⚠️ **{odul}** çekilişi iptal edildi. Yeterli katılım olmadı.'
        )

    kazananlar = random.sample(users, kazanan_sayisi)
    kazanan_mentions = ', '.join([k.mention for k in kazananlar])

    kazanan_embed = discord.Embed(
        title='🎉 ÇEKİLİŞ SONUÇLANDI! 🎉',
        description=f'**Ödül:** {odul}\n**Kazanan(lar):** {kazanan_mentions}',
        color=discord.Color.green(),
    )
    await interaction.channel.send(
        content=f'Tebrikler {kazanan_mentions}!', embed=kazanan_embed
    )


@bot.tree.command(name='ping', description='Botun gecikme süresini gösterir.')
async def ping(interaction: discord.Interaction):
    bot_ping = round(bot.latency * 1000)
    embed = discord.Embed(
        title='🏓 Pong!',
        description=f'**Bot Gecikmesi:** `{bot_ping} ms`',
        color=discord.Color.blue(),
        timestamp=datetime.now(timezone.utc),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name='ticket-kur', description='Destek panelini bu kanala kurar.'
)
@app_commands.checks.has_permissions(administrator=True)
async def ticket_kur(interaction: discord.Interaction):
    embed = discord.Embed(
        title='🎫 Destek & İletişim Paneli',
        description='Aşağıdaki butonlardan talep oluşturabilirsiniz.',
        color=discord.Color.blue(),
    )
    await interaction.channel.send(embed=embed, view=TicketView())
    await interaction.response.send_message(
        'Destek paneli kuruldu!', ephemeral=True
    )


@bot.tree.command(
    name='temizle', description='Belirtilen miktarda mesajı siler.'
)
@app_commands.checks.has_permissions(manage_messages=True)
async def temizle(interaction: discord.Interaction, miktar: int):
    if miktar < 1 or miktar > 100:
        return await interaction.response.send_message(
            '1 ile 100 arası bir sayı girin.', ephemeral=True
        )

    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=miktar)
    await interaction.followup.send(
        f'🧹 {len(deleted)} mesaj temizlendi.', ephemeral=True
    )


@bot.tree.command(
    name='guvenlik', description='Sunucu koruma sistemini açar/kapatır.'
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.choices(
    durum=[
        app_commands.Choice(name='Aç 🟢', value='ac'),
        app_commands.Choice(name='Kapat 🔴', value='kapat'),
    ]
)
async def guvenlik_komut(
    interaction: discord.Interaction, durum: app_commands.Choice[str]
):
    global guvenlik_aktif
    guvenlik_aktif = durum.value == 'ac'
    await interaction.response.send_message(
        '🛡️ Güvenlik sistemi'
        f" **{'AKTİF' if guvenlik_aktif else 'DEVRE DIŞI'}**.",
        ephemeral=True,
    )


# ---------------------------------------------------------
# 10. ERENSİBOT BİREBİR ŞABLONLU SUNUCU LOGLARI
# ---------------------------------------------------------

# 1. Mesaj Silinme Logu
@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    embed = discord.Embed(
        title='🗑️ • Bir Mesaj Silindi!',
        color=0xFF4D4D,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_author(
        name=f'{message.author} ({message.author.id})',
        icon_url=message.author.display_avatar.url,
    )
    embed.add_field(
        name='Kanal:',
        value=f'{message.channel.mention} (`#{message.channel.name}`)',
        inline=True,
    )
    embed.add_field(name='Mesaj Sahibi:', value=message.author.mention, inline=True)
    embed.add_field(
        name='Silinen Mesaj:',
        value=f"```{message.content[:1000] or 'İçerik yok (Resim/Embed)'}```",
        inline=False,
    )
    embed.set_footer(
        text=f'Kullanıcı ID: {message.author.id} • Mesaj ID: {message.id}'
    )
    await erensi_log_gonder(message.guild, embed)


# 2. Mesaj Düzenlenme Logu
@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.author.bot or not before.guild or before.content == after.content:
        return

    embed = discord.Embed(
        title='✏️ • Bir Mesaj Düzenlendi!',
        color=0xFFA500,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_author(
        name=f'{before.author} ({before.author.id})',
        icon_url=before.author.display_avatar.url,
    )
    embed.add_field(
        name='Kanal:',
        value=f'{before.channel.mention} [Mesaja Git]({after.jump_url})',
        inline=False,
    )
    embed.add_field(
        name='Eski Mesaj:',
        value=f"```{before.content[:500] or 'Boş'}```",
        inline=False,
    )
    embed.add_field(
        name='Yeni Mesaj:',
        value=f"```{after.content[:500] or 'Boş'}```",
        inline=False,
    )
    embed.set_footer(
        text=f'Kullanıcı ID: {before.author.id} • Mesaj ID: {after.id}'
    )
    await erensi_log_gonder(before.guild, embed)


# 3. Kanal Oluşturma Logu & Guard
@bot.event
async def on_guild_channel_create(channel):
    guild = channel.guild
    simdi = datetime.now(timezone.utc).timestamp()
    await asyncio.sleep(0.3)

    yapan_kisi = 'Bilinmiyor'
    olusturan_user = None

    try:
        async for entry in guild.audit_logs(
            action=discord.AuditLogAction.channel_create, limit=1
        ):
            if (
                datetime.now(timezone.utc) - entry.created_at
            ).total_seconds() < 5.0 and entry.target.id == channel.id:
                olusturan_user = entry.user
                yapan_kisi = (
                    f'{entry.user.mention} (`{entry.user.name}` - `{entry.user.id}`)'
                )
                break
    except Exception as e:
        print(f'Audit log hatası (Kanal Oluşturma): {e}')

    embed = discord.Embed(
        title='📁 • Yeni Bir Kanal Oluşturuldu!',
        color=0x2ECC71,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name='Kanal Adı:',
        value=f'`#{channel.name}` ({channel.mention})',
        inline=True,
    )
    embed.add_field(
        name='Kanal Türü:',
        value=f'`{str(channel.type).capitalize()}`',
        inline=True,
    )
    embed.add_field(name='Oluşturan Yetkili:', value=yapan_kisi, inline=False)
    embed.set_footer(text=f'Kanal ID: {channel.id}')
    await erensi_log_gonder(guild, embed)

    if (
        not guvenlik_aktif
        or any(k in channel.name for k in TICKET_KANAL_KEYWORDS)
        or not olusturan_user
    ):
        return

    if (
        olusturan_user.bot
        or olusturan_user == guild.owner
        or olusturan_user.id == BOT_SAHIBI_ID
        or is_whitelisted(guild.id, olusturan_user, 'Channel-Create')
    ):
        return

    user_id = olusturan_user.id
    kanal_olusturma_gecmisi.setdefault(user_id, [])
    kanal_olusturma_gecmisi[user_id] = [
        t for t in kanal_olusturma_gecmisi[user_id] if simdi - t < 10.0
    ]
    kanal_olusturma_gecmisi[user_id].append(simdi)

    try:
        await channel.delete(reason='Guard: İzinsiz spam kanal açıldı.')
    except Exception:
        pass

    if len(kanal_olusturma_gecmisi[user_id]) >= 2:
        await ceza_uygula(
            guild,
            olusturan_user,
            '10 saniyede 2+ izinsiz kanal açtı!',
            'Channel-Create',
            channel.name,
        )


# 4. Kanal Silme Logu & Guard
@bot.event
async def on_guild_channel_delete(channel):
    guild = channel.guild
    simdi = datetime.now(timezone.utc).timestamp()
    await asyncio.sleep(0.3)

    yapan_kisi = 'Bilinmiyor'
    silen_user = None

    try:
        async for entry in guild.audit_logs(
            action=discord.AuditLogAction.channel_delete, limit=1
        ):
            if (
                datetime.now(timezone.utc) - entry.created_at
            ).total_seconds() < 5.0 and entry.target.id == channel.id:
                silen_user = entry.user
                yapan_kisi = (
                    f'{entry.user.mention} (`{entry.user.name}` - `{entry.user.id}`)'
                )
                break
    except Exception as e:
        print(f'Audit log hatası (Kanal Silme): {e}')

    embed = discord.Embed(
        title='🗑️ • Bir Kanal Silindi!',
        color=0xE74C3C,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name='Silinen Kanal:', value=f'`#{channel.name}`', inline=True
    )
    embed.add_field(name='Kanal ID:', value=f'`{channel.id}`', inline=True)
    embed.add_field(name='Silen Yetkili:', value=yapan_kisi, inline=False)
    embed.set_footer(text=f'Kanal Türü: {str(channel.type).capitalize()}')
    await erensi_log_gonder(guild, embed)

    if (
        not guvenlik_aktif
        or any(k in channel.name for k in TICKET_KANAL_KEYWORDS)
        or not silen_user
    ):
        return

    if (
        silen_user.bot
        or silen_user == guild.owner
        or silen_user.id == BOT_SAHIBI_ID
        or is_whitelisted(guild.id, silen_user, 'Channel-Delete')
    ):
        return

    try:
        await channel.clone(reason='Guard: Silinen kanal kurtarıldı.')
    except Exception:
        pass

    await ceza_uygula(
        guild,
        silen_user,
        f'İzinsiz kanal sildi (#{channel.name})',
        'Channel-Delete',
        channel.name,
    )


# 5. Rol Oluşturma Logu & Guard
@bot.event
async def on_guild_role_create(role):
    guild = role.guild
    await asyncio.sleep(0.3)

    yapan_kisi = 'Bilinmiyor'
    olusturan_user = None

    try:
        async for entry in guild.audit_logs(
            action=discord.AuditLogAction.role_create, limit=1
        ):
            if (
                datetime.now(timezone.utc) - entry.created_at
            ).total_seconds() < 5.0 and entry.target.id == role.id:
                olusturan_user = entry.user
                yapan_kisi = (
                    f'{entry.user.mention} (`{entry.user.name}` - `{entry.user.id}`)'
                )
                break
    except Exception as e:
        print(f'Audit log hatası (Rol Oluşturma): {e}')

    embed = discord.Embed(
        title='🛡️ • Yeni Bir Rol Oluşturuldu!',
        color=0x2ECC71,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name='Rol Adı:', value=role.mention, inline=True)
    embed.add_field(name='Rol ID:', value=f'`{role.id}`', inline=True)
    embed.add_field(name='Oluşturan Yetkili:', value=yapan_kisi, inline=False)
    embed.set_footer(text=f'Sunucu: {guild.name}')
    await erensi_log_gonder(guild, embed)

    if not guvenlik_aktif or not olusturan_user:
        return

    if (
        olusturan_user.bot
        or olusturan_user == guild.owner
        or olusturan_user.id == BOT_SAHIBI_ID
        or is_whitelisted(guild.id, olusturan_user, 'Role-Create')
    ):
        return

    try:
        await role.delete(reason='Guard: İzinsiz rol açıldı.')
    except Exception:
        pass
    await ceza_uygula(
        guild,
        olusturan_user,
        f'İzinsiz rol oluşturdu (@{role.name})',
        'Role-Create',
        role.name,
    )


# 6. Rol Silme Logu & Guard
@bot.event
async def on_guild_role_delete(role):
    guild = role.guild
    await asyncio.sleep(0.3)

    yapan_kisi = 'Bilinmiyor'
    silen_user = None

    try:
        async for entry in guild.audit_logs(
            action=discord.AuditLogAction.role_delete, limit=1
        ):
            if (
                datetime.now(timezone.utc) - entry.created_at
            ).total_seconds() < 5.0 and entry.target.id == role.id:
                silen_user = entry.user
                yapan_kisi = (
                    f'{entry.user.mention} (`{entry.user.name}` - `{entry.user.id}`)'
                )
                break
    except Exception as e:
        print(f'Audit log hatası (Rol Silme): {e}')

    embed = discord.Embed(
        title='🗑️ • Bir Rol Silindi!',
        color=0xE74C3C,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name='Silinen Rol:', value=f'`@{role.name}`', inline=True)
    embed.add_field(name='Rol ID:', value=f'`{role.id}`', inline=True)
    embed.add_field(name='Silen Yetkili:', value=yapan_kisi, inline=False)
    embed.set_footer(text=f'Sunucu: {guild.name}')
    await erensi_log_gonder(guild, embed)

    if not guvenlik_aktif or not silen_user:
        return

    if (
        silen_user.bot
        or silen_user == guild.owner
        or silen_user.id == BOT_SAHIBI_ID
        or is_whitelisted(guild.id, silen_user, 'Role-Delete')
    ):
        return

    try:
        await guild.create_role(
            name=role.name,
            permissions=role.permissions,
            color=role.color,
            reason='Guard: Rol geri yüklendi.',
        )
    except Exception:
        pass
    await ceza_uygula(
        guild,
        silen_user,
        f'İzinsiz rol sildi (@{role.name})',
        'Role-Delete',
        role.name,
    )


# 7. Üye Rol Güncelleme Logu & Guard
@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if before.guild is None or before.roles == after.roles:
        return

    guild = before.guild
    alnan_roller = [r for r in before.roles if r not in after.roles]
    eklenen_roller = [r for r in after.roles if r not in before.roles]

    if eklenen_roller or alnan_roller:
        embed = discord.Embed(
            title='👤 • Bir Üyenin Rolleri Güncellendi!',
            color=0x3498DB,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_author(
            name=f'{after} ({after.id})', icon_url=after.display_avatar.url
        )
        embed.add_field(name='Kullanıcı:', value=after.mention, inline=False)
        if eklenen_roller:
            embed.add_field(
                name='➕ Verilen Rol(ler):',
                value=', '.join([r.mention for r in eklenen_roller]),
                inline=False,
            )
        if alnan_roller:
            embed.add_field(
                name='➖ Alınan Rol(ler):',
                value=', '.join([r.mention for r in alnan_roller]),
                inline=False,
            )
        embed.set_footer(text=f'Kullanıcı ID: {after.id}')
        await erensi_log_gonder(guild, embed)

    if not guvenlik_aktif:
        return

    await asyncio.sleep(0.2)

    if alnan_roller:
        kritik = [
            r
            for r in alnan_roller
            if r.permissions.administrator
            or r.permissions.manage_roles
            or r.permissions.manage_channels
        ]
        if kritik:
            async for entry in guild.audit_logs(
                action=discord.AuditLogAction.member_role_update, limit=1
            ):
                yapan = entry.user
                if (
                    yapan.bot
                    or yapan == guild.owner
                    or yapan.id == BOT_SAHIBI_ID
                    or is_whitelisted(guild.id, yapan, 'Role-Update')
                ):
                    return
                try:
                    await after.add_roles(*kritik, reason='Guard: Rol iade edildi.')
                except Exception:
                    pass
                await ceza_uygula(
                    guild,
                    yapan,
                    f'{after.name} adlı yetkilinin rollerini çekti!',
                    'Role-Update',
                    after.name,
                )

    if eklenen_roller:
        kritik_eklenen = [
            r
            for r in eklenen_roller
            if r.permissions.administrator
            or r.permissions.manage_roles
            or r.permissions.manage_channels
        ]
        if kritik_eklenen:
            async for entry in guild.audit_logs(
                action=discord.AuditLogAction.member_role_update, limit=1
            ):
                yapan = entry.user
                if (
                    yapan.bot
                    or yapan == guild.owner
                    or yapan.id == BOT_SAHIBI_ID
                    or is_whitelisted(guild.id, yapan, 'Role-Update')
                ):
                    return
                try:
                    await after.remove_roles(
                        *kritik_eklenen, reason='Guard: İzinsiz rol alındı.'
                    )
                except Exception:
                    pass
                await ceza_uygula(
                    guild,
                    yapan,
                    f'{after.name} adlı üyeye izinsiz yetki verdi!',
                    'Role-Update',
                    after.name,
                )


# 8. Üye Katılma Logu & Otorol & Bot-Add Koruması
@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild
    data = load_whitelist()
    guild_data = data.get(str(guild.id), {})

    otorol_id = guild_data.get('otorol_id')
    if otorol_id and not member.bot:
        rol = guild.get_role(otorol_id)
        if rol:
            try:
                await member.add_roles(
                    rol, reason='Otorol: Otomatik üye rolü verildi.'
                )
            except Exception as e:
                print(f'Otorol verilemedi: {e}')

    embed = discord.Embed(
        title='📥 • Bir Kullanıcı Sunucuya Katıldı!',
        color=0x2ECC71,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(
        name='Kullanıcı:',
        value=f'{member.mention} (`{member.name}`)',
        inline=True,
    )
    embed.add_field(
        name='Hesap Oluşturulma:',
        value=f'<t:{int(member.created_at.timestamp())}:R>',
        inline=True,
    )
    embed.add_field(
        name='Üye Sayısı:', value=f'**{guild.member_count}**', inline=False
    )
    embed.set_footer(text=f'Kullanıcı ID: {member.id}')
    await erensi_log_gonder(guild, embed)

    if member.bot and guvenlik_aktif:
        await asyncio.sleep(0.3)
        async for entry in guild.audit_logs(
            action=discord.AuditLogAction.bot_add, limit=1
        ):
            ekleyen = entry.user
            if (
                ekleyen != guild.owner
                and ekleyen.id != BOT_SAHIBI_ID
                and not is_whitelisted(guild.id, ekleyen, 'Bot-Add')
            ):
                try:
                    await member.ban(reason='Guard: İzinsiz bot eklendi.')
                except Exception:
                    pass
                await ceza_uygula(
                    guild,
                    ekleyen,
                    f'İzinsiz bot soktu ({member.name})',
                    'Bot-Add',
                    member.name,
                )
                return

    kanal_id = guild_data.get('gelen_giden_kanal')

    inviter = 'Bilinmiyor / Özel Davet'
    eski_davetler = davet_hafizasi.get(guild.id, {})
    try:
        yeni_davetler = await guild.invites()
        for invite in yeni_davetler:
            if invite.code in eski_davetler:
                if invite.uses > eski_davetler[invite.code]:
                    inviter = (
                        invite.inviter.mention if invite.inviter else 'Bilinmiyor'
                    )
                    break
        davet_hafizasi[guild.id] = {
            invite.code: invite.uses for invite in yeni_davetler
        }
    except Exception as e:
        print(f'Davet kontrol hatası: {e}')

    simdi = datetime.now(timezone.utc)
    olusturulma = member.created_at
    gun_sayisi = (simdi - olusturulma).days
    hesap_yasi = (
        f'{gun_sayisi // 365} Yıl {gun_sayisi % 365} Gün Önce'
        if gun_sayisi > 365
        else f'{gun_sayisi} Gün Önce'
    )

    if kanal_id:
        kanal = guild.get_channel(kanal_id)
        if kanal:
            g_embed = discord.Embed(
                title='Aramıza Biri Katıldı! 🎉',
                description=(
                    f'Hoş geldin {member.mention}!\n\n👤 **Davet Eden:**'
                    f' {inviter}\n📅 **Hesap Yaşı:** `{hesap_yasi}`'
                    f' (<t:{int(olusturulma.timestamp())}:R>)\n📊 **Toplam Üye:**'
                    f' **{guild.member_count}**'
                ),
                color=discord.Color.green(),
            )
            g_embed.set_thumbnail(url=member.display_avatar.url)
            await kanal.send(embed=g_embed)


# 9. Üye Ayrılma / Kick Logu
@bot.event
async def on_member_remove(member: discord.Member):
    guild = member.guild

    embed = discord.Embed(
        title='📤 • Bir Kullanıcı Sunucudan Ayrıldı!',
        color=0xE67E22,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(
        name='Kullanıcı:',
        value=f'{member.mention} (`{member.name}`)',
        inline=True,
    )
    embed.add_field(
        name='Kalan Üye Sayısı:',
        value=f'**{guild.member_count}**',
        inline=True,
    )
    embed.set_footer(text=f'Kullanıcı ID: {member.id}')
    await erensi_log_gonder(guild, embed)

    if guvenlik_aktif:
        await asyncio.sleep(0.3)
        async for entry in guild.audit_logs(
            action=discord.AuditLogAction.kick, limit=1
        ):
            if (
                datetime.now(timezone.utc) - entry.created_at
            ).total_seconds() < 5.0 and entry.target.id == member.id:
                atan = entry.user
                if (
                    atan != guild.owner
                    and atan.id != BOT_SAHIBI_ID
                    and not is_whitelisted(member.guild.id, atan, 'Kick')
                ):
                    await ceza_uygula(
                        guild,
                        atan,
                        f'İzinsiz üye attı (Kick: {member.name})',
                        'Kick',
                        member.name,
                    )
                break

    data = load_whitelist()
    kanal_id = data.get(str(guild.id), {}).get('gelen_giden_kanal')
    if kanal_id:
        kanal = guild.get_channel(kanal_id)
        if kanal:
            c_embed = discord.Embed(
                title='Aramızdan Biri Ayrıldı 😢',
                description=(
                    f'Görüşürüz {member.mention}!\n\n📊 **Kalan Üye:**'
                    f' **{guild.member_count}**'
                ),
                color=discord.Color.red(),
            )
            c_embed.set_thumbnail(url=member.display_avatar.url)
            await kanal.send(embed=c_embed)


# 10. Ban Atılma & Kaldırılma Logları
@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    embed = discord.Embed(
        title='🔨 • Bir Kullanıcı Yasaklandı!',
        color=0x992D22,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(
        name='Yasaklanan:', value=f'{user.mention} (`{user.name}`)', inline=True
    )
    embed.add_field(name='Kullanıcı ID:', value=f'`{user.id}`', inline=True)
    embed.set_footer(text=f'Sunucu: {guild.name}')
    await erensi_log_gonder(guild, embed)

    if not guvenlik_aktif:
        return
    await asyncio.sleep(0.3)
    async for entry in guild.audit_logs(
        action=discord.AuditLogAction.ban, limit=1
    ):
        if (
            datetime.now(timezone.utc) - entry.created_at
        ).total_seconds() < 5.0 and entry.target.id == user.id:
            banlayan = entry.user
            if (
                banlayan != guild.owner
                and banlayan.id != BOT_SAHIBI_ID
                and not is_whitelisted(guild.id, banlayan, 'Ban')
            ):
                await ceza_uygula(
                    guild,
                    banlayan,
                    f'İzinsiz üye banladı ({user.name})',
                    'Ban',
                    user.name,
                )
            break


@bot.event
async def on_member_unban(guild: discord.Guild, user: discord.User):
    embed = discord.Embed(
        title='🔓 • Bir Kullanıcının Yasağı Kaldırıldı!',
        color=0x1ABC9C,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(
        name='Yasağı Açılan:',
        value=f'{user.mention} (`{user.name}`)',
        inline=True,
    )
    embed.add_field(name='Kullanıcı ID:', value=f'`{user.id}`', inline=True)
    embed.set_footer(text=f'Sunucu: {guild.name}')
    await erensi_log_gonder(guild, embed)


# 11. Davet Oluşturma Logu
@bot.event
async def on_invite_create(invite: discord.Invite):
    embed = discord.Embed(
        title='📨 • Yeni Bir Davet Linki Oluşturuldu!',
        color=0x3498DB,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name='Davet Kodu:', value=f'`{invite.code}`', inline=True)
    embed.add_field(
        name='Oluşturan:',
        value=invite.inviter.mention if invite.inviter else 'Bilinmiyor',
        inline=True,
    )
    embed.set_footer(text=f'Sunucu: {invite.guild.name}')
    await erensi_log_gonder(invite.guild, embed)

    if guvenlik_aktif:
        async for entry in invite.guild.audit_logs(
            action=discord.AuditLogAction.invite_create, limit=1
        ):
            yapan = entry.user
            if (
                yapan != invite.guild.owner
                and yapan.id != BOT_SAHIBI_ID
                and not is_whitelisted(invite.guild.id, yapan, 'Invite-Link')
            ):
                try:
                    await invite.delete(
                        reason='Guard: İzinsiz davet linki silindi.'
                    )
                except Exception:
                    pass
                break
    await davetleri_guncelle(invite.guild)


@bot.event
async def on_invite_delete(invite: discord.Invite):
    await davetleri_guncelle(invite.guild)


# 12. Mesaj Takibi (YASAKLI ETİKET, LİNK & SPAM KORUMASI)
@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    if guvenlik_aktif:
        user = message.author
        user_id = user.id
        simdi = datetime.now(timezone.utc).timestamp()
        is_exempt = (
            user == message.guild.owner
            or user_id == BOT_SAHIBI_ID
            or is_whitelisted(message.guild.id, user, 'All')
        )

        if not is_exempt:

            if not is_whitelisted(message.guild.id, user, 'Mention'):
                yasakli_etiket_tespit = False
                if '@everyone' in message.content or '@here' in message.content:
                    yasakli_etiket_tespit = True

                if not yasakli_etiket_tespit and message.role_mentions:
                    yasakli_rol_idleri = [
                        1539361902596067458,
                        1539361906568073236,
                    ]
                    for role in message.role_mentions:
                        if role.id in yasakli_rol_idleri:
                            yasakli_etiket_tespit = True
                            break

                if yasakli_etiket_tespit:
                    try:
                        await message.delete()
                    except Exception:
                        pass

                    etiket_spam_gecmisi.setdefault(user_id, [])
                    etiket_spam_gecmisi[user_id] = [
                        t
                        for t in etiket_spam_gecmisi[user_id]
                        if simdi - t < 10.0
                    ]
                    etiket_spam_gecmisi[user_id].append(simdi)

                    if len(etiket_spam_gecmisi[user_id]) >= 2:
                        await ceza_uygula(
                            message.guild,
                            user,
                            '10 saniyede 2+ kez izinsiz toplu/yasaklı etiket'
                            ' attı!',
                            'Mention',
                            'Sohbet Kanalı',
                        )
                        etiket_spam_gecmisi[user_id] = []
                    else:
                        try:
                            await message.channel.send(
                                f'⚠️ {user.mention}, bu etiketi atma yetkiniz'
                                ' yok! Tekrar ederseniz yetkileriniz'
                                ' alınacaktır.',
                                delete_after=4,
                            )
                        except Exception:
                            pass
                    return

            if not is_whitelisted(message.guild.id, user, 'Link'):
                if LINK_REGEX.search(message.content):
                    try:
                        await message.delete()
                    except Exception:
                        pass

                    link_spam_gecmisi.setdefault(user_id, [])
                    link_spam_gecmisi[user_id] = [
                        t
                        for t in link_spam_gecmisi[user_id]
                        if simdi - t < 10.0
                    ]
                    link_spam_gecmisi[user_id].append(simdi)

                    embed = discord.Embed(
                        title='🔗 • Bir Reklam / Bağlantı Engellendi!',
                        color=0xE74C3C,
                        timestamp=datetime.now(timezone.utc),
                    )
                    embed.add_field(
                        name='Kullanıcı:',
                        value=f'{user.mention} (`{user.id}`)',
                        inline=True,
                    )
                    embed.add_field(
                        name='Kanal:',
                        value=message.channel.mention,
                        inline=True,
                    )
                    embed.add_field(
                        name='Engellenen İçerik:',
                        value=f'```{message.content[:500]}```',
                        inline=False,
                    )
                    embed.set_footer(text=f'Kullanıcı ID: {user.id}')
                    await erensi_log_gonder(message.guild, embed)

                    if len(link_spam_gecmisi[user_id]) >= 2:
                        await ceza_uygula(
                            message.guild,
                            user,
                            '10 saniyede 2+ kez izinsiz bağlantı/link paylaştı!',
                            'Link',
                            'Sohbet Kanalı',
                        )
                        link_spam_gecmisi[user_id] = []
                    else:
                        try:
                            await message.channel.send(
                                f'⚠️ {user.mention}, bu sunucuda bağlantı/link'
                                ' paylaşmak yasaktır!',
                                delete_after=4,
                            )
                        except Exception:
                            pass
                    return

            mesaj_gecmisi.setdefault(user_id, [])
            mesaj_gecmisi[user_id] = [
                t for t in mesaj_gecmisi[user_id] if simdi - t < 3.0
            ]
            mesaj_gecmisi[user_id].append(simdi)

            if len(mesaj_gecmisi[user_id]) >= 4:
                try:
                    await message.channel.purge(
                        limit=10, check=lambda m: m.author.id == user_id
                    )
                    await message.channel.send(
                        f'🚨 {user.mention}, çok hızlı mesaj attığınız için'
                        ' mesajlarınız temizlendi!',
                        delete_after=4,
                    )
                except Exception:
                    pass
                mesaj_gecmisi[user_id] = []

    await bot.process_commands(message)


# ---------------------------------------------------------
# 11. ON READY
# ---------------------------------------------------------
@bot.event
async def on_ready():
    bot.add_view(TicketView())
    bot.add_view(TicketKapatView())

    for guild in bot.guilds:
        await davetleri_guncelle(guild)
        try:
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(
                f'BAŞARILI: "{guild.name}" sunucusu için {len(synced)} komut'
                ' eşitlendi.'
            )
        except Exception as e:
            print(f'Eşitleme hatası ({guild.name}): {e}')

    print(f'🚀 {bot.user} Koruma ve Erensi Tarzı Log Sistemi 7/24 Aktif!')


# ---------------------------------------------------------
# 12. BAŞLATMA DÖNGÜSÜ
# ---------------------------------------------------------
if __name__ == '__main__':
    keep_alive()

    TOKEN = os.environ.get('DISCORD_TOKEN')

    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ HATA: 'DISCORD_TOKEN' çevre değişkeni bulunamadı!")