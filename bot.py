import asyncio
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from threading import Thread

import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask

# =========================================================
# GEREKSINIMLER: discord.py>=2.4 , flask
# Developer Portal'da: SERVER MEMBERS + MESSAGE CONTENT intent ACIK olmali.
# Botu davet ederken scope: bot + applications.commands
# Bot yetkileri: Administrator (veya en az Audit Log, Ban, Manage Roles/Channels)
# Bot rolu sunucudaki en ust rollerden biri olmali.
# =========================================================

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
# 2. VERITABANI (JSON + RAM CACHE -> diske her kontrolde gitmez, hizli)
# ---------------------------------------------------------
WHITELIST_FILE = 'whitelist_data.json'
_wl_cache = None


def load_whitelist():
    global _wl_cache
    if _wl_cache is None:
        if os.path.exists(WHITELIST_FILE):
            try:
                with open(WHITELIST_FILE, 'r', encoding='utf-8') as f:
                    _wl_cache = json.load(f)
            except Exception:
                _wl_cache = {}
        else:
            _wl_cache = {}
    return _wl_cache


def save_whitelist(data=None):
    global _wl_cache
    if data is not None:
        _wl_cache = data
    try:
        with open(WHITELIST_FILE, 'w', encoding='utf-8') as f:
            json.dump(_wl_cache, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f'Whitelist kaydedilemedi: {e}')


def ayar_getir(guild_id: int) -> dict:
    data = load_whitelist()
    g = data.setdefault(str(guild_id), {})
    degisti = False
    for anahtar, varsayilan in (
        ('users', {}),
        ('roles', {}),
        ('log_channel_id', None),
        ('gelen_giden_kanal', None),
        ('otorol_id', None),
        ('guard', True),
    ):
        if anahtar not in g:
            g[anahtar] = varsayilan
            degisti = True
    if degisti:
        save_whitelist()
    return g


def is_whitelisted(guild_id: int, user, category: str) -> bool:
    guild_data = load_whitelist().get(str(guild_id), {})

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
# 3. BOT TANIMI
# ---------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.invites = True
intents.moderation = True  # audit log olaylari (on_audit_log_entry_create) icin

BOT_SAHIBI_ID = 1451263034541604997


class GuardBot(commands.Bot):

    async def setup_hook(self):
        # Restart sonrasi ticket butonlari calismaya devam etsin
        self.add_view(TicketView())
        self.add_view(TicketKapatView())


bot = GuardBot(command_prefix='!', intents=intents)

davet_hafizasi = {}
silinen_kanallar = {}
silinen_roller = {}
cezali_kullanicilar = set()

kanal_silme_gecmisi = {}
kanal_olusturma_gecmisi = {}
rol_silme_gecmisi = {}
rol_olusturma_gecmisi = {}
ban_gecmisi = {}
kick_gecmisi = {}
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


def simdi():
    return datetime.now(timezone.utc)


def guard_aktif(guild_id: int) -> bool:
    return ayar_getir(guild_id).get('guard', True)


def limit_asildi(gecmis: dict, key, limit: int, sure: float) -> bool:
    t = time.monotonic()
    liste = [x for x in gecmis.get(key, []) if t - x < sure]
    liste.append(t)
    gecmis[key] = liste
    return len(liste) >= limit


def tehlikeli_rol_mu(role: discord.Role) -> bool:
    p = role.permissions
    return (
        p.administrator
        or p.manage_roles
        or p.manage_channels
        or p.manage_guild
        or p.ban_members
        or p.kick_members
        or p.manage_webhooks
    )


def zaman_donustur(zaman_str: str):
    birimler = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
    match = re.match(r'^(\d+)([smhd])$', zaman_str.lower())
    if not match:
        return None
    sure, birim = match.groups()
    return int(sure) * birimler[birim]


async def davetleri_guncelle(guild: discord.Guild):
    try:
        invites = await guild.invites()
        davet_hafizasi[guild.id] = {i.code: i.uses for i in invites}
    except Exception as e:
        print(f'Davetler alınamadı ({guild.name}): {e}')


# ---------------------------------------------------------
# 4. LOG + CEZA
# ---------------------------------------------------------
async def erensi_log_gonder(guild: discord.Guild, embed: discord.Embed):
    if not guild:
        return

    log_channel_id = ayar_getir(guild.id).get('log_channel_id')
    target = guild.get_channel(log_channel_id) if log_channel_id else None

    if not target:
        target = discord.utils.find(
            lambda c: 'log' in c.name.lower() and isinstance(c, discord.TextChannel),
            guild.channels,
        )

    if target:
        try:
            await target.send(embed=embed)
        except Exception as e:
            print(f'Log atılamadı: {e}')


def muaf_mi(guild: discord.Guild, user, kategori: str) -> bool:
    return (
        user.id == guild.owner_id
        or user.bot
        or user.id == bot.user.id
        or user.id == BOT_SAHIBI_ID
        or is_whitelisted(guild.id, user, kategori)
    )


async def ceza_uygula(
    guild: discord.Guild,
    user: discord.Member,
    sebep: str,
    action_category: str = 'All',
    target_name: str = 'Sunucu Yapısı',
):
    if muaf_mi(guild, user, action_category):
        return False

    # Ayni kisiye ust uste ceza/log olmasin
    if user.id in cezali_kullanicilar:
        return True
    cezali_kullanicilar.add(user.id)
    bot.loop.call_later(30, cezali_kullanicilar.discard, user.id)

    alinacak_roller = [
        r
        for r in user.roles
        if not r.is_default()
        and not r.managed
        and r < guild.me.top_role
        and (
            r.permissions.administrator
            or r.permissions.manage_roles
            or r.permissions.manage_channels
        )
    ]

    # Ban ve rol alma AYNI ANDA gider (eskiden sirayla gidiyordu = yavas)
    gorevler = [
        guild.ban(
            user,
            reason=f'🚨 Anti-Nuke: {sebep}',
            delete_message_seconds=86400,
        )
    ]
    if alinacak_roller:
        gorevler.append(
            user.remove_roles(*alinacak_roller, reason=f'GUARD/ANTI-RAID: {sebep}')
        )
    sonuclar = await asyncio.gather(*gorevler, return_exceptions=True)
    ban_basarili = not isinstance(sonuclar[0], Exception)
    if not ban_basarili:
        print(f'Banlanamadı ({user}): {sonuclar[0]} -> bot rolünü yukarı taşı!')

    embed = discord.Embed(
        title='🚨 • Anti-Nuke Güvenlik Müdahalesi!',
        color=0x992D22,
        timestamp=simdi(),
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(
        name='Kullanıcı:',
        value=f'{user.mention} (`{user.name}` - `{user.id}`)',
        inline=False,
    )
    embed.add_field(name='İhlal Edilen Kategori:', value=f'`{action_category}`')
    embed.add_field(name='Sebep:', value=f'```{sebep}```', inline=False)
    embed.add_field(name='Hedef:', value=f'`{target_name}`')
    embed.add_field(
        name='Yapılan İşlem:',
        value=(
            '🔴 **YETKİLERİ ALINDI VE BANLANDI**'
            if ban_basarili
            else '🟠 **YETKİLERİ ALINDI** (Ban başarısız: bot rolü yetersiz)'
        ),
        inline=False,
    )
    embed.set_footer(text=f'Kullanıcı ID: {user.id} • Guard Sentinel')
    await erensi_log_gonder(guild, embed)
    return True


async def nuke_denetle(
    guild, member, kategori, gecmis, limit, sure, sebep, hedef
) -> bool:
    """Limit asildiysa cezalandirir. Ceza verildiyse True doner."""
    if muaf_mi(guild, member, kategori):
        return False
    if not limit_asildi(gecmis, (guild.id, member.id), limit, sure):
        return False
    return await ceza_uygula(guild, member, sebep, kategori, hedef)


# ---------------------------------------------------------
# 5. ANTI-NUKE (AUDIT LOG - ANLIK TEPKI)
# ---------------------------------------------------------
# Eskiden audit log'u elle sorgulamak gerekiyordu ve kayit gec dusuyordu.
# on_audit_log_entry_create olayi Discord kaydi olusur olusmaz gonderir.
@bot.event
async def on_guild_channel_delete(channel):
    silinen_kanallar[channel.id] = channel
    if len(silinen_kanallar) > 100:
        silinen_kanallar.pop(next(iter(silinen_kanallar)))


@bot.event
async def on_guild_role_delete(role):
    silinen_roller[role.id] = role
    if len(silinen_roller) > 100:
        silinen_roller.pop(next(iter(silinen_roller)))


async def kanal_geri_yukle(guild: discord.Guild, kanal_id: int):
    kanal = None
    for _ in range(10):  # olay sirasi garanti degil, kisaca bekle
        kanal = silinen_kanallar.get(kanal_id)
        if kanal:
            break
        await asyncio.sleep(0.3)
    if not kanal:
        return
    try:
        if isinstance(kanal, discord.CategoryChannel):
            yeni = await guild.create_category(
                kanal.name, overwrites=kanal.overwrites, reason='Anti-Nuke geri yükleme'
            )
        elif isinstance(kanal, discord.TextChannel):
            yeni = await guild.create_text_channel(
                kanal.name,
                overwrites=kanal.overwrites,
                category=kanal.category,
                topic=kanal.topic,
                nsfw=kanal.nsfw,
                slowmode_delay=kanal.slowmode_delay,
                reason='Anti-Nuke geri yükleme',
            )
        elif isinstance(kanal, discord.VoiceChannel):
            yeni = await guild.create_voice_channel(
                kanal.name,
                overwrites=kanal.overwrites,
                category=kanal.category,
                bitrate=min(kanal.bitrate, guild.bitrate_limit),
                user_limit=kanal.user_limit,
                reason='Anti-Nuke geri yükleme',
            )
        else:
            return
        try:
            await yeni.edit(position=kanal.position)
        except Exception:
            pass
    except Exception as e:
        print(f'Kanal geri yüklenemedi: {e}')


async def rol_geri_yukle(guild: discord.Guild, rol_id: int):
    rol = None
    for _ in range(10):
        rol = silinen_roller.get(rol_id)
        if rol:
            break
        await asyncio.sleep(0.3)
    if not rol:
        return
    try:
        yeni = await guild.create_role(
            name=rol.name,
            permissions=rol.permissions,
            colour=rol.colour,
            hoist=rol.hoist,
            mentionable=rol.mentionable,
            reason='Anti-Nuke geri yükleme',
        )
        try:
            await yeni.edit(position=min(rol.position, guild.me.top_role.position - 1))
        except Exception:
            pass
    except Exception as e:
        print(f'Rol geri yüklenemedi: {e}')


@bot.event
async def on_audit_log_entry_create(entry: discord.AuditLogEntry):
    guild = entry.guild
    if not guild or entry.user_id is None or entry.user_id == bot.user.id:
        return
    if not guard_aktif(guild.id):
        return

    A = discord.AuditLogAction
    act = entry.action
    izlenen = {
        A.channel_delete,
        A.channel_create,
        A.role_delete,
        A.role_create,
        A.role_update,
        A.member_role_update,
        A.ban,
        A.kick,
        A.member_prune,
        A.bot_add,
        A.invite_create,
    }
    if act not in izlenen:
        return

    member = guild.get_member(entry.user_id)
    if member is None:
        try:
            member = await guild.fetch_member(entry.user_id)
        except Exception:
            return

    hedef_ad = getattr(entry.before, 'name', None) or str(entry.target.id if entry.target else '?')

    if act == A.channel_delete:
        if await nuke_denetle(
            guild, member, 'Channel-Delete', kanal_silme_gecmisi, 1, 10,
            'İzinsiz kanal silme', hedef_ad,
        ):
            await kanal_geri_yukle(guild, entry.target.id)

    elif act == A.channel_create:
        if await nuke_denetle(
            guild, member, 'Channel-Create', kanal_olusturma_gecmisi, 3, 10,
            'Hızlı/izinsiz kanal oluşturma', hedef_ad,
        ):
            k = guild.get_channel(entry.target.id)
            if k:
                try:
                    await k.delete(reason='Anti-Nuke')
                except Exception:
                    pass

    elif act == A.role_delete:
        if await nuke_denetle(
            guild, member, 'Role-Delete', rol_silme_gecmisi, 1, 10,
            'İzinsiz rol silme', hedef_ad,
        ):
            await rol_geri_yukle(guild, entry.target.id)

    elif act == A.role_create:
        if await nuke_denetle(
            guild, member, 'Role-Create', rol_olusturma_gecmisi, 3, 10,
            'Hızlı/izinsiz rol oluşturma', hedef_ad,
        ):
            r = guild.get_role(entry.target.id)
            if r:
                try:
                    await r.delete(reason='Anti-Nuke')
                except Exception:
                    pass

    elif act == A.role_update:
        onceki = getattr(entry.before, 'permissions', None)
        sonraki = getattr(entry.after, 'permissions', None)
        if sonraki and sonraki.administrator and not (onceki and onceki.administrator):
            if not muaf_mi(guild, member, 'Role-Update'):
                await ceza_uygula(
                    guild, member, 'Role Administrator yetkisi verme',
                    'Role-Update', hedef_ad,
                )
                r = guild.get_role(entry.target.id)
                if r and onceki is not None:
                    try:
                        await r.edit(permissions=onceki, reason='Anti-Nuke geri alma')
                    except Exception:
                        pass

    elif act == A.member_role_update:
        eklenenler = [
            guild.get_role(r.id) for r in (getattr(entry.after, 'roles', None) or [])
        ]
        tehlikeli = [r for r in eklenenler if r and tehlikeli_rol_mu(r)]
        if tehlikeli and not muaf_mi(guild, member, 'Role-Add'):
            await ceza_uygula(
                guild, member, 'İzinsiz yetkili rol verme', 'Role-Add',
                ', '.join(r.name for r in tehlikeli),
            )
            hedef = guild.get_member(entry.target.id)
            if hedef:
                try:
                    await hedef.remove_roles(*tehlikeli, reason='Anti-Nuke geri alma')
                except Exception:
                    pass

    elif act == A.ban:
        await nuke_denetle(
            guild, member, 'Ban', ban_gecmisi, 3, 15,
            'Kısa sürede toplu ban', str(entry.target.id),
        )

    elif act == A.kick:
        await nuke_denetle(
            guild, member, 'Kick', kick_gecmisi, 3, 15,
            'Kısa sürede toplu kick', str(entry.target.id),
        )

    elif act == A.member_prune:
        if not muaf_mi(guild, member, 'Prune'):
            await ceza_uygula(guild, member, 'Toplu üye çıkarma (Prune)', 'Prune')

    elif act == A.bot_add:
        if not muaf_mi(guild, member, 'Bot-Add'):
            await ceza_uygula(guild, member, 'İzinsiz bot ekleme', 'Bot-Add', hedef_ad)
            eklenen = guild.get_member(entry.target.id)
            if eklenen:
                try:
                    await eklenen.kick(reason='Anti-Nuke: izinsiz bot')
                except Exception:
                    pass

    elif act == A.invite_create:
        if not muaf_mi(guild, member, 'Invite-Link'):
            kod = getattr(entry.after, 'code', None)
            if kod:
                try:
                    await bot.http.delete_invite(kod, reason='Anti-Nuke: izinsiz davet')
                except Exception:
                    pass
            embed = discord.Embed(
                title='🔗 • İzinsiz Davet Linki Silindi',
                color=0xE67E22,
                timestamp=simdi(),
            )
            embed.add_field(name='Kullanıcı:', value=member.mention)
            embed.add_field(name='Kod:', value=f'`{kod}`')
            await erensi_log_gonder(guild, embed)


# ---------------------------------------------------------
# 6. MESAJ KORUMASI (LINK / ETIKET / SPAM)
# ---------------------------------------------------------
async def mesaj_denetle(message: discord.Message) -> bool:
    """Mesaj silindiyse True doner."""
    guild = message.guild
    member = message.author
    if member.id in (guild.owner_id, BOT_SAHIBI_ID):
        return False

    # @everyone / @here
    if message.mention_everyone and not is_whitelisted(guild.id, member, 'Mention'):
        try:
            await message.delete()
            await member.timeout(timedelta(minutes=10), reason='İzinsiz @everyone/@here')
        except Exception:
            pass
        await message.channel.send(
            f'⚠️ {member.mention} `@everyone/@here` kullanma yetkin yok!',
            delete_after=5,
        )
        return True

    # Link
    kanal_adi = message.channel.name.lower() if hasattr(message.channel, 'name') else ''
    ticket_kanali = any(k in kanal_adi for k in TICKET_KANAL_KEYWORDS)
    if (
        not ticket_kanali
        and LINK_REGEX.search(message.content)
        and not is_whitelisted(guild.id, member, 'Link')
    ):
        try:
            await message.delete()
        except Exception:
            pass
        await message.channel.send(
            f'⚠️ {member.mention} link paylaşma yetkin yok!', delete_after=5
        )
        embed = discord.Embed(
            title='🔗 • İzinsiz Link Silindi', color=0xE67E22, timestamp=simdi()
        )
        embed.add_field(name='Kullanıcı:', value=member.mention)
        embed.add_field(name='Kanal:', value=message.channel.mention)
        embed.add_field(name='İçerik:', value=f'```{message.content[:300]}```', inline=False)
        await erensi_log_gonder(guild, embed)
        return True

    # Toplu etiket
    if len(message.mentions) >= 5 and not is_whitelisted(guild.id, member, 'Mention'):
        try:
            await message.delete()
            await member.timeout(timedelta(minutes=10), reason='Etiket spam')
        except Exception:
            pass
        return True

    # Mesaj spam (5 sn'de 6 mesaj)
    if not is_whitelisted(guild.id, member, 'All'):
        if limit_asildi(mesaj_gecmisi, (guild.id, member.id), 6, 5):
            mesaj_gecmisi[(guild.id, member.id)] = []
            try:
                await member.timeout(timedelta(minutes=5), reason='Mesaj spam')
                await message.channel.purge(
                    limit=15, check=lambda m: m.author.id == member.id
                )
            except Exception:
                pass
            await message.channel.send(
                f'🔇 {member.mention} spam yaptığı için 5 dk susturuldu.',
                delete_after=5,
            )
            return True
    return False


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    if isinstance(message.author, discord.Member) and guard_aktif(message.guild.id):
        if await mesaj_denetle(message):
            return
    await bot.process_commands(message)


# ---------------------------------------------------------
# 7. GELEN / GIDEN / OTOROL / DAVET TAKIBI
# ---------------------------------------------------------
@bot.event
async def on_invite_create(invite):
    davet_hafizasi.setdefault(invite.guild.id, {})[invite.code] = invite.uses


@bot.event
async def on_invite_delete(invite):
    davet_hafizasi.get(invite.guild.id, {}).pop(invite.code, None)


async def davet_eden_bul(guild: discord.Guild):
    try:
        yeni = await guild.invites()
    except Exception:
        return None
    eski = davet_hafizasi.get(guild.id, {})
    bulunan = None
    for inv in yeni:
        if inv.uses > eski.get(inv.code, 0):
            bulunan = inv
            break
    davet_hafizasi[guild.id] = {i.code: i.uses for i in yeni}
    return bulunan


@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild
    ayar = ayar_getir(guild.id)

    if ayar.get('otorol_id'):
        rol = guild.get_role(ayar['otorol_id'])
        if rol and rol < guild.me.top_role:
            try:
                await member.add_roles(rol, reason='Otorol')
            except Exception as e:
                print(f'Otorol verilemedi: {e}')

    davet = await davet_eden_bul(guild)

    kanal = guild.get_channel(ayar['gelen_giden_kanal']) if ayar.get('gelen_giden_kanal') else None
    embed = discord.Embed(
        title='📥 • Sunucuya Yeni Üye Katıldı!', color=0x2ECC71, timestamp=simdi()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name='Kullanıcı:', value=f'{member.mention} (`{member.name}`)')
    embed.add_field(name='Toplam Üye Sayısı:', value=f'`{guild.member_count}`')
    if davet and davet.inviter:
        embed.add_field(
            name='Davet Eden:',
            value=f'{davet.inviter.mention} (`{davet.uses}` davet)',
            inline=False,
        )
    embed.add_field(
        name='Hesap Açılış:', value=discord.utils.format_dt(member.created_at, 'R'), inline=False
    )
    embed.set_footer(text=f'Kullanıcı ID: {member.id}')
    if kanal:
        try:
            await kanal.send(embed=embed)
        except Exception:
            pass
    await erensi_log_gonder(guild, embed)


@bot.event
async def on_member_remove(member: discord.Member):
    guild = member.guild
    ayar = ayar_getir(guild.id)
    kanal = guild.get_channel(ayar['gelen_giden_kanal']) if ayar.get('gelen_giden_kanal') else None

    embed = discord.Embed(
        title='📤 • Bir Üye Sunucudan Ayrıldı!', color=0xE74C3C, timestamp=simdi()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name='Kullanıcı:', value=f'{member.mention} (`{member.name}`)')
    embed.add_field(name='Kalan Üye Sayısı', value=f'`{guild.member_count}`')
    embed.set_footer(text=f'Kullanıcı ID: {member.id}')
    if kanal:
        try:
            await kanal.send(embed=embed)
        except Exception:
            pass
    await erensi_log_gonder(guild, embed)


# ---------------------------------------------------------
# 8. TICKET
# ---------------------------------------------------------
class TicketKapatView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label='🔒 Talebi Kapat',
        style=discord.ButtonStyle.red,
        custom_id='ticket_kapat_buton',
    )
    async def ticket_kapat(self, interaction: discord.Interaction, button: discord.ui.Button):
        kanal = interaction.channel
        izin = kanal.permissions_for(interaction.user)
        if not (
            izin.manage_channels
            or interaction.user.id == BOT_SAHIBI_ID
            or kanal.name.endswith(interaction.user.name.lower())
        ):
            return await interaction.response.send_message(
                '❌ Bu talebi kapatma yetkin yok.', ephemeral=True
            )

        await interaction.response.send_message('Destek kanalı 5 saniye içinde siliniyor...')
        embed = discord.Embed(title='🎫 • Ticket Kapatıldı!', color=0xFFA500, timestamp=simdi())
        embed.add_field(name='Kanal:', value=f'`#{kanal.name}`')
        embed.add_field(name='Kapatan Yetkili:', value=interaction.user.mention)
        embed.set_footer(text=f'Kullanıcı ID: {interaction.user.id}')
        await erensi_log_gonder(interaction.guild, embed)
        await asyncio.sleep(5)
        try:
            await kanal.delete()
        except Exception as e:
            print(f'Ticket silinemedi: {e}')


class TicketView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    async def kanal_olustur(self, interaction: discord.Interaction, kategori_adi: str):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass

        try:
            guild = interaction.guild
            user = interaction.user
            kanal_adi = f'{kategori_adi}-{user.name.lower()}'

            mevcut = discord.utils.get(guild.text_channels, name=kanal_adi)
            if mevcut:
                return await interaction.followup.send(
                    f'Zaten açık bir **{kategori_adi}** biletiniz var: {mevcut.mention}',
                    ephemeral=True,
                )

            CATEGORY_ID = 1479876130323431616
            category = guild.get_channel(CATEGORY_ID)

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            }

            if isinstance(category, discord.CategoryChannel):
                ticket_channel = await category.create_text_channel(
                    name=kanal_adi, overwrites=overwrites
                )
            else:
                ticket_channel = await guild.create_text_channel(
                    name=kanal_adi, overwrites=overwrites
                )

            await interaction.followup.send(
                f'**{kategori_adi.upper()}** talebiniz oluşturuldu: {ticket_channel.mention}',
                ephemeral=True,
            )

            rol_etiketleri = ''
            if kategori_adi == 'ekip-alim':
                rol_etiketleri = '<@&1545087594575175780>'
            elif kategori_adi == 'merge-ally':
                rol_etiketleri = '<@&1545087593211887659> <@&1545087322809438258>'
            elif kategori_adi in ['partner', 'genel-destek', 'reklam']:
                rol_etiketleri = '<@&1545087590460293120>'

            mesaj = (
                f'Merhaba {user.mention} {rol_etiketleri},'
                f' **{kategori_adi.upper()}** talebiniz alındı. Yetkililer en kısa'
                ' sürede sizinle ilgilenecektir.\n\n'
            )

            if kategori_adi == 'ekip-alim':
                mesaj += (
                    '📋 **Lütfen başvuru formunu doldurun:**\n```\nİsim: \nYaş: \nTier:'
                    ' \nGünlük Aktiflik Süren: \nMobil/Pc: \nÖnceki Ulusun: \nNeden'
                    ' Ayrıldın: \nNeden Biz:\n```\n'
                )
            elif kategori_adi == 'partner':
                mesaj += '📋 **Sunucunuz kaç kişi?**\n'
            elif kategori_adi == 'merge-ally':
                mesaj += (
                    '📋 **Günlük kaç aktif çekiyorsunuz?**\n **Hangi Sunucuları'
                    ' Oynuyorsunuz?**\n'
                )
            elif kategori_adi == 'reklam':
                mesaj += '📋 **Hangi paketi alacaksınız?**\n'
            elif kategori_adi == 'genel-destek':
                mesaj += '📋 **Sorununuz nedir?**\n'

            mesaj += '\nTalebi sonlandırmak için aşağıdaki butona basabilirsiniz.'
            await ticket_channel.send(
                mesaj,
                view=TicketKapatView(),
                allowed_mentions=discord.AllowedMentions(users=True, roles=True),
            )

            embed = discord.Embed(
                title='🎫 • Yeni Bir Ticket Açıldı!', color=0x2ECC71, timestamp=simdi()
            )
            embed.add_field(name='Kullanıcı:', value=user.mention)
            embed.add_field(name='Kategori:', value=f'`{kategori_adi.upper()}`')
            embed.add_field(name='Kanal:', value=ticket_channel.mention)
            embed.set_footer(text=f'Kullanıcı ID: {user.id}')
            await erensi_log_gonder(guild, embed)

        except Exception as e:
            print(f'❌ Ticket oluşturulurken hata: {e}')
            try:
                await interaction.followup.send(
                    '❌ Ticket oluşturulamadı (botun kanal oluşturma yetkisini kontrol edin).',
                    ephemeral=True,
                )
            except Exception:
                pass

    @discord.ui.button(label='⚔️ Ekip Alım', style=discord.ButtonStyle.red, custom_id='btn_ekip_alim')
    async def ekip_alim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.kanal_olustur(interaction, 'ekip-alim')

    @discord.ui.button(label='🛡️ Merge / Ally', style=discord.ButtonStyle.green, custom_id='btn_merge_ally')
    async def merge_ally(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.kanal_olustur(interaction, 'merge-ally')

    @discord.ui.button(label='🤝 Partner', style=discord.ButtonStyle.blurple, custom_id='btn_partner')
    async def partner(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.kanal_olustur(interaction, 'partner')

    @discord.ui.button(label='💸 Reklam', style=discord.ButtonStyle.blurple, custom_id='btn_reklam')
    async def reklam(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.kanal_olustur(interaction, 'reklam')

    @discord.ui.button(label='💠 Genel Destek', style=discord.ButtonStyle.secondary, custom_id='btn_genel_destek')
    async def genel_destek(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.kanal_olustur(interaction, 'genel-destek')


# ---------------------------------------------------------
# 9. YETKI KONTROL YARDIMCILARI
# ---------------------------------------------------------
def sahip_mi(interaction: discord.Interaction) -> bool:
    return interaction.user.id in (interaction.guild.owner_id, BOT_SAHIBI_ID)


def yetkili_mi(interaction: discord.Interaction, perm: str, kategori: str) -> bool:
    u = interaction.user
    return (
        sahip_mi(interaction)
        or getattr(u.guild_permissions, perm, False)
        or is_whitelisted(interaction.guild.id, u, kategori)
    )


def hedef_kontrol(interaction: discord.Interaction, hedef: discord.Member) -> str:
    """Sorun varsa hata mesaji, yoksa bos string doner."""
    if hedef.id == interaction.guild.owner_id:
        return '❌ Sunucu sahibine işlem yapılamaz.'
    if hedef.id == bot.user.id:
        return '❌ Bana işlem yapamazsın.'
    if interaction.guild.me.top_role <= hedef.top_role:
        return '❌ Bu kullanıcının rolü benden yüksek/eşit. Bot rolünü yukarı taşıyın.'
    if not sahip_mi(interaction) and interaction.user.top_role <= hedef.top_role:
        return '❌ Kendi rolünüzden yüksek/eşit birine işlem yapamazsınız.'
    return ''


def whitelist_yetki_ve_hiyerarsi_kontrolu(
    interaction: discord.Interaction, target_role: discord.Role
) -> tuple:
    user = interaction.user
    guild = interaction.guild

    if not (
        sahip_mi(interaction)
        or user.guild_permissions.administrator
        or is_whitelisted(guild.id, user, 'Role-Command')
    ):
        return (
            False,
            '❌ Bu komut için `/whitelist` listesinde `Rol Verme/Alma Komutu (Role-Command)` yetkin olmalı!',
        )
    if target_role.managed or target_role.is_default():
        return False, '❌ Bu rol elle verilip alınamaz (bot/entegrasyon rolü veya @everyone).'
    if guild.me.top_role <= target_role:
        return False, '❌ Bu rol benim en yüksek rolümden üstte veya eşit! Bot rolünü yukarı taşıyın.'
    if not sahip_mi(interaction) and user.top_role <= target_role:
        return False, '❌ Kendi en yüksek rolünüzden yüksek veya eşit bir rolü veremez/alamazsınız!'
    return True, ''


# ---------------------------------------------------------
# 10. WHITELIST KOMUTLARI
# ---------------------------------------------------------
WHITELIST_CHOICES = [
    app_commands.Choice(name='Kullanıcı/Rol: Tüm Yetkiler (Tam Güven)', value='All'),
    app_commands.Choice(name='İşlem: Bağlantı/Link Paylaşma', value='Link'),
    app_commands.Choice(name='İşlem: Etiket Yetkisi (@everyone/@here)', value='Mention'),
    app_commands.Choice(name='İşlem: Rol Verme/Alma Komutu (/rolver /rolal)', value='Role-Command'),
    app_commands.Choice(name='İşlem: Üye Yasaklama (Ban)', value='Ban'),
    app_commands.Choice(name='İşlem: Üye Atma (Kick)', value='Kick'),
    app_commands.Choice(name='İşlem: Bot Ekleme', value='Bot-Add'),
    app_commands.Choice(name='İşlem: Rol Güncelleme', value='Role-Update'),
    app_commands.Choice(name='İşlem: Yetkili Rol Verme', value='Role-Add'),
    app_commands.Choice(name='İşlem: Kanal Oluşturma', value='Channel-Create'),
    app_commands.Choice(name='İşlem: Kanal Silme', value='Channel-Delete'),
    app_commands.Choice(name='İşlem: Rol Oluşturma', value='Role-Create'),
    app_commands.Choice(name='İşlem: Rol Silme', value='Role-Delete'),
    app_commands.Choice(name='İşlem: Davet Linki Açma', value='Invite-Link'),
    app_commands.Choice(name='İşlem: Toplu Üye Çıkarma (Prune)', value='Prune'),
]


class WhitelistGroup(app_commands.Group):

    def __init__(self):
        super().__init__(
            name='whitelist',
            description='Anti-nuke ve işlem whitelist yetki yönetimi',
            guild_only=True,
        )

    @app_commands.command(name='add_user', description='Kullanıcıya belirli bir eylem için yetki tanımlar.')
    @app_commands.describe(user='Yetkilendirilecek üye', category='Yetki kategorisi')
    @app_commands.choices(category=WHITELIST_CHOICES)
    async def add_user(self, interaction: discord.Interaction, user: discord.Member, category: app_commands.Choice[str]):
        if not sahip_mi(interaction):
            return await interaction.response.send_message('❌ Bu komutu yalnızca Sunucu Sahibi kullanabilir.', ephemeral=True)

        g = ayar_getir(interaction.guild_id)
        perms = g['users'].setdefault(str(user.id), [])
        if category.value in perms:
            return await interaction.response.send_message(
                f'⚠️ {user.mention} zaten `{category.name}` yetkisine sahip.', ephemeral=True
            )
        perms.append(category.value)
        save_whitelist()

        embed = discord.Embed(
            title='🛡️ • Whitelist Güncellendi!',
            description=f'{user.mention} kullanıcısına **`{category.name}`** izni verildi.',
            color=0x2ECC71,
            timestamp=simdi(),
        )
        embed.set_footer(text=f'Yetki Veren: {interaction.user.name}')
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name='add_role', description='Bir role toplu yetki tanımlar (Örn: Link izni).')
    @app_commands.describe(role='Yetkilendirilecek rol', category='Yetki türü')
    @app_commands.choices(category=WHITELIST_CHOICES)
    async def add_role(self, interaction: discord.Interaction, role: discord.Role, category: app_commands.Choice[str]):
        if not sahip_mi(interaction):
            return await interaction.response.send_message('❌ Bu komutu yalnızca Sunucu Sahibi kullanabilir.', ephemeral=True)

        g = ayar_getir(interaction.guild_id)
        perms = g['roles'].setdefault(str(role.id), [])
        if category.value in perms:
            return await interaction.response.send_message(
                f'⚠️ {role.mention} rolü zaten `{category.name}` yetkisine sahip.', ephemeral=True
            )
        perms.append(category.value)
        save_whitelist()

        embed = discord.Embed(
            title='🛡️ • Role Whitelist Yetkisi Verildi!',
            description=f'{role.mention} rolüne sahip herkes için **`{category.name}`** izni aktif edildi.',
            color=0x2ECC71,
            timestamp=simdi(),
        )
        embed.set_footer(text=f'Yetki Veren: {interaction.user.name}')
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name='remove_user', description='Kullanıcının whitelist yetkilerini kaldırır.')
    async def remove_user(self, interaction: discord.Interaction, user: discord.Member):
        if not sahip_mi(interaction):
            return await interaction.response.send_message('❌ Bu komutu yalnızca Sunucu Sahibi kullanabilir.', ephemeral=True)

        g = ayar_getir(interaction.guild_id)
        if str(user.id) in g['users']:
            del g['users'][str(user.id)]
            save_whitelist()
            return await interaction.response.send_message(
                f'🗑️ {user.mention} kullanıcısının tüm whitelist yetkileri kaldırıldı.'
            )
        await interaction.response.send_message('❌ Bu kullanıcı whitelist listesinde bulunamadı.', ephemeral=True)

    @app_commands.command(name='remove_role', description='Bir rolün whitelist yetkilerini kaldırır.')
    async def remove_role(self, interaction: discord.Interaction, role: discord.Role):
        if not sahip_mi(interaction):
            return await interaction.response.send_message('❌ Bu komutu yalnızca Sunucu Sahibi kullanabilir.', ephemeral=True)

        g = ayar_getir(interaction.guild_id)
        if str(role.id) in g['roles']:
            del g['roles'][str(role.id)]
            save_whitelist()
            return await interaction.response.send_message(
                f'🗑️ {role.mention} rolünün tüm whitelist yetkileri kaldırıldı.'
            )
        await interaction.response.send_message('❌ Bu rol whitelist listesinde bulunamadı.', ephemeral=True)

    @app_commands.command(name='list', description='Sunucudaki whitelist kullanıcı ve rollerini listeler.')
    async def list_users(self, interaction: discord.Interaction):
        g = ayar_getir(interaction.guild_id)
        users, roles = g['users'], g['roles']

        if not users and not roles:
            return await interaction.response.send_message(
                'ℹ️ Whitelist listesinde hiç yetkili kullanıcı veya rol yok.', ephemeral=True
            )

        desc = '### 👤 Yetkili Kullanıcılar:\n'
        if users:
            for u_id, perms in users.items():
                desc += f'• <@{u_id}>: ' + ', '.join(f'`{p}`' for p in perms) + '\n'
        else:
            desc += '*Ekli kullanıcı yok.*\n'

        desc += '\n### 🛡️ Yetkili Roller:\n'
        if roles:
            for r_id, perms in roles.items():
                desc += f'• <@&{r_id}>: ' + ', '.join(f'`{p}`' for p in perms) + '\n'
        else:
            desc += '*Ekli rol yok.*\n'

        embed = discord.Embed(
            title='📜 • Whitelist Yetki Listesi',
            description=desc[:4000],
            color=0x3498DB,
            timestamp=simdi(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


bot.tree.add_command(WhitelistGroup())


# ---------------------------------------------------------
# 11. AYAR KOMUTLARI
# ---------------------------------------------------------
@bot.tree.command(name='set_log_channel', description='Tüm sunucu ve güvenlik loglarının gönderileceği kanalı ayarlar.')
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def set_log_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not (sahip_mi(interaction) or interaction.user.guild_permissions.administrator):
        return await interaction.response.send_message('❌ Bu ayarı sadece yöneticiler değiştirebilir.', ephemeral=True)

    ayar_getir(interaction.guild_id)['log_channel_id'] = channel.id
    save_whitelist()
    await interaction.response.send_message(
        f'📋 **Sunucu Log Kanalı Ayarlandı:** {channel.mention}', ephemeral=True
    )


@bot.tree.command(name='gelen_giden_kanal', description='Giriş/çıkış mesajlarının atılacağı kanalı ayarlar.')
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def gelen_giden_kanal(interaction: discord.Interaction, channel: discord.TextChannel):
    if not (sahip_mi(interaction) or interaction.user.guild_permissions.administrator):
        return await interaction.response.send_message('❌ Yalnızca yöneticiler.', ephemeral=True)
    ayar_getir(interaction.guild_id)['gelen_giden_kanal'] = channel.id
    save_whitelist()
    await interaction.response.send_message(f'✅ Gelen/Giden kanalı: {channel.mention}', ephemeral=True)


@bot.tree.command(name='otorol', description='Sunucuya girenlere otomatik verilecek rolü ayarlar.')
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def otorol(interaction: discord.Interaction, role: discord.Role):
    if not (sahip_mi(interaction) or interaction.user.guild_permissions.administrator):
        return await interaction.response.send_message('❌ Yalnızca yöneticiler.', ephemeral=True)
    if role.managed or role >= interaction.guild.me.top_role:
        return await interaction.response.send_message('❌ Bu rolü ben veremem (rolüm yetersiz).', ephemeral=True)
    ayar_getir(interaction.guild_id)['otorol_id'] = role.id
    save_whitelist()
    await interaction.response.send_message(f'✅ Otorol: {role.mention}', ephemeral=True)


@bot.tree.command(name='guard', description='Anti-nuke ve mesaj korumasını açar/kapatır.')
@app_commands.describe(durum='Açık / Kapalı')
@app_commands.choices(
    durum=[app_commands.Choice(name='Açık', value='ac'), app_commands.Choice(name='Kapalı', value='kapat')]
)
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def guard_komut(interaction: discord.Interaction, durum: app_commands.Choice[str]):
    if not sahip_mi(interaction):
        return await interaction.response.send_message('❌ Yalnızca Sunucu Sahibi.', ephemeral=True)
    ayar_getir(interaction.guild_id)['guard'] = durum.value == 'ac'
    save_whitelist()
    await interaction.response.send_message(f'🛡️ Guard artık **{durum.name}**.', ephemeral=True)


@bot.tree.command(name='ticket_kur', description='Bu kanala ticket panelini gönderir.')
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def ticket_kur(interaction: discord.Interaction):
    if not (sahip_mi(interaction) or interaction.user.guild_permissions.administrator):
        return await interaction.response.send_message('❌ Yalnızca yöneticiler.', ephemeral=True)
    embed = discord.Embed(
        title='🎫 • Destek Merkezi',
        description='Aşağıdaki butonlardan ilgili kategoriyi seçerek talep oluşturabilirsiniz.',
        color=0x3498DB,
    )
    await interaction.channel.send(embed=embed, view=TicketView())
    await interaction.response.send_message('✅ Panel gönderildi.', ephemeral=True)


# ---------------------------------------------------------
# 12. ROL VER / ROL AL
# ---------------------------------------------------------
@bot.tree.command(name='rolver', description='Bir üyeye rol verir.')
@app_commands.describe(uye='Rol verilecek üye', rol='Verilecek rol')
@app_commands.guild_only()
async def rolver(interaction: discord.Interaction, uye: discord.Member, rol: discord.Role):
    ok, msg = whitelist_yetki_ve_hiyerarsi_kontrolu(interaction, rol)
    if not ok:
        return await interaction.response.send_message(msg, ephemeral=True)
    if rol in uye.roles:
        return await interaction.response.send_message('⚠️ Üyede bu rol zaten var.', ephemeral=True)
    await uye.add_roles(rol, reason=f'/rolver - {interaction.user}')
    await interaction.response.send_message(f'✅ {uye.mention} kullanıcısına {rol.mention} verildi.')

    embed = discord.Embed(title='🎭 • Rol Verildi', color=0x2ECC71, timestamp=simdi())
    embed.add_field(name='Yetkili:', value=interaction.user.mention)
    embed.add_field(name='Üye:', value=uye.mention)
    embed.add_field(name='Rol:', value=rol.mention)
    await erensi_log_gonder(interaction.guild, embed)


@bot.tree.command(name='rolal', description='Bir üyeden rol alır.')
@app_commands.describe(uye='Rolü alınacak üye', rol='Alınacak rol')
@app_commands.guild_only()
async def rolal(interaction: discord.Interaction, uye: discord.Member, rol: discord.Role):
    ok, msg = whitelist_yetki_ve_hiyerarsi_kontrolu(interaction, rol)
    if not ok:
        return await interaction.response.send_message(msg, ephemeral=True)
    if rol not in uye.roles:
        return await interaction.response.send_message('⚠️ Üyede bu rol yok.', ephemeral=True)
    await uye.remove_roles(rol, reason=f'/rolal - {interaction.user}')
    await interaction.response.send_message(f'✅ {uye.mention} kullanıcısından {rol.mention} alındı.')

    embed = discord.Embed(title='🎭 • Rol Alındı', color=0xE67E22, timestamp=simdi())
    embed.add_field(name='Yetkili:', value=interaction.user.mention)
    embed.add_field(name='Üye:', value=uye.mention)
    embed.add_field(name='Rol:', value=rol.mention)
    await erensi_log_gonder(interaction.guild, embed)


# ---------------------------------------------------------
# 13. MODERASYON KOMUTLARI
# ---------------------------------------------------------
async def mod_log(interaction, baslik, renk, hedef, sebep, ekstra=None):
    embed = discord.Embed(title=baslik, color=renk, timestamp=simdi())
    embed.add_field(name='Yetkili:', value=interaction.user.mention)
    embed.add_field(name='Hedef:', value=f'{hedef} (`{getattr(hedef, "id", "")}`)')
    if ekstra:
        embed.add_field(name='Süre:', value=ekstra)
    embed.add_field(name='Sebep:', value=sebep, inline=False)
    await erensi_log_gonder(interaction.guild, embed)


@bot.tree.command(name='ban', description='Kullanıcıyı yasaklar.')
@app_commands.describe(uye='Yasaklanacak üye', sebep='Sebep')
@app_commands.default_permissions(ban_members=True)
@app_commands.guild_only()
async def ban_komut(interaction: discord.Interaction, uye: discord.Member, sebep: str = 'Sebep belirtilmedi'):
    if not yetkili_mi(interaction, 'ban_members', 'Ban'):
        return await interaction.response.send_message('❌ Yetkin yok.', ephemeral=True)
    hata = hedef_kontrol(interaction, uye)
    if hata:
        return await interaction.response.send_message(hata, ephemeral=True)
    try:
        await uye.ban(reason=f'{interaction.user}: {sebep}', delete_message_seconds=0)
    except Exception as e:
        return await interaction.response.send_message(f'❌ Banlanamadı: `{e}`', ephemeral=True)
    await interaction.response.send_message(f'🔨 **{uye}** yasaklandı. Sebep: {sebep}')
    await mod_log(interaction, '🔨 • Üye Yasaklandı', 0xE74C3C, uye, sebep)


@bot.tree.command(name='unban', description='Kullanıcının yasağını kaldırır (ID ile).')
@app_commands.describe(kullanici_id='Yasağı kaldırılacak kullanıcı ID')
@app_commands.default_permissions(ban_members=True)
@app_commands.guild_only()
async def unban_komut(interaction: discord.Interaction, kullanici_id: str):
    if not yetkili_mi(interaction, 'ban_members', 'Ban'):
        return await interaction.response.send_message('❌ Yetkin yok.', ephemeral=True)
    try:
        await interaction.guild.unban(discord.Object(id=int(kullanici_id)), reason=str(interaction.user))
    except Exception as e:
        return await interaction.response.send_message(f'❌ Yasak kaldırılamadı: `{e}`', ephemeral=True)
    await interaction.response.send_message(f'✅ `{kullanici_id}` yasağı kaldırıldı.')
    await mod_log(interaction, '♻️ • Yasak Kaldırıldı', 0x2ECC71, f'`{kullanici_id}`', '-')


@bot.tree.command(name='kick', description='Kullanıcıyı sunucudan atar.')
@app_commands.describe(uye='Atılacak üye', sebep='Sebep')
@app_commands.default_permissions(kick_members=True)
@app_commands.guild_only()
async def kick_komut(interaction: discord.Interaction, uye: discord.Member, sebep: str = 'Sebep belirtilmedi'):
    if not yetkili_mi(interaction, 'kick_members', 'Kick'):
        return await interaction.response.send_message('❌ Yetkin yok.', ephemeral=True)
    hata = hedef_kontrol(interaction, uye)
    if hata:
        return await interaction.response.send_message(hata, ephemeral=True)
    try:
        await uye.kick(reason=f'{interaction.user}: {sebep}')
    except Exception as e:
        return await interaction.response.send_message(f'❌ Atılamadı: `{e}`', ephemeral=True)
    await interaction.response.send_message(f'👢 **{uye}** atıldı. Sebep: {sebep}')
    await mod_log(interaction, '👢 • Üye Atıldı', 0xE67E22, uye, sebep)


@bot.tree.command(name='timeout', description='Üyeyi susturur. Süre örn: 10m, 2h, 1d')
@app_commands.describe(uye='Susturulacak üye', sure='10s / 10m / 2h / 1d', sebep='Sebep')
@app_commands.default_permissions(moderate_members=True)
@app_commands.guild_only()
async def timeout_komut(interaction: discord.Interaction, uye: discord.Member, sure: str, sebep: str = 'Sebep belirtilmedi'):
    if not yetkili_mi(interaction, 'moderate_members', 'Kick'):
        return await interaction.response.send_message('❌ Yetkin yok.', ephemeral=True)
    saniye = zaman_donustur(sure)
    if not saniye or saniye > 28 * 86400:
        return await interaction.response.send_message('❌ Geçersiz süre. Örn: `10m`, `2h`, `1d` (en fazla 28d).', ephemeral=True)
    hata = hedef_kontrol(interaction, uye)
    if hata:
        return await interaction.response.send_message(hata, ephemeral=True)
    try:
        await uye.timeout(timedelta(seconds=saniye), reason=f'{interaction.user}: {sebep}')
    except Exception as e:
        return await interaction.response.send_message(f'❌ Susturulamadı: `{e}`', ephemeral=True)
    await interaction.response.send_message(f'🔇 **{uye}** {sure} susturuldu. Sebep: {sebep}')
    await mod_log(interaction, '🔇 • Üye Susturuldu', 0xF1C40F, uye, sebep, sure)


@bot.tree.command(name='temizle', description='Kanaldaki mesajları siler.')
@app_commands.describe(adet='Silinecek mesaj sayısı (1-100)')
@app_commands.default_permissions(manage_messages=True)
@app_commands.guild_only()
async def temizle(interaction: discord.Interaction, adet: app_commands.Range[int, 1, 100]):
    if not interaction.user.guild_permissions.manage_messages and not sahip_mi(interaction):
        return await interaction.response.send_message('❌ Yetkin yok.', ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    silinen = await interaction.channel.purge(limit=adet)
    await interaction.followup.send(f'🧹 `{len(silinen)}` mesaj silindi.', ephemeral=True)


# ---------------------------------------------------------
# 14. TOPLU DM
# ---------------------------------------------------------
@bot.tree.command(name='duyuru-dm', description='Sunucudaki tüm üyelere özel mesaj (DM) olarak duyuru iletir.')
@app_commands.describe(mesaj='Gönderilecek duyuru metni')
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def duyuru_dm(interaction: discord.Interaction, mesaj: str):
    if not sahip_mi(interaction):
        return await interaction.response.send_message('❌ Bu komutu yalnızca Sunucu Sahibi kullanabilir.', ephemeral=True)

    await interaction.response.send_message(
        '🚀 **Toplu DM Gönderimi Başlatıldı!** Tamamlanınca log kanalına bilgi düşecek.',
        ephemeral=True,
    )

    guild = interaction.guild
    gonderen = interaction.user
    basarili = 0
    basarisiz = 0

    for member in list(guild.members):
        if member.bot:
            continue
        try:
            embed = discord.Embed(
                title=f'📢 • {guild.name} Duyurusu',
                description=mesaj,
                color=0x3498DB,
                timestamp=simdi(),
            )
            embed.set_footer(text=f'Gönderen Yetkili: {gonderen.name}')
            await member.send(embed=embed)
            basarili += 1
        except Exception:
            basarisiz += 1
        await asyncio.sleep(1.5)

    log_embed = discord.Embed(
        title='📬 • Toplu DM Gönderimi Tamamlandı!', color=0x2ECC71, timestamp=simdi()
    )
    log_embed.add_field(name='Gönderen:', value=gonderen.mention)
    log_embed.add_field(name='Başarılı:', value=f'`{basarili}`')
    log_embed.add_field(name='Başarısız (DM Kapalı):', value=f'`{basarisiz}`')
    log_embed.add_field(name='Duyuru İçeriği:', value=f'```{mesaj[:500]}```', inline=False)
    await erensi_log_gonder(guild, log_embed)


# ---------------------------------------------------------
# 15. YEDEKLEME VE GERI YUKLEME
# ---------------------------------------------------------
def serialize_overwrites(overwrites):
    sonuc = []
    for target, ow in overwrites.items():
        if not isinstance(target, discord.Role):
            continue  # sadece rol izinleri yedeklenir
        allow, deny = ow.pair()
        sonuc.append({'name': target.name, 'is_role': True, 'allow': allow.value, 'deny': deny.value})
    return sonuc


def overwrites_olustur(guild: discord.Guild, liste):
    ow = {}
    for o in liste:
        rol = discord.utils.get(guild.roles, name=o['name'])
        if rol:
            ow[rol] = discord.PermissionOverwrite.from_pair(
                discord.Permissions(o['allow']), discord.Permissions(o['deny'])
            )
    return ow


@bot.command()
@commands.has_permissions(administrator=True)
async def yedek_al(ctx):
    guild = ctx.guild
    status_msg = await ctx.send('⏳ Sunucu yapısı, roller ve kanal izinleri yedekleniyor...')

    backup = {'roles': [], 'categories': [], 'text_channels': [], 'voice_channels': []}

    for role in reversed(guild.roles):
        if role.is_default() or not role.managed:
            backup['roles'].append({
                'name': role.name,
                'permissions': role.permissions.value,
                'color': role.color.value,
                'hoist': role.hoist,
                'mentionable': role.mentionable,
                'is_default': role.is_default(),
            })

    for cat in guild.categories:
        backup['categories'].append({
            'name': cat.name,
            'position': cat.position,
            'overwrites': serialize_overwrites(cat.overwrites),
        })

    for tc in guild.text_channels:
        backup['text_channels'].append({
            'name': tc.name,
            'topic': tc.topic,
            'nsfw': tc.nsfw,
            'position': tc.position,
            'category': tc.category.name if tc.category else None,
            'overwrites': serialize_overwrites(tc.overwrites),
        })

    for vc in guild.voice_channels:
        backup['voice_channels'].append({
            'name': vc.name,
            'user_limit': vc.user_limit,
            'bitrate': vc.bitrate,
            'position': vc.position,
            'category': vc.category.name if vc.category else None,
            'overwrites': serialize_overwrites(vc.overwrites),
        })

    file_path = f'backup_{guild.id}.json'
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(backup, f, ensure_ascii=False, indent=4)

    try:
        await ctx.author.send(
            '🛡️ **Sunucu Yedeğiniz Alındı!**\nDosyayı güvenli bir yerde saklayabilirsiniz:',
            file=discord.File(file_path),
        )
        await status_msg.edit(content='✅ Yedek başarıyla alındı ve özel mesajınıza (DM) gönderildi!')
    except discord.Forbidden:
        await status_msg.edit(content='✅ Yedek oluşturuldu fakat DM kutunuz kapalı olduğu için iletilemedi.')


@bot.command()
@commands.has_permissions(administrator=True)
async def yedek_yukle(ctx):
    guild = ctx.guild
    file_path = f'backup_{guild.id}.json'

    if not os.path.exists(file_path):
        return await ctx.send(f'❌ Bu sunucuya ait yedek dosyası (`{file_path}`) bulunamadı!')

    confirm_msg = await ctx.send(
        '⚠️ **DİKKAT:** Yedekteki **eksik** roller, kategoriler ve kanallar yeniden'
        ' oluşturulacak (mevcut olanlara dokunulmaz). Devam etmek için 30 saniye'
        ' içinde `onayla` yazın.'
    )

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() == 'onayla'

    try:
        await bot.wait_for('message', timeout=30.0, check=check)
    except asyncio.TimeoutError:
        return await confirm_msg.edit(content='❌ Süre dolduğu için yedek yükleme iptal edildi.')

    await ctx.send('🚀 Geri yükleme başlatıldı...')

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        for r in data.get('roles', []):
            if r.get('is_default') or discord.utils.get(guild.roles, name=r['name']):
                continue
            try:
                await guild.create_role(
                    name=r['name'],
                    permissions=discord.Permissions(r['permissions']),
                    color=discord.Color(r['color']),
                    hoist=r['hoist'],
                    mentionable=r['mentionable'],
                )
            except Exception:
                pass

        for c in sorted(data.get('categories', []), key=lambda x: x['position']):
            if discord.utils.get(guild.categories, name=c['name']):
                continue
            try:
                await guild.create_category(c['name'], overwrites=overwrites_olustur(guild, c['overwrites']))
            except Exception:
                pass

        for t in sorted(data.get('text_channels', []), key=lambda x: x['position']):
            cat = discord.utils.get(guild.categories, name=t['category']) if t['category'] else None
            if discord.utils.get(guild.text_channels, name=t['name'], category=cat):
                continue
            try:
                await guild.create_text_channel(
                    t['name'], category=cat, topic=t['topic'], nsfw=t['nsfw'],
                    overwrites=overwrites_olustur(guild, t['overwrites']),
                )
            except Exception:
                pass

        for v in sorted(data.get('voice_channels', []), key=lambda x: x['position']):
            cat = discord.utils.get(guild.categories, name=v['category']) if v['category'] else None
            if discord.utils.get(guild.voice_channels, name=v['name'], category=cat):
                continue
            try:
                await guild.create_voice_channel(
                    v['name'], category=cat, user_limit=v['user_limit'],
                    bitrate=min(v['bitrate'], guild.bitrate_limit),
                    overwrites=overwrites_olustur(guild, v['overwrites']),
                )
            except Exception:
                pass

        await ctx.send('✅ Yedek başarıyla yüklendi ve uygulandı!')
    except Exception as e:
        await ctx.send(f'❌ Yedek yüklenirken bir hata oluştu: `{e}`')


@bot.command()
async def senkronla(ctx):
    """Sadece bot sahibi: slash komutlarını bu sunucuya anında yükler."""
    if ctx.author.id != BOT_SAHIBI_ID:
        return
    bot.tree.copy_global_to(guild=ctx.guild)
    s = await bot.tree.sync(guild=ctx.guild)
    await ctx.send(f'✅ {len(s)} slash komutu bu sunucuya yüklendi.')


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingPermissions):
        return await ctx.send('❌ Bu komut için yönetici olmalısın.')
    print(f'Komut hatası: {error}')


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    print(f'Slash komut hatası ({interaction.command.name if interaction.command else "?"}): {error}')
    mesaj = '❌ Komut çalışırken bir hata oluştu.'
    if isinstance(error, app_commands.MissingPermissions):
        mesaj = '❌ Bu komut için yetkin yok.'
    elif isinstance(error, app_commands.BotMissingPermissions):
        mesaj = '❌ Benim bu işlem için yetkim yok.'
    try:
        if interaction.response.is_done():
            await interaction.followup.send(mesaj, ephemeral=True)
        else:
            await interaction.response.send_message(mesaj, ephemeral=True)
    except Exception:
        pass


# ---------------------------------------------------------
# 16. KOMUT SENKRONIZASYONU (KOMUTLAR GORUNMEME SORUNUNUN COZUMU)
# ---------------------------------------------------------
# Global sync'in Discord'da gorunmesi uzun surebiliyor / onbellekte takiliyor.
# Bu yuzden komutlari HER SUNUCUYA ayri ayri yukluyoruz (anında görünür),
# eski global kopyalari da siliyoruz ki komutlar cift gorunmesin.
async def sunucuya_komut_yukle(guild: discord.Guild):
    try:
        bot.tree.copy_global_to(guild=guild)
        s = await bot.tree.sync(guild=guild)
        print(f'✅ {guild.name}: {len(s)} slash komutu yüklendi.')
    except Exception as e:
        print(f'❌ {guild.name} komut yükleme hatası: {e}')


senkron_yapildi = False


@bot.event
async def on_ready():
    global senkron_yapildi
    if not senkron_yapildi:
        senkron_yapildi = True
        for g in bot.guilds:
            await sunucuya_komut_yukle(g)
        try:
            await bot.http.bulk_upsert_global_commands(bot.application_id, [])
        except Exception as e:
            print(f'Global komut temizleme hatası: {e}')

    for g in bot.guilds:
        await davetleri_guncelle(g)

    print(f'Bot aktif: {bot.user.name} ({bot.user.id})')


@bot.event
async def on_guild_join(guild: discord.Guild):
    await sunucuya_komut_yukle(guild)
    await davetleri_guncelle(guild)


# ---------------------------------------------------------
# 17. CALISTIR
# ---------------------------------------------------------
if __name__ == '__main__':
    keep_alive()
    TOKEN = os.environ.get('DISCORD_TOKEN')
    if TOKEN:
        bot.run(TOKEN)
    else:
        print('❌ HATA: DISCORD_TOKEN çevresel değişkeni bulunamadı!')
