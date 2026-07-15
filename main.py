import discord
from discord import app_commands
from discord.ext import commands, tasks
import sqlite3
import pandas as pd
import numpy as np
from PIL import Image
import io
import random
import ast
import os
import sys
import datetime
import traceback
import asyncio
import logging
import colorsys
import shutil
from dotenv import load_dotenv
import db_sync

# ==============================================================================
# 全世界戦争Bot - 究極完全統合版 (絵文字削減 / Tips&宣伝機能 / 防衛費複数選択対応)
# ==============================================================================
load_dotenv()
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DB_FILE = os.getenv("DB_FILE", "war_game_worlds.db")
BACKUP_DIR = os.getenv("BACKUP_DIR", "db_backups")
PROMO_LINK = "https://discord.gg/dsGhNNJfzc"
TIPS_FILE = "tips.txt"

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)

SAY_LOG_DIR = os.path.expanduser("./world-war-bot/say_log")
os.makedirs(SAY_LOG_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

# 宣伝リンクとTipsを取得する関数 (コマンド実行時の末尾に付加)
def get_promo_and_tip():
    res = ""
    # 約10%の確率で宣伝リンク
    if random.random() < 0.1:
        res += f"\n\n-# Official Support Server: {PROMO_LINK}"
    
    # 約30%の確率でTipsをファイルから読み込み
    if random.random() < 0.3:
        try:
            if os.path.exists(TIPS_FILE):
                with open(TIPS_FILE, "r", encoding="utf-8") as f:
                    tips = [line.strip() for line in f if line.strip() and not line.startswith("#")]
                if tips:
                    if not res: res += "\n\n"
                    else: res += "\n"
                    res += f"-# Tip: {random.choice(tips)}"
        except Exception:
            pass
    return res

# ==============================================================================
# 初期設定＆辞書・隣接データ読み込み
# ==============================================================================
def load_country_map():
    mapping = {"CHINA": "CHN", "PRC": "CHN"}
    if os.path.exists("country_map.txt"):
        with open("country_map.txt", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"): continue
                parts = line.split(",")
                if len(parts) == 2: mapping[parts[0].strip().upper()] = parts[1].strip().upper()
    return mapping

def get_paypay_link():
    if os.path.exists("paypay.txt"):
        with open("paypay.txt", "r", encoding="utf-8") as f:
            link = f.read().strip()
            if link: return link
    return "Support link is not currently configured."

COUNTRY_MAP = load_country_map()
TERRITORY_YIELD = {"USA": 300, "RUS": 300, "CHN": 300, "JPN": 200, "DEU": 200, "FRA": 200, "GBR": 200, "IND": 200, "BRA": 150, "AUS": 150, "CAN": 150}

# 経済バランス
BASE_INCOME = 1000
DEFAULT_DEFENSE = 200 

RAW_ADJACENCY = {
    "JPN": ["KOR", "PRK", "RUS", "TWN", "CHN", "PHL"],
    "USA": ["CAN", "MEX", "CUB", "RUS", "BHS"],
    "RUS": ["CHN", "PRK", "MNG", "KAZ", "JPN", "FIN", "BLR", "UKR", "EST", "LVA", "LTU", "POL", "NOR", "GEO", "AZE", "USA"],
    "CHN": ["RUS", "MNG", "PRK", "KAZ", "KGZ", "TJK", "AFG", "PAK", "IND", "NPL", "BTN", "MMR", "LAO", "VNM", "TWN", "JPN", "MAC", "HKG"],
    "DEU": ["DNK", "POL", "CZE", "AUT", "CHE", "FRA", "LUX", "BEL", "NLD"],
    "FRA": ["BEL", "LUX", "DEU", "CHE", "ITA", "MCO", "ESP", "AND", "GBR"],
    "GBR": ["IRL", "FRA"],
    "IND": ["PAK", "CHN", "NPL", "BTN", "BGD", "MMR", "LKA", "MDV"],
    "BRA": ["URY", "ARG", "PRY", "BOL", "PER", "COL", "VEN", "GUY", "SUR", "GUF"],
    "AUS": ["IDN", "PNG", "NZL", "TLS"],
    "CAN": ["USA"],
    "KOR": ["PRK", "JPN", "CHN"],
    "PRK": ["KOR", "CHN", "RUS", "JPN"],
    "ITA": ["FRA", "CHE", "AUT", "SVN", "SMR", "VAT"],
    "ESP": ["FRA", "PRT", "AND", "MAR"],
    "TUR": ["GRC", "BGR", "GEO", "ARM", "AZE", "IRN", "IRQ", "SYR"],
    "SAU": ["JOR", "IRQ", "KWT", "BHR", "QAT", "ARE", "OMN", "YEM"],
    "ZAF": ["NAM", "BWA", "ZWE", "MOZ", "SWZ", "LSO"]
}
ADJACENCY_GRAPH = {}
for k, v_list in RAW_ADJACENCY.items():
    if k not in ADJACENCY_GRAPH: ADJACENCY_GRAPH[k] = set()
    for v in v_list:
        ADJACENCY_GRAPH[k].add(v)
        if v not in ADJACENCY_GRAPH: ADJACENCY_GRAPH[v] = set()
        ADJACENCY_GRAPH[v].add(k)

intents = discord.Intents.all()
intents.presences = True
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

try:
    df_countries = pd.read_csv("countries_data.csv")
    country_to_mask = {row['iso_alpha'].upper().strip(): ast.literal_eval(row['rgb_str']) for _, row in df_countries.iterrows()}
    VALID_CODES = set(country_to_mask.keys())
except Exception as e:
    logger.error(f"Failed to load countries_data.csv: {e}")
    VALID_CODES = set()

db_sync.download_db()
# ==============================================================================
# データベース接続・初期化
# ==============================================================================
def get_db_connection(): return sqlite3.connect(DB_FILE, timeout=20.0)

def init_db():
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("PRAGMA journal_mode=WAL;")
            c.execute('''CREATE TABLE IF NOT EXISTS server_channels (guild_id TEXT PRIMARY KEY, world1_ch TEXT, world2_ch TEXT, world3_ch TEXT, notify_ch TEXT, notify_enabled INTEGER DEFAULT 1)''')
            c.execute('''CREATE TABLE IF NOT EXISTS user_settings (guild_id TEXT, user_id TEXT, active_world INTEGER DEFAULT 1, PRIMARY KEY(guild_id, user_id))''')
            c.execute('''CREATE TABLE IF NOT EXISTS players (guild_id TEXT, world_id INTEGER, user_id TEXT, user_name TEXT, short_name TEXT DEFAULT 'Unnamed', r INTEGER, g INTEGER, b INTEGER, gold INTEGER DEFAULT 1000, main_country TEXT DEFAULT 'Not Set', PRIMARY KEY(guild_id, world_id, user_id))''')
            c.execute('''CREATE TABLE IF NOT EXISTS territories (guild_id TEXT, world_id INTEGER, iso_alpha TEXT, owner_id TEXT, defense INTEGER DEFAULT 100, PRIMARY KEY(guild_id, world_id, iso_alpha))''')
            c.execute('''CREATE TABLE IF NOT EXISTS alliances (guild_id TEXT, world_id INTEGER, user_a TEXT, user_b TEXT, PRIMARY KEY(guild_id, world_id, user_a, user_b))''')
            c.execute('''CREATE TABLE IF NOT EXISTS wars (guild_id TEXT, world_id INTEGER, attacker_id TEXT, defender_id TEXT, PRIMARY KEY(guild_id, world_id, attacker_id, defender_id))''')
            c.execute('''CREATE TABLE IF NOT EXISTS un_members (guild_id TEXT, world_id INTEGER, user_id TEXT, PRIMARY KEY(guild_id, world_id, user_id))''')
            c.execute('''CREATE TABLE IF NOT EXISTS un_invites (guild_id TEXT, world_id INTEGER, user_id TEXT, PRIMARY KEY(guild_id, world_id, user_id))''')
            c.execute('''CREATE TABLE IF NOT EXISTS camps (guild_id TEXT, world_id INTEGER, camp_name TEXT, founder_id TEXT, PRIMARY KEY(guild_id, world_id, camp_name))''')
            c.execute('''CREATE TABLE IF NOT EXISTS camp_members (guild_id TEXT, world_id INTEGER, user_id TEXT, camp_name TEXT, PRIMARY KEY(guild_id, world_id, user_id))''')
            c.execute('''CREATE TABLE IF NOT EXISTS camp_invites (guild_id TEXT, world_id INTEGER, user_id TEXT, camp_name TEXT, PRIMARY KEY(guild_id, world_id, user_id, camp_name))''')
            c.execute('''CREATE TABLE IF NOT EXISTS server_ops (guild_id TEXT, user_id TEXT, PRIMARY KEY(guild_id, user_id))''')

            def add_column(table, column, data_type):
                c.execute(f"PRAGMA table_info({table})")
                if column not in [row[1] for row in c.fetchall()]: c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {data_type}")
                    
            add_column("players", "oil", "INTEGER DEFAULT 8000")
            add_column("players", "debt", "INTEGER DEFAULT 0")
            add_column("server_channels", "oil_enabled_w1", "INTEGER DEFAULT 1")
            add_column("server_channels", "oil_enabled_w2", "INTEGER DEFAULT 1")
            add_column("server_channels", "oil_enabled_w3", "INTEGER DEFAULT 1")
            add_column("server_channels", "adjacency_penalty", "INTEGER DEFAULT 0")
            add_column("server_channels", "reset_interval", "INTEGER DEFAULT 7")
            add_column("server_channels", "last_reset_date", f"TEXT DEFAULT '{datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')}'")
            add_column("user_settings", "confirm_attack", "INTEGER DEFAULT 1")
            conn.commit()
    except Exception as e: logger.error(f"DB initialization error: {e}")

init_db()

# ==============================================================================
# 便利関数群
# ==============================================================================
async def safe_defer(interaction: discord.Interaction, ephemeral: bool = False):
    if not interaction.response.is_done(): await interaction.response.defer(ephemeral=ephemeral)

async def send_dm_fallback(member: discord.Member, channel: discord.TextChannel, content: str, view: discord.ui.View = None, embed: discord.Embed = None):
    try:
        await member.send(content, view=view, embed=embed)
        return True
    except Exception:
        try:
            msg = f"{member.mention} [Notice] Since DM delivery is disabled, you are being notified here.\n{content}"
            await channel.send(msg, view=view, embed=embed)
        except Exception: pass
        return False

def is_slash_op_or_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator: return True
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT 1 FROM server_ops WHERE guild_id = ? AND user_id = ?", (str(interaction.guild_id), str(interaction.user.id)))
            if c.fetchone(): return True
        return False
    return app_commands.check(predicate)

async def ensure_world_context(interaction: discord.Interaction) -> int:
    guild_id, channel_id, user_id = str(interaction.guild_id), str(interaction.channel.id), str(interaction.user.id)
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT world1_ch, world2_ch, world3_ch FROM server_channels WHERE guild_id = ?", (guild_id,))
        row = c.fetchone()
        if row and any(row): 
            if channel_id == row[0]: return 1
            if channel_id == row[1]: return 2
            if channel_id == row[2]: return 3
            msg = "[Error] This command cannot be executed in this channel.\nPlease use the dedicated channel for each World."
            if interaction.response.is_done(): await interaction.followup.send(msg, ephemeral=True)
            else: await interaction.response.send_message(msg, ephemeral=True)
            return 0
        c.execute("SELECT active_world FROM user_settings WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        urow = c.fetchone()
        return urow[0] if urow else 1

def is_oil_enabled(guild_id: str, world_id: int) -> bool:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT oil_enabled_w1, oil_enabled_w2, oil_enabled_w3 FROM server_channels WHERE guild_id = ?", (guild_id,))
        row = c.fetchone()
        if not row: return True
        return bool(row[world_id - 1]) if 1 <= world_id <= 3 else True

def resolve_country_code(target: str) -> str: return COUNTRY_MAP.get(target.replace(" ", "").strip().upper(), target.replace(" ", "").strip().upper())

def is_allied(guild_id: str, world_id: int, user_a: str, user_b: str) -> bool:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT 1 FROM alliances WHERE guild_id=? AND world_id=? AND ((user_a=? AND user_b=?) OR (user_a=? AND user_b=?))", (guild_id, world_id, user_a, user_b, user_b, user_a))
        return c.fetchone() is not None

def is_at_war(guild_id: str, world_id: int, attacker: str, defender: str) -> bool:
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT 1 FROM wars WHERE guild_id=? AND world_id=? AND attacker_id=? AND defender_id=?", (guild_id, world_id, attacker, defender))
        return c.fetchone() is not None

def check_and_create_user(cursor, guild_id, world_id, user_id, user_name):
    cursor.execute("SELECT gold FROM players WHERE guild_id = ? AND world_id = ? AND user_id = ?", (guild_id, world_id, user_id))
    if not cursor.fetchone():
        h, s, v = random.random(), random.uniform(0.6, 1.0), random.uniform(0.8, 1.0)
        r, g, b = [int(x * 255) for x in colorsys.hsv_to_rgb(h, s, v)]
        cursor.execute("INSERT INTO players (guild_id, world_id, user_id, user_name, short_name, r, g, b, oil, debt) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 8000, 0)", (guild_id, world_id, user_id, user_name, user_name[:3], r, g, b))

def _generate_current_map_sync(guild_id: str, world_id: int):
    try:
        base_np, mask_np = np.array(Image.open("base_map.png").convert("RGBA")), np.array(Image.open("mask_map.png").convert("RGB"))
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT t.iso_alpha, p.r, p.g, p.b, p.user_name FROM territories t JOIN players p ON t.owner_id = p.user_id AND t.world_id = p.world_id AND t.guild_id = p.guild_id WHERE t.guild_id = ? AND t.world_id = ?", (guild_id, world_id))
            occupied_lands = c.fetchall()
        for iso_alpha, r, g, b, _ in occupied_lands:
            if iso_alpha in country_to_mask:
                mask_color = country_to_mask[iso_alpha]
                match = ((mask_np[:, :, 0] == mask_color[0]) & (mask_np[:, :, 1] == mask_color[1]) & (mask_np[:, :, 2] == mask_color[2]))
                base_np[match] = [r, g, b, 160]
        img_bin = io.BytesIO()
        Image.fromarray(base_np).save(img_bin, format="PNG")
        img_bin.seek(0)
        return discord.File(fp=img_bin, filename="war_map.png"), occupied_lands
    except Exception as e:
        logger.error(f"Map generation error: \n{traceback.format_exc()}")
        return None, []


# ==============================================================================
# 管理者用 スラッシュコマンドグループ (/op)
# ==============================================================================
class OpGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="op", description="Setting commands exclusive to server admins and OP role holders")

    @app_commands.command(name="setup", description="Automatically creates a war category and dedicated channels for each World")
    @is_slash_op_or_admin()
    async def cmd_setup(self, interaction: discord.Interaction):
        await safe_defer(interaction)
        category = await interaction.guild.create_category("war-bot")
        ch1 = await category.create_text_channel("war-bot-1")
        ch2 = await category.create_text_channel("war-bot-2")
        ch3 = await category.create_text_channel("war-bot-3")
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO server_channels (guild_id, world1_ch, world2_ch, world3_ch, notify_ch, notify_enabled) VALUES (?, ?, ?, ?, ?, 1)", (str(interaction.guild_id), str(ch1.id), str(ch2.id), str(ch3.id), str(ch1.id)))
            conn.commit()
        await interaction.followup.send("[Completed] Created and linked the category and dedicated channels for each World.")

    @app_commands.command(name="adj", description="Toggle adjacent expedition penalty (increased cost when not adjacent) ON/OFF")
    @is_slash_op_or_admin()
    async def cmd_adj(self, interaction: discord.Interaction):
        await safe_defer(interaction)
        guild_id = str(interaction.guild_id)
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO server_channels (guild_id) VALUES (?)", (guild_id,))
            c.execute("SELECT adjacency_penalty FROM server_channels WHERE guild_id=?", (guild_id,))
            row = c.fetchone()
            new_val = 0 if (row and row[0] == 1) else 1
            c.execute("UPDATE server_channels SET adjacency_penalty=? WHERE guild_id=?", (new_val, guild_id))
            conn.commit()
        await interaction.followup.send(f"[Setting] Changed adjacent expedition penalty to **{'ON (Enabled)' if new_val==1 else 'OFF (Disabled)'}**.")

    @app_commands.command(name="reset_interval", description="Change automatic reset interval (0 to disable)")
    @is_slash_op_or_admin()
    async def cmd_reset_interval(self, interaction: discord.Interaction, days: int):
        await safe_defer(interaction)
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO server_channels (guild_id) VALUES (?)", (str(interaction.guild_id),))
            c.execute("UPDATE server_channels SET reset_interval=? WHERE guild_id=?", (days, str(interaction.guild_id)))
            conn.commit()
        msg = f"[Setting] Configured automatic server reset to **every {days} days**." if days > 0 else "[Setting] Configured automatic server reset to **OFF (manual only)**."
        await interaction.followup.send(msg)

    @app_commands.command(name="reset", description="Instantly resets all data for the currently active world")
    @is_slash_op_or_admin()
    async def cmd_reset(self, interaction: discord.Interaction):
        await safe_defer(interaction)
        world_id = await ensure_world_context(interaction)
        if world_id == 0: return
        with get_db_connection() as conn:
            c = conn.cursor()
            for table in ['players', 'territories', 'alliances', 'wars', 'un_members', 'un_invites', 'camps', 'camp_members', 'camp_invites']:
                c.execute(f"DELETE FROM {table} WHERE guild_id=? AND world_id=?", (str(interaction.guild_id), world_id))
            conn.commit()
        await interaction.followup.send(f"[Completed] Manually reset all data for [World #{world_id}]. A new history begins.")

    @app_commands.command(name="op_setting", description="Grant or revoke OP permissions for a specified user")
    @app_commands.choices(mode=[app_commands.Choice(name="Grant Permission (ON)", value=1), app_commands.Choice(name="Revoke Permission (OFF)", value=0)])
    @is_slash_op_or_admin()
    async def cmd_op_setting(self, interaction: discord.Interaction, target: discord.Member, mode: app_commands.Choice[int]):
        await safe_defer(interaction)
        add_op = (mode.value == 1)
        with get_db_connection() as conn:
            c = conn.cursor()
            if add_op:
                c.execute("INSERT OR IGNORE INTO server_ops (guild_id, user_id) VALUES (?, ?)", (str(interaction.guild_id), str(target.id)))
                msg = f"[Success] Granted admin/OP permissions to {target.mention}."
            else:
                c.execute("DELETE FROM server_ops WHERE guild_id=? AND user_id=?", (str(interaction.guild_id), str(target.id)))
                msg = f"[Success] Revoked admin/OP permissions from {target.mention}."
            conn.commit()
        await interaction.followup.send(msg)

    @app_commands.command(name="oil_setting", description="Toggle the oil consumption system")
    @app_commands.choices(world=[app_commands.Choice(name="All Servers (0)", value=0), app_commands.Choice(name="World #1", value=1), app_commands.Choice(name="World #2", value=2), app_commands.Choice(name="World #3", value=3)])
    @app_commands.choices(mode=[app_commands.Choice(name="Enabled (ON)", value=1), app_commands.Choice(name="Disabled (OFF)", value=0)])
    @is_slash_op_or_admin()
    async def cmd_oil_setting(self, interaction: discord.Interaction, world: app_commands.Choice[int], mode: app_commands.Choice[int]):
        await safe_defer(interaction)
        val = mode.value; w_id = world.value
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO server_channels (guild_id) VALUES (?)", (str(interaction.guild_id),))
            if w_id == 0:
                c.execute("UPDATE server_channels SET oil_enabled_w1=?, oil_enabled_w2=?, oil_enabled_w3=? WHERE guild_id=?", (val, val, val, str(interaction.guild_id)))
                msg = f"[Setting] Configured global oil system to **{'ON' if val==1 else 'OFF'}**."
            else:
                c.execute(f"UPDATE server_channels SET oil_enabled_w{w_id}=? WHERE guild_id=?", (val, str(interaction.guild_id)))
                msg = f"[Setting] Configured World #{w_id} oil system to **{'ON' if val==1 else 'OFF'}**."
            conn.commit()
        await interaction.followup.send(msg)

    @app_commands.command(name="channel_setting", description="Manually link existing channels to each World")
    @is_slash_op_or_admin()
    async def cmd_channel_setting(self, interaction: discord.Interaction, world1: discord.TextChannel, world2: discord.TextChannel, world3: discord.TextChannel):
        await safe_defer(interaction)
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO server_channels (guild_id, world1_ch, world2_ch, world3_ch, notify_ch, notify_enabled) VALUES (?, ?, ?, ?, COALESCE((SELECT notify_ch FROM server_channels WHERE guild_id=?), ?), 1)", (str(interaction.guild_id), str(world1.id), str(world2.id), str(world3.id), str(interaction.guild_id), str(world1.id)))
            conn.commit()
        await interaction.followup.send("[Completed] Manually configured dedicated channels for each World.")

    @app_commands.command(name="reboot_setting", description="Set the notification channel and toggle for payouts or wipes")
    @app_commands.choices(mode=[app_commands.Choice(name="Enabled (ON)", value=1), app_commands.Choice(name="Disabled (OFF)", value=0)])
    @is_slash_op_or_admin()
    async def cmd_reboot_setting(self, interaction: discord.Interaction, notify_channel: discord.TextChannel, mode: app_commands.Choice[int]):
        await safe_defer(interaction)
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO server_channels (guild_id) VALUES (?)", (str(interaction.guild_id),))
            c.execute("UPDATE server_channels SET notify_ch=?, notify_enabled=? WHERE guild_id=?", (str(notify_channel.id), mode.value, str(interaction.guild_id)))
            conn.commit()
        await interaction.followup.send(f"[Setting] Changed notification settings to **{'ON' if mode.value==1 else 'OFF'}** and set the channel to {notify_channel.mention}.")

bot.tree.add_command(OpGroup())


# ==============================================================================
# ユーザー設定とヘルプ・GUI
# ==============================================================================
@bot.tree.command(name="user_setting", description="Configure user-specific convenience settings (e.g., toggling confirmation dialogs)")
@app_commands.choices(setting=[app_commands.Choice(name="Attack Confirmation Dialog", value="confirm_attack")])
@app_commands.choices(mode=[app_commands.Choice(name="Turn ON", value=1), app_commands.Choice(name="Turn OFF", value=0)])
async def cmd_user_setting(interaction: discord.Interaction, setting: app_commands.Choice[str], mode: app_commands.Choice[int]):
    await safe_defer(interaction, ephemeral=True)
    guild_id, user_id = str(interaction.guild_id), str(interaction.user.id)
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO user_settings (guild_id, user_id) VALUES (?, ?)", (guild_id, user_id))
        if setting.value == "confirm_attack":
            c.execute("UPDATE user_settings SET confirm_attack=? WHERE guild_id=? AND user_id=?", (mode.value, guild_id, user_id))
        conn.commit()
    msg = f"[Setting] Changed **{setting.name}** to **{'ON' if mode.value==1 else 'OFF'}**."
    msg += get_promo_and_tip()
    await interaction.followup.send(msg, ephemeral=True)

class CommandGUIView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.select(placeholder="Select an easy action from the menu...", options=[
        discord.SelectOption(label="Check Status", value="status", description="Check your money, oil, and total military force"),
        discord.SelectOption(label="Plan Invasion (Attack)", value="attack", description="Launch an attack on a specified country"),
        discord.SelectOption(label="Diplomatic Procedures (GUI)", value="gui", description="Declare war or request/cancel alliances"),
        discord.SelectOption(label="Invasion Targets", value="targets", description="List of vacant lands and enemy countries you can attack"),
        discord.SelectOption(label="Manage Territory", value="country_management", description="Menu to abandon ownership of your territories"),
        discord.SelectOption(label="Territorial Defense Status", value="country_status", description="Check the defense power list of your territories")
    ])
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        val = select.values[0]
        if val == "attack":
            await interaction.response.send_modal(AttackTargetModal())
            return
        await interaction.response.edit_message(view=self)
        if val == "status": await cmd_status.callback(interaction)
        elif val == "gui": await cmd_diplomacy.callback(interaction)
        elif val == "targets": await cmd_targets.callback(interaction)
        elif val == "country_management": await cmd_country_management.callback(interaction)
        elif val == "country_status": await cmd_country_status.callback(interaction)

class HelpView(discord.ui.View):
    def __init__(self): 
        super().__init__(timeout=None)
        
    def generate_embed(self, page_id: str):
        embed = discord.Embed(title="Global War Bot Guidebook", color=0x3498db)
        if page_id == "basic":
            embed.description = "**Basic Rules & Resources**\nThis bot is a simulation game where players compete for countries to seize global hegemony."
            embed.add_field(name="Regular Payouts", value="Every day (7:00 / 19:00 JST), you receive a basic salary (1000G), tax revenue, and oil distribution. Maintenance costs (number of territories × 25L) are also deducted.", inline=False)
            embed.add_field(name="Work & Resources", value="Use `/work` to earn funds and oil. Try to do this in the early game.\nIf you run out of funds, you can borrow 5000G from the national treasury using `/war_bonds` (deducted from future payouts).", inline=False)
        elif page_id == "war":
            embed.description = "**War & Domestic Affairs**"
            embed.add_field(name="Invasion Operations", value="Attack other countries or vacant lands with `/attack`. **The funds you enter are consumed entirely**.\nSurprise attacks without a declaration of war, or attacking distant countries, will reduce your effective attack power due to penalties.", inline=False)
            embed.add_field(name="Defense & Empire Bonus", value="Raise your territory's defense with `/defend`.\n**Empires owning 3 or more territories** get a discount on defense costs (0.9x multiplier), allowing more efficient defense.", inline=False)
        elif page_id == "diplomacy":
            embed.description = "**Diplomacy & Trade**"
            embed.add_field(name="Declarations of War & Alliances", value="Done via `/gui`. Even if a declaration of war is rejected, you can still launch a surprise attack.\nAlliances grant mutual non-aggression, and you'll receive DM notifications if your ally is attacked.", inline=False)
            embed.add_field(name="Trade", value="Purchase oil from other players with `/import_oil`, or directly exchange funds and oil with the system using `/convert_resource`.", inline=False)
        elif page_id == "admin":
            embed.description = "**For Administrators**"
            embed.add_field(name="OP Commands", value="First, run `/op setup` to batch-create dedicated channels.\nAll other configurations can be managed using commands starting with `/op`.", inline=False)
        return embed

    @discord.ui.button(label="Basic Rules", style=discord.ButtonStyle.primary, custom_id="help_basic")
    async def btn_basic(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.generate_embed("basic"), view=self)

    @discord.ui.button(label="War & Domestic", style=discord.ButtonStyle.secondary, custom_id="help_war")
    async def btn_war(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.generate_embed("war"), view=self)

    @discord.ui.button(label="Diplomacy & Trade", style=discord.ButtonStyle.secondary, custom_id="help_dip")
    async def btn_dip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.generate_embed("diplomacy"), view=self)

    @discord.ui.button(label="For Admins", style=discord.ButtonStyle.danger, custom_id="help_admin")
    async def btn_admin(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.generate_embed("admin"), view=self)

@bot.tree.command(name="help", description="Explains the game rules, initial setup, and details on how to play")
async def cmd_help(interaction: discord.Interaction):
    await safe_defer(interaction)
    view = HelpView()
    await interaction.followup.send(content=get_promo_and_tip(), embed=view.generate_embed("basic"), view=view)


# ==============================================================================
# 一般コマンド＆外交システム (貿易・攻撃確認対応)
# ==============================================================================
class OilImportView(discord.ui.View):
    def __init__(self, guild_id, world_id, buyer_id, seller_id, amount, price):
        super().__init__(timeout=86400)
        self.guild_id = guild_id
        self.world_id = world_id
        self.buyer_id = buyer_id
        self.seller_id = seller_id
        self.amount = amount
        self.price = price

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def btn_accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.seller_id:
            return await interaction.response.send_message("[Error] You do not have permission.", ephemeral=True)
            
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT oil FROM players WHERE guild_id=? AND world_id=? AND user_id=?", (self.guild_id, self.world_id, self.seller_id))
            seller_row = c.fetchone()
            if not seller_row or seller_row[0] < self.amount:
                return await interaction.response.send_message("[Error] Insufficient oil to complete the trade.", ephemeral=True)
            
            c.execute("SELECT gold FROM players WHERE guild_id=? AND world_id=? AND user_id=?", (self.guild_id, self.world_id, self.buyer_id))
            buyer_row = c.fetchone()
            if not buyer_row or buyer_row[0] < self.price:
                return await interaction.response.send_message("[Error] The buyer has insufficient funds, trade cancelled.", ephemeral=True)

            c.execute("UPDATE players SET oil=oil-?, gold=gold+? WHERE guild_id=? AND world_id=? AND user_id=?", (self.amount, self.price, self.guild_id, self.world_id, self.seller_id))
            c.execute("UPDATE players SET oil=oil+?, gold=gold-? WHERE guild_id=? AND world_id=? AND user_id=?", (self.amount, self.price, self.guild_id, self.world_id, self.buyer_id))
            conn.commit()

        for child in self.children: child.disabled = True
        await interaction.response.edit_message(content=f"[Trade Complete / World #{self.world_id}]\nExported **{self.amount} L** of oil to <@{self.buyer_id}> and received **{self.price} Gold**.", view=self)

        guild = interaction.client.get_guild(int(self.guild_id))
        if guild:
            buyer_member = guild.get_member(int(self.buyer_id))
            if buyer_member:
                try: await buyer_member.send(f"[Trade Complete / World #{self.world_id}]\n<@{self.seller_id}> approved the import request.\nAcquired **{self.amount} L** of oil and paid **{self.price} Gold**.")
                except: pass

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
    async def btn_reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.seller_id:
            return await interaction.response.send_message("[Error] You do not have permission.", ephemeral=True)
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(content=f"[Rejected / World #{self.world_id}] Trade request was rejected.", view=self)

class DiplomacyUserSelect(discord.ui.UserSelect):
    def __init__(self, action: str, world_id: int):
        self.action = action; self.world_id = world_id
        super().__init__(placeholder="Select target player...")

    async def callback(self, interaction: discord.Interaction):
        await safe_defer(interaction) 
        guild_id, user_id, target_id = str(interaction.guild_id), str(interaction.user.id), str(self.values[0].id)
        w_id = self.world_id; target = self.values[0]

        if user_id == target_id: return await interaction.followup.send("[Error] You cannot select yourself.", ephemeral=True)
        if target.bot: return await interaction.followup.send("[Error] You cannot select a Bot.", ephemeral=True)

        target_member = interaction.guild.get_member(int(target_id))

        with get_db_connection() as conn:
            c = conn.cursor()
            is_ally = is_allied(guild_id, w_id, user_id, target_id)

            if self.action == "war":
                if is_ally: return await interaction.followup.send("[Error] You cannot declare war on an ally.", ephemeral=True)
                view = discord.ui.View(timeout=86400)
                btn_accept = discord.ui.Button(label="Accept", style=discord.ButtonStyle.danger)
                btn_reject = discord.ui.Button(label="Reject", style=discord.ButtonStyle.success)

                async def accept_callback(i: discord.Interaction):
                    if str(i.user.id) != target_id: return await i.response.send_message("[Error] You do not have permission.", ephemeral=True)
                    with get_db_connection() as conn2:
                        c2 = conn2.cursor()
                        c2.execute("INSERT OR IGNORE INTO wars (guild_id, world_id, attacker_id, defender_id) VALUES (?, ?, ?, ?)", (guild_id, w_id, user_id, target_id))
                        conn2.commit()
                    for child in view.children: child.disabled = True
                    await i.response.edit_message(content=f"[Declaration of War Accepted / World #{w_id}]\n<@{target_id}> and <@{user_id}> are officially at war!", view=view)

                async def reject_callback(i: discord.Interaction):
                    if str(i.user.id) != target_id: return await i.response.send_message("[Error] You do not have permission.", ephemeral=True)
                    for child in view.children: child.disabled = True
                    await i.response.edit_message(content=f"[Declaration of War Rejected / World #{w_id}]\n<@{target_id}> rejected the declaration of war, and negotiations have broken down.\n<@{user_id}> has no choice but to launch a forced march (surprise attack).", view=view)

                btn_accept.callback = accept_callback
                btn_reject.callback = reject_callback
                view.add_item(btn_accept)
                view.add_item(btn_reject)

                content = f"**[Declaration of War Envoy / World #{w_id}]**\nAn envoy declaring war from <@{user_id}> has arrived!\nPlease choose to accept or reject.\n*Note: Even if rejected, negotiations break down, and they can still launch a forced march (surprise attack)."
                if target_member: await send_dm_fallback(target_member, interaction.channel, content, view)
                else: await interaction.channel.send(f"<@{target_id}> {content}", view=view)
                await interaction.followup.send("[Completed] Sent a declaration of war envoy. (Check DM or channel)", ephemeral=True)

            elif self.action == "alliance_invite":
                if is_ally: return await interaction.followup.send("[Error] You are already allied.", ephemeral=True)
                view = discord.ui.View(timeout=86400)
                btn_accept = discord.ui.Button(label="Approve", style=discord.ButtonStyle.success)
                async def accept_callback(i: discord.Interaction):
                    if str(i.user.id) != target_id: return await i.response.send_message("[Error] You do not have permission.", ephemeral=True)
                    with get_db_connection() as conn2:
                        c2 = conn2.cursor()
                        c2.execute("INSERT OR IGNORE INTO alliances (guild_id, world_id, user_a, user_b) VALUES (?, ?, ?, ?)", (guild_id, w_id, user_id, target_id))
                        c2.execute("DELETE FROM wars WHERE guild_id=? AND world_id=? AND ((attacker_id=? AND defender_id=?) OR (attacker_id=? AND defender_id=?))", (guild_id, w_id, user_id, target_id, target_id, user_id))
                        conn2.commit()
                    for child in view.children: child.disabled = True
                    await i.response.edit_message(content=f"[Alliance Formed / World #{w_id}]\n<@{user_id}> and <@{target_id}> have formed a military alliance!", view=view)
                btn_accept.callback = accept_callback
                view.add_item(btn_accept)
                
                content = f"**[Military Alliance Proposal / World #{w_id}]**\nA military alliance proposal has arrived from <@{user_id}>!\nWould you like to approve and form an alliance?"
                if target_member: await send_dm_fallback(target_member, interaction.channel, content, view)
                else: await interaction.channel.send(f"<@{target_id}> {content}", view=view)
                await interaction.followup.send("[Completed] Sent alliance proposal. (Check DM or channel)", ephemeral=True)

            elif self.action == "alliance_cancel":
                if not is_ally: return await interaction.followup.send("[Error] You are not allied with that player.", ephemeral=True)
                c.execute("DELETE FROM alliances WHERE guild_id=? AND world_id=? AND ((user_a=? AND user_b=?) OR (user_a=? AND user_b=?))", (guild_id, w_id, user_id, target_id, target_id, user_id))
                conn.commit()
                await interaction.channel.send(f"[Alliance Dissolved / World #{w_id}] <@{user_id}> dissolved the alliance with <@{target_id}>.")
                await interaction.followup.send("[Completed] Dissolved the alliance.", ephemeral=True)

class DiplomacyActionSelect(discord.ui.Select):
    def __init__(self, world_id: int):
        self.world_id = world_id
        options = [
            discord.SelectOption(label="Declare War", value="war"),
            discord.SelectOption(label="Propose Alliance", value="alliance_invite"),
            discord.SelectOption(label="Dissolve Alliance", value="alliance_cancel"),
            discord.SelectOption(label="Ally List", value="alliance_list"),
            discord.SelectOption(label="UN Member List", value="un_list"),
            discord.SelectOption(label="Camp List", value="camp_list")
        ]
        super().__init__(placeholder="Select diplomatic action...", options=options)

    async def callback(self, interaction: discord.Interaction):
        val = self.values[0]
        if val == "alliance_list":
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("SELECT user_a, user_b FROM alliances WHERE guild_id=? AND world_id=?", (str(interaction.guild_id), self.world_id))
                rows = c.fetchall()
            if not rows: return await interaction.response.send_message(f"[World #{self.world_id}] There are currently no active alliances.", ephemeral=True)
            embed = discord.Embed(title=f"Active Military Alliances [World #{self.world_id}]", description="\n".join([f"・ <@{a}> ＆ <@{b}>" for a, b in rows]), color=0x3498db)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        elif val == "un_list":
            await cmd_un_list.callback(interaction)
        elif val == "camp_list":
            await cmd_camp_list.callback(interaction)
        else:
            view = discord.ui.View(timeout=60)
            view.add_item(DiplomacyUserSelect(val, self.world_id))
            await interaction.response.send_message("👉 Next, select the target player:", view=view, ephemeral=True)

class CountryManageSelect(discord.ui.Select):
    def __init__(self, territories, world_id, user_id):
        self.world_id = world_id; self.user_id = user_id
        options = [discord.SelectOption(label=t[0], description=f"Defense: {t[1]}") for t in territories[:25]]
        super().__init__(placeholder="Select territory to manage...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_iso = self.values[0]
        view = discord.ui.View(timeout=60)
        btn_abandon = discord.ui.Button(label="Abandon Ownership", style=discord.ButtonStyle.danger)
        async def abandon_callback(i: discord.Interaction):
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("DELETE FROM territories WHERE guild_id=? AND world_id=? AND iso_alpha=? AND owner_id=?", (str(i.guild_id), self.world_id, selected_iso, self.user_id))
                conn.commit()
            for child in view.children: child.disabled = True
            await i.response.edit_message(content=f"[Completed] Abandoned ownership of **{selected_iso}**.", view=view)
        btn_abandon.callback = abandon_callback
        view.add_item(btn_abandon)
        await interaction.response.edit_message(content=f"**{selected_iso}** Management Menu", view=view)

class CountryManageMainView(discord.ui.View):
    def __init__(self, territories, world_id, user_id):
        super().__init__(timeout=60)
        self.add_item(CountryManageSelect(territories, world_id, user_id))

# --- 攻撃処理のロジック実行関数 ---
async def execute_attack_logic(interaction: discord.Interaction, target_code: str, active_world: int, total_cost: int):
    guild_id, user_id, user_name, world_id = str(interaction.guild_id), str(interaction.user.id), interaction.user.display_name, active_world
    
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT gold, oil FROM players WHERE guild_id=? AND world_id=? AND user_id=?", (guild_id, world_id, user_id))
        p_row = c.fetchone()
        current_gold, current_oil = (p_row[0], p_row[1]) if p_row else (0, 0)
        
        oil_enabled = is_oil_enabled(guild_id, world_id)
        oil_cost = total_cost if oil_enabled else 0
        if oil_enabled and current_oil < oil_cost: return await interaction.followup.send(f"[Error] Insufficient oil! (Required: {oil_cost} L / Current: {current_oil} L)", ephemeral=True)

        c.execute("SELECT owner_id, defense FROM territories WHERE guild_id=? AND world_id=? AND iso_alpha=?", (guild_id, world_id, target_code))
        def_row = c.fetchone()
        defender_id, base_def = (def_row[0], def_row[1]) if def_row else (None, DEFAULT_DEFENSE)

        if defender_id and is_allied(guild_id, world_id, user_id, defender_id): return await interaction.followup.send("[Error] You cannot attack an ally's territory!", ephemeral=True)

        war_status_text, defense_power, base_cost_multiplier = "Declared War", base_def, 1.0
        if defender_id and not is_at_war(guild_id, world_id, user_id, defender_id):
            war_status_text, defense_power, base_cost_multiplier = "[Warning] Surprise Attack/Forced March (Effective attack power reduced)", int(base_def * 1.5), 1.5
        
        distance_penalty, distance_text = 1.0, ""
        c.execute("SELECT adjacency_penalty FROM server_channels WHERE guild_id=?", (guild_id,))
        row_adj = c.fetchone()
        if row_adj and row_adj[0] == 1:
            c.execute("SELECT iso_alpha FROM territories WHERE guild_id=? AND world_id=? AND owner_id=?", (guild_id, world_id, user_id))
            my_lands = [r[0] for r in c.fetchall()]
            if my_lands and not any(target_code in ADJACENCY_GRAPH.get(l, set()) for l in my_lands):
                distance_penalty = 1.5
                distance_text = "\n[Warning] Expedition Penalty (Effective attack power reduced)"

        final_cost_multiplier = base_cost_multiplier * distance_penalty
        actual_power = int(total_cost / final_cost_multiplier)

        if current_gold < total_cost: return await interaction.followup.send(f"[Error] Insufficient funds. (Current: {current_gold} / Specified: {total_cost} Gold)", ephemeral=True)

        atk_roll, def_roll = int(actual_power * random.uniform(0.8, 1.2)), int(defense_power * random.uniform(0.8, 1.2))
        embed = discord.Embed(title=f"Operation Report: Invasion of {target_code} [World #{world_id}]")
        
        if atk_roll > def_roll:
            surviving_troops = max(10, actual_power - int(actual_power * random.uniform(0.1, 0.4)))
            c.execute("UPDATE players SET gold=?, oil=? WHERE guild_id=? AND world_id=? AND user_id=?", (current_gold - total_cost, current_oil - oil_cost, guild_id, world_id, user_id))
            c.execute("INSERT OR REPLACE INTO territories (guild_id, world_id, iso_alpha, owner_id, defense) VALUES (?, ?, ?, ?, ?)", (guild_id, world_id, target_code, user_id, surviving_troops))
            embed.color = 0x2ecc71
            embed.description = f"**Operation Successful! {user_name} captured {target_code}!**\n\nOperation Type: {war_status_text}{distance_text}\nFunds Spent: **{total_cost}** Gold\nEffective Attack Power: **{actual_power}** (after penalties)\nRemaining Troops: {surviving_troops}"
        else:
            new_defense = max(10, base_def - int(atk_roll * random.uniform(0.4, 0.8)))
            c.execute("UPDATE players SET gold=?, oil=? WHERE guild_id=? AND world_id=? AND user_id=?", (current_gold - total_cost, current_oil - oil_cost, guild_id, world_id, user_id))
            if defender_id: c.execute("UPDATE territories SET defense=? WHERE guild_id=? AND world_id=? AND iso_alpha=?", (new_defense, guild_id, world_id, target_code))
            embed.color = 0xe74c3c
            embed.description = f"**Operation Failed... Defeated by defensive forces.**\n\nOperation Type: {war_status_text}{distance_text}\nFunds Lost: **{total_cost}** Gold\nEffective Attack Power: **{actual_power}**"
        conn.commit()

    if defender_id:
        notify_users = set([defender_id])
        with get_db_connection() as conn3:
            c3 = conn3.cursor()
            c3.execute("SELECT user_a, user_b FROM alliances WHERE guild_id=? AND world_id=? AND (user_a=? OR user_b=?)", (guild_id, world_id, defender_id, defender_id))
            for ua, ub in c3.fetchall():
                notify_users.add(ua)
                notify_users.add(ub)
        if user_id in notify_users: notify_users.remove(user_id)

        async def send_dms():
            guild = bot.get_guild(int(guild_id))
            if not guild: return
            for uid in notify_users:
                member = guild.get_member(int(uid))
                if member and not member.bot:
                    try:
                        role_text = "Your territory" if uid == defender_id else f"Ally's (<@{defender_id}>) territory"
                        msg = f"[Emergency / World #{world_id}]\n{role_text} **[{target_code}]** has been attacked by <@{user_id}> ({user_name})! Check the situation immediately!"
                        await member.send(msg)
                    except Exception: pass
        bot.loop.create_task(send_dms())

    map_file, _ = await asyncio.to_thread(_generate_current_map_sync, guild_id, world_id)
    content_msg = get_promo_and_tip()
    
    if map_file: await interaction.channel.send(content=content_msg, embed=embed.set_image(url="attachment://war_map.png"), file=map_file)
    else: await interaction.channel.send(content=content_msg, embed=embed)
    
    if interaction.response.is_done(): await interaction.followup.send("[Completed] Operation complete. (Please check the channel)", ephemeral=True)
    else: await interaction.response.send_message("[Completed] Operation complete. (Please check the channel)", ephemeral=True)

class AttackConfirmView(discord.ui.View):
    def __init__(self, target_code: str, active_world: int, total_cost: int, actual_power: int, warning_text: str):
        super().__init__(timeout=60)
        self.target_code = target_code
        self.active_world = active_world
        self.total_cost = total_cost
        self.actual_power = actual_power
        self.warning_text = warning_text

    @discord.ui.button(label="March with these orders", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(content="Deploying forces...", view=self)
        await execute_attack_logic(interaction, self.target_code, self.active_world, self.total_cost)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(content="Operation cancelled.", view=self)

class TroopModal(discord.ui.Modal, title="Force Organization"):
    troops_input = discord.ui.TextInput(label="Funds to deploy (Cost)", placeholder="e.g. 1000", required=True)
    def __init__(self, target_code: str, active_world: int):
        super().__init__()
        self.target_code = target_code; self.active_world = active_world

    async def on_submit(self, interaction: discord.Interaction):
        raw_val = self.troops_input.value.translate(str.maketrans('０１２３４５６７８９', '0123456789')).replace(',', '').strip()
        try: total_cost = int(raw_val)
        except ValueError: return await interaction.response.send_message("[Error] Please enter a valid number.", ephemeral=True)
        if total_cost <= 0: return await interaction.response.send_message("[Error] Funds must be 1 or higher.", ephemeral=True)

        guild_id, user_id, world_id = str(interaction.guild_id), str(interaction.user.id), self.active_world
        
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT confirm_attack FROM user_settings WHERE guild_id=? AND user_id=?", (guild_id, user_id))
            row = c.fetchone()
            confirm = row[0] if row else 1
            
            c.execute("SELECT owner_id FROM territories WHERE guild_id=? AND world_id=? AND iso_alpha=?", (guild_id, world_id, self.target_code))
            def_row = c.fetchone()
            defender_id = def_row[0] if def_row else None
            
            base_cost_multiplier = 1.0
            if defender_id and not is_at_war(guild_id, world_id, user_id, defender_id):
                base_cost_multiplier = 1.5
            
            distance_penalty = 1.0
            c.execute("SELECT adjacency_penalty FROM server_channels WHERE guild_id=?", (guild_id,))
            row_adj = c.fetchone()
            if row_adj and row_adj[0] == 1:
                c.execute("SELECT iso_alpha FROM territories WHERE guild_id=? AND world_id=? AND owner_id=?", (guild_id, world_id, user_id))
                my_lands = [r[0] for r in c.fetchall()]
                if my_lands and not any(self.target_code in ADJACENCY_GRAPH.get(l, set()) for l in my_lands):
                    distance_penalty = 1.5

            final_cost_multiplier = base_cost_multiplier * distance_penalty
            actual_power = int(total_cost / final_cost_multiplier)

        if confirm == 1:
            warning = []
            if base_cost_multiplier > 1.0: warning.append("[Warning] Surprise attack penalty (no war declaration)")
            if distance_penalty > 1.0: warning.append("[Warning] Expedition penalty (not adjacent)")
            warn_str = "\n".join(warning) if warning else "[Info] No penalties"

            view = AttackConfirmView(self.target_code, world_id, total_cost, actual_power, warn_str)
            msg = f"**Final Confirmation**\nTarget: **{self.target_code}**\n\nDeployed Funds: **{total_cost} Gold**\n{warn_str}\n\nEffective Attack Power: **{actual_power}**\nAre you sure you want to deploy?"
            await interaction.response.send_message(msg, view=view, ephemeral=True)
        else:
            await safe_defer(interaction)
            await execute_attack_logic(interaction, self.target_code, world_id, total_cost)

class AttackView(discord.ui.View):
    def __init__(self, author_id: int, target_code: str, active_world: int):
        super().__init__(timeout=60)
        self.author_id, self.target_code, self.active_world = author_id, target_code, active_world

    @discord.ui.button(label="March", style=discord.ButtonStyle.danger)
    async def confirm_attack(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id: return await interaction.response.send_message("[Error] You cannot perform this action.", ephemeral=True)
        await interaction.response.send_modal(TroopModal(self.target_code, self.active_world))
        for child in self.children: child.disabled = True
        try: await interaction.message.edit(view=self)
        except Exception: pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_attack(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id: return await interaction.response.send_message("[Error] You cannot perform this action.", ephemeral=True)
        for child in self.children: child.disabled = True
        try: await interaction.message.edit(content="Operation cancelled.", embed=None, view=self)
        except Exception: pass

class AttackTargetModal(discord.ui.Modal, title="Target Selection"):
    target_input = discord.ui.TextInput(label="Target Country Code", placeholder="e.g. JPN", required=True)
    async def on_submit(self, interaction: discord.Interaction):
        await cmd_attack.callback(interaction, self.target_input.value)

# ==============================================================================
# バックグラウンドタスク (定時給付・リセット・CLI入力)
# ==============================================================================
async def console_input_task():
    loop = asyncio.get_running_loop()
    print("\n=====================================================")
    print("💻 [Terminal Communication Feature] is enabled!")
    print("Format: <Channel ID> <Message>")
    print("Example: 123456789012345678 Hello!")
    print("=====================================================\n")
    while True:
        try:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                await asyncio.sleep(1)
                continue
            line = line.strip()
            if not line: continue
            
            parts = line.split(" ", 1)
            if len(parts) == 2:
                channel_id_str, message = parts
                try:
                    channel_id = int(channel_id_str)
                    channel = bot.get_channel(channel_id)
                    if channel:
                        asyncio.create_task(channel.send(message))
                        print(f"✅ Sent to channel {channel_id}: {message}")
                    else:
                        print(f"❌ Channel {channel_id} not found or Bot lacks access.")
                except ValueError:
                    print("❌ Channel ID must be a number.")
            else:
                print("❌ Invalid format. Example: 1234567890 Hello!")
        except Exception as e:
            logger.error(f"Error in console input task: {e}")
            await asyncio.sleep(1)

@tasks.loop(time=[datetime.time(hour=7, tzinfo=datetime.timezone.utc), datetime.time(hour=19, tzinfo=datetime.timezone.utc)])
async def scheduled_tasks():
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    today_str = now_utc.strftime('%Y-%m-%d')
    if now_utc.hour == 19:
        reset_guilds = []
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT guild_id, reset_interval, last_reset_date FROM server_channels")
            for g_id, interval, last_date_str in c.fetchall():
                if interval > 0:
                    try:
                        last_date = datetime.datetime.strptime(last_date_str, '%Y-%m-%d').replace(tzinfo=datetime.timezone.utc)
                        if (now_utc - last_date).days >= interval: reset_guilds.append(g_id)
                    except: reset_guilds.append(g_id)
            for g_id in reset_guilds:
                for table in ['players', 'territories', 'alliances', 'wars', 'un_members', 'un_invites', 'camps', 'camp_members', 'camp_invites']: 
                    c.execute(f"DELETE FROM {table} WHERE guild_id=?", (g_id,))
                c.execute("UPDATE server_channels SET last_reset_date=? WHERE guild_id=?", (today_str, g_id))
            conn.commit()
        for g_id in reset_guilds:
            guild = bot.get_guild(int(g_id))
            if guild:
                with get_db_connection() as conn:
                    c = conn.cursor()
                    c.execute("SELECT notify_ch, notify_enabled FROM server_channels WHERE guild_id=?", (str(guild.id),))
                    row = c.fetchone()
                if row and row[1] == 1 and row[0]:
                    channel = guild.get_channel(int(row[0]))
                    if channel:
                        try: await channel.send("[Scheduled Reset Completed] Data wiped. A new history has begun.")
                        except: pass
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT owner_id, iso_alpha, guild_id, world_id FROM territories")
            income_map, territory_counts = {}, {}
            for o_id, iso, g_id, w_id in c.fetchall():
                key = (g_id, w_id, o_id)
                income_map[key] = income_map.get(key, 0) + TERRITORY_YIELD.get(iso, 50)
                territory_counts[key] = territory_counts.get(key, 0) + 1
            c.execute("SELECT guild_id, oil_enabled_w1, oil_enabled_w2, oil_enabled_w3 FROM server_channels")
            oil_configs = {r[0]: (r[1], r[2], r[3]) for r in c.fetchall()}
            
            c.execute("SELECT guild_id, world_id, user_id, gold, oil, debt FROM players")
            for g_id, w_id, u_id, gold, oil, debt in c.fetchall():
                key = (g_id, w_id, u_id)
                new_gold = gold + BASE_INCOME + income_map.get(key, 0)
                
                if debt and debt > 0:
                    repay = min(debt, int(new_gold * 0.5))
                    if repay > 0:
                        debt -= repay
                        new_gold -= repay
                        c.execute("UPDATE players SET debt=? WHERE guild_id=? AND world_id=? AND user_id=?", (debt, g_id, w_id, u_id))

                oil_enabled = bool(oil_configs.get(g_id, (1,1,1))[w_id-1]) if 1 <= w_id <= 3 else True
                if oil_enabled:
                    oil_drain = territory_counts.get(key, 0) * 25
                    c.execute("UPDATE players SET gold=?, oil=? WHERE guild_id=? AND world_id=? AND user_id=?", (new_gold, max(0, oil + 2000 - oil_drain), g_id, w_id, u_id))
                else:
                    c.execute("UPDATE players SET gold=? WHERE guild_id=? AND world_id=? AND user_id=?", (new_gold, g_id, w_id, u_id))
            conn.commit()
            
        for guild in bot.guilds:
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("SELECT notify_ch, notify_enabled FROM server_channels WHERE guild_id=?", (str(guild.id),))
                row = c.fetchone()
            if row and row[1] == 1 and row[0]:
                channel = guild.get_channel(int(row[0]))
                if channel:
                    try: await channel.send("[Scheduled Payout] Basic salary, tax revenues, and oil rations have been credited. (*A portion will be deducted if you have war debts.)")
                    except: pass
    except Exception as e: logger.error(f"Payout schedule error: {e}")

@tasks.loop(minutes=10)
async def update_presence():
    try:
        total_members = sum(guild.member_count for guild in bot.guilds if guild.member_count)
        await bot.change_presence(status=discord.Status.online, activity=discord.Activity(type=discord.ActivityType.watching, name=f"{total_members} users"))
    except: pass

    # 【追加】10分おきにSupabaseへデータを自動セーブ
    try:
        # 非同期処理を邪魔しないように、バックグラウンドスレッドで同期を実行
        await bot.loop.run_in_executor(None, db_sync.upload_db)
    except Exception as e:
        logger.error(f"[Sync] Scheduled upload error: {e}")

async def send_greeting_message(channel: discord.TextChannel):
    embed = discord.Embed(title="Welcome to Global War Bot", description="Thank you for adding the bot!\n\n**[For Admins]**\nRun `/op setup` to check all configuration commands.\n\n**[For Players]**\nCheck `/help` for rules, and `/gui` for easy actions!", color=0x2ecc71)
    try: await channel.send(embed=embed)
    except: pass

@bot.event
async def on_guild_join(guild: discord.Guild):
    channel = guild.system_channel
    if not channel:
        for ch in guild.text_channels:
            if ch.permissions_for(guild.me).send_messages: channel = ch; break
    if channel: await send_greeting_message(channel)

# ==============================================================================
# コマンド群定義
# ==============================================================================
@bot.tree.command(name="donate", description="Displays support links for the developer")
async def cmd_donate(interaction: discord.Interaction):
    await safe_defer(interaction)
    msg = f"**Support the Developer**\n{get_paypay_link()}"
    await interaction.followup.send(msg + get_promo_and_tip())

work_cooldowns = {} 

@bot.tree.command(name="work", description="Work to earn funds (UN members receive a bonus)")
async def cmd_work(interaction: discord.Interaction):
    await safe_defer(interaction, ephemeral=True)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return
    guild_id, user_id = str(interaction.guild_id), str(interaction.user.id)
    
    now = datetime.datetime.now(datetime.timezone.utc)
    key = (guild_id, world_id, user_id)
    if key in work_cooldowns:
        elapsed = (now - work_cooldowns[key]).total_seconds()
        if elapsed < 3600:
            minutes, seconds = divmod(int(3600 - elapsed), 60)
            return await interaction.followup.send(f"Still on cooldown. Please wait {minutes}m {seconds}s.", ephemeral=True)
            
    work_cooldowns[key] = now
    earned_gold = random.randint(300, 800)
    
    with get_db_connection() as conn:
        c = conn.cursor()
        check_and_create_user(c, guild_id, world_id, user_id, interaction.user.display_name)
        
        c.execute("SELECT 1 FROM un_members WHERE guild_id=? AND world_id=? AND user_id=?", (guild_id, world_id, user_id))
        is_un = c.fetchone()
        
        if is_un:
            earned_gold = int(earned_gold * 1.5)
            earned_oil = random.randint(75, 225)
            c.execute("UPDATE players SET gold=gold+?, oil=oil+? WHERE guild_id=? AND world_id=? AND user_id=?", (earned_gold, earned_oil, guild_id, world_id, user_id))
            msg_bonus = f"\n[UN Bonus] Mined extra funds and oil (**{earned_oil} L**)!"
        else:
            earned_oil = random.randint(50, 150)
            c.execute("UPDATE players SET gold=gold+?, oil=oil+? WHERE guild_id=? AND world_id=? AND user_id=?", (earned_gold, earned_oil, guild_id, world_id, user_id))
            msg_bonus = f"\n[Standard Mining] Mined oil (**{earned_oil} L**)!"
            
        c.execute("SELECT gold, oil FROM players WHERE guild_id=? AND world_id=? AND user_id=?", (guild_id, world_id, user_id))
        current_gold, current_oil = c.fetchone()
        conn.commit()
        
    msg = f"[Completed] Worked and earned `{earned_gold}` Gold in [World #{world_id}]! {msg_bonus}\nWallet: **{current_gold} Gold** / Oil: **{current_oil} L**"
    await interaction.followup.send(msg + get_promo_and_tip(), ephemeral=True)

@bot.tree.command(name="war_bonds", description="Borrow funds by issuing war bonds when out of money (deducted from payouts)")
async def cmd_war_bonds(interaction: discord.Interaction):
    await safe_defer(interaction, ephemeral=True)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return
    guild_id, user_id = str(interaction.guild_id), str(interaction.user.id)
    
    with get_db_connection() as conn:
        c = conn.cursor()
        check_and_create_user(c, guild_id, world_id, user_id, interaction.user.display_name)
        c.execute("SELECT gold, debt FROM players WHERE guild_id=? AND world_id=? AND user_id=?", (guild_id, world_id, user_id))
        row = c.fetchone()
        gold, debt = row[0], row[1] if row else (0, 0)

        if gold >= 1000:
            return await interaction.followup.send("[Error] War bonds can only be issued if your funds are **below 1000 Gold**.", ephemeral=True)
        if debt > 0:
            return await interaction.followup.send("[Error] You have already issued war bonds. You cannot issue them again until paid off.", ephemeral=True)

        c.execute("UPDATE players SET gold=gold+5000, debt=5000 WHERE guild_id=? AND world_id=? AND user_id=?", (guild_id, world_id, user_id))
        conn.commit()
    
    msg = f"[World #{world_id}] **War Bonds Issued!**\nBorrowed **5000 Gold** from the national treasury. A portion of your income will be automatically used for repayment during future payouts."
    await interaction.followup.send(msg + get_promo_and_tip(), ephemeral=True)

@bot.tree.command(name="import_oil", description="Apply to import (purchase) oil from a specified player")
async def cmd_import_oil(interaction: discord.Interaction, target: discord.Member, amount: int):
    await safe_defer(interaction)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return
    if amount <= 0: return await interaction.followup.send("[Error] Please specify 1L or more.", ephemeral=True)
    if target.bot or interaction.user.id == target.id: return await interaction.followup.send("[Error] You cannot apply to yourself or Bots.", ephemeral=True)
    
    guild_id, user_id, target_id = str(interaction.guild_id), str(interaction.user.id), str(target.id)
    price = int(amount * 100 / 150)

    with get_db_connection() as conn:
        c = conn.cursor()
        check_and_create_user(c, guild_id, world_id, user_id, interaction.user.display_name)
        c.execute("SELECT gold FROM players WHERE guild_id=? AND world_id=? AND user_id=?", (guild_id, world_id, user_id))
        row = c.fetchone()
        if not row or row[0] < price:
            return await interaction.followup.send(f"[Error] Insufficient funds. (Importing {amount} L requires **{price} Gold**)", ephemeral=True)

    view = OilImportView(guild_id, world_id, user_id, target_id, amount, price)
    content = f"**[Oil Import Request / World #{world_id}]**\n<@{user_id}> wants to purchase **{amount} L** of oil from you for **{price} Gold**.\nDo you approve?"
    
    if await send_dm_fallback(target, interaction.channel, content, view):
        await interaction.followup.send("[Completed] Sent oil import request to the user via DM.", ephemeral=True)
    else:
        await interaction.followup.send("[Completed] Sent oil import request to the user.", ephemeral=True)

@bot.tree.command(name="convert_resource", description="Exchange Gold and Oil with the system (treasury)")
@app_commands.choices(exchange_type=[
    app_commands.Choice(name="Pay Gold to get Oil (100G -> 150L)", value="gold_to_oil"),
    app_commands.Choice(name="Pay Oil to get Gold (150L -> 100G)", value="oil_to_gold")
])
async def cmd_convert_resource(interaction: discord.Interaction, exchange_type: app_commands.Choice[str], amount: int):
    await safe_defer(interaction, ephemeral=True)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return
    if amount <= 0: return await interaction.followup.send("[Error] Please specify 1 or more.", ephemeral=True)

    guild_id, user_id = str(interaction.guild_id), str(interaction.user.id)
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT gold, oil FROM players WHERE guild_id=? AND world_id=? AND user_id=?", (guild_id, world_id, user_id))
        row = c.fetchone()
        if not row: return await interaction.followup.send("[Error] Data not found.", ephemeral=True)
        gold, oil = row[0], row[1]

        if exchange_type.value == "gold_to_oil":
            if gold < amount:
                return await interaction.followup.send(f"[Error] Insufficient funds. ({amount} Gold required)", ephemeral=True)
            oil_gain = int(amount * 150 / 100)
            c.execute("UPDATE players SET gold=gold-?, oil=oil+? WHERE guild_id=? AND world_id=? AND user_id=?", (amount, oil_gain, guild_id, world_id, user_id))
            msg = f"[Completed] Paid **{amount} Gold** to the treasury and acquired **{oil_gain} L** of oil!"
        else:
            if oil < amount:
                return await interaction.followup.send(f"[Error] Insufficient oil. ({amount} L required)", ephemeral=True)
            gold_gain = int(amount * 100 / 150)
            c.execute("UPDATE players SET oil=oil-?, gold=gold+? WHERE guild_id=? AND world_id=? AND user_id=?", (amount, gold_gain, guild_id, world_id, user_id))
            msg = f"[Completed] Sold **{amount} L** of oil to the treasury and acquired **{gold_gain} Gold!**"
        conn.commit()

    await interaction.followup.send(msg + get_promo_and_tip(), ephemeral=True)

@bot.tree.command(name="pay", description="Transfer your funds (Gold) to a specified player")
async def cmd_pay(interaction: discord.Interaction, target: discord.Member, amount: int):
    await safe_defer(interaction)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return
    if amount <= 0: return await interaction.followup.send("[Error] Transfer amount must be 1 or more.")
    if target.bot or interaction.user.id == target.id: return await interaction.followup.send("[Error] You cannot transfer to yourself or bots.")
    guild_id, user_id, target_id = str(interaction.guild_id), str(interaction.user.id), str(target.id)
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT gold FROM players WHERE guild_id=? AND world_id=? AND user_id=?", (guild_id, world_id, user_id))
        row = c.fetchone()
        if not row or row[0] < amount: return await interaction.followup.send("[Error] Insufficient funds.")
        check_and_create_user(c, guild_id, world_id, target_id, target.display_name)
        c.execute("UPDATE players SET gold=gold-? WHERE guild_id=? AND world_id=? AND user_id=?", (amount, guild_id, world_id, user_id))
        c.execute("UPDATE players SET gold=gold+? WHERE guild_id=? AND world_id=? AND user_id=?", (amount, guild_id, world_id, target_id))
        conn.commit()
    await interaction.followup.send(f"[Transfer Complete / World #{world_id}] <@{user_id}> transferred **{amount} Gold** to <@{target_id}>!" + get_promo_and_tip())

@bot.tree.command(name="say", description="Send a private message to a specified user or camp")
async def cmd_say(interaction: discord.Interaction, target: str, message: str):
    await safe_defer(interaction, ephemeral=True)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT user_id FROM camp_members WHERE guild_id=? AND world_id=? AND camp_name=?", (str(interaction.guild_id), world_id, target))
        camp_members = c.fetchall()

    if camp_members:
        success_count = 0
        for (uid,) in camp_members:
            member = interaction.guild.get_member(int(uid))
            if member and not member.bot:
                try: await member.send(f"**[Camp Comm: {target}]** From {interaction.user.display_name}:\n{message}"); success_count += 1
                except: pass
        await interaction.followup.send(f"[Complete] Sent to {success_count} members of camp \"{target}\".", ephemeral=True)
    else:
        target_member = discord.utils.find(lambda m: target.lower() in m.display_name.lower() or target.lower() in m.name.lower(), interaction.guild.members)
        if not target_member: return await interaction.followup.send("[Error] Not found.", ephemeral=True)
        try:
            await target_member.send(f"**[Message]** From {interaction.user.display_name}:\n{message}")
            await interaction.followup.send(f"[Complete] Sent to {target_member.display_name}.", ephemeral=True)
        except: await interaction.followup.send("[Error] Failed to send.", ephemeral=True)

async def my_countries_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    guild_id, user_id = str(interaction.guild_id), str(interaction.user.id)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return []
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT iso_alpha FROM territories WHERE guild_id=? AND world_id=? AND owner_id=?", (guild_id, world_id, user_id))
        choices = [row[0] for row in c.fetchall() if current.lower() in row[0].lower()]
        return [app_commands.Choice(name=f"Territory: {code}", value=code) for code in choices[:25]]

@bot.tree.command(name="country_name", description="Set your main country from the territories you own")
@app_commands.autocomplete(target_code=my_countries_autocomplete)
async def cmd_country_name(interaction: discord.Interaction, target_code: str):
    await safe_defer(interaction)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return
    guild_id, user_id = str(interaction.guild_id), str(interaction.user.id)
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT 1 FROM territories WHERE guild_id=? AND world_id=? AND owner_id=? AND iso_alpha=?", (guild_id, world_id, user_id, target_code))
        if not c.fetchone(): return await interaction.followup.send("[Error] You do not own this country.", ephemeral=True)
        c.execute("UPDATE players SET main_country=? WHERE guild_id=? AND world_id=? AND user_id=?", (target_code, guild_id, world_id, user_id))
        conn.commit()
    await interaction.followup.send(f"[Settings] [World #{world_id}] Set main country to **[{target_code}]**!")

@bot.tree.command(name="targets", description="Check the list of currently attackable countries")
async def cmd_targets(interaction: discord.Interaction):
    await safe_defer(interaction, ephemeral=True)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return
    guild_id, user_id = str(interaction.guild_id), str(interaction.user.id)
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT iso_alpha, owner_id FROM territories WHERE guild_id=? AND world_id=?", (guild_id, world_id))
        occupied = {row[0]: row[1] for row in c.fetchall()}
        c.execute("SELECT iso_alpha FROM territories WHERE guild_id=? AND world_id=? AND owner_id=?", (guild_id, world_id, user_id))
        my_lands = [r[0] for r in c.fetchall()]
        
    adj_targets = set()
    for land in my_lands: adj_targets.update(ADJACENCY_GRAPH.get(land, set()))
    unoccupied_lands = [code for code in VALID_CODES if code not in occupied]
    enemy_lands = [code for code, owner in occupied.items() if owner != user_id and not is_allied(guild_id, world_id, user_id, owner)]
    
    def format_land(code): return f"`{code}`*" if code in adj_targets else f"`{code}`"
    
    embed = discord.Embed(title=f"List of Attackable Countries/Regions [World #{world_id}]", description="* Indicates adjacency to your own territory.", color=0xe74c3c)
    embed.add_field(name="Unclaimed Lands", value=" ".join([format_land(c) for c in sorted(unoccupied_lands)])[:1024] if unoccupied_lands else "None", inline=False)
    embed.add_field(name="Enemy/Other Players' Lands", value=" ".join([format_land(c) for c in sorted(enemy_lands)])[:1024] if enemy_lands else "None", inline=False)
    await interaction.followup.send(content=get_promo_and_tip(), embed=embed, ephemeral=True)

@bot.tree.command(name="gui", description="Perform diplomatic actions such as declaring war or proposing alliances via GUI")
async def cmd_diplomacy(interaction: discord.Interaction):
    await safe_defer(interaction, ephemeral=True)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return
    view = discord.ui.View(timeout=60)
    view.add_item(DiplomacyActionSelect(world_id))
    await interaction.followup.send(f"**Ministry of Foreign Affairs** [World #{world_id}]\nSelect the diplomatic action you wish to perform.", view=view, ephemeral=True)

@bot.tree.command(name="attack", description="Plan a military campaign against a specified country")
async def cmd_attack(interaction: discord.Interaction, target: str):
    await safe_defer(interaction, ephemeral=True)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return

    code = resolve_country_code(target)
    if code not in VALID_CODES: return await interaction.followup.send(f"[Error] Country name does not exist: {target}", ephemeral=True)
    guild_id, user_id = str(interaction.guild_id), str(interaction.user.id)
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT owner_id, defense FROM territories WHERE guild_id=? AND world_id=? AND iso_alpha=?", (guild_id, world_id, code))
        def_row = c.fetchone()
    
    defender_id = def_row[0] if def_row else None
    base_def = def_row[1] if def_row else DEFAULT_DEFENSE
    owner_name, warning_text = "Unclaimed", ""

    base_cost_multiplier = 1.0
    if defender_id:
        if defender_id == user_id: return await interaction.followup.send("[Error] This is already your territory.", ephemeral=True)
        try:
            owner_user = await bot.fetch_user(int(defender_id))
            owner_name = owner_user.display_name
        except: pass
        if not is_at_war(guild_id, world_id, user_id, defender_id):
            base_cost_multiplier = 1.5
            warning_text = f"\n\n[Warning] {code} currently belongs to **{owner_name}**.\nAttacking without declaring war will reduce your effective attack power."

    distance_penalty = 1.0
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT adjacency_penalty FROM server_channels WHERE guild_id=?", (guild_id,))
        row_adj = c.fetchone()
        if row_adj and row_adj[0] == 1:
            c.execute("SELECT iso_alpha FROM territories WHERE guild_id=? AND world_id=? AND owner_id=?", (guild_id, world_id, user_id))
            my_lands = [r[0] for r in c.fetchall()]
            if my_lands and not any(code in ADJACENCY_GRAPH.get(l, set()) for l in my_lands):
                distance_penalty = 1.5

    final_cost_multiplier = base_cost_multiplier * distance_penalty
    
    min_est = max(100, int(base_def * final_cost_multiplier * 0.8))
    max_est = max(100, int(base_def * final_cost_multiplier * 1.2))

    embed = discord.Embed(title=f"Military Headquarters [World #{world_id}]", description=f"Target: **{code}**{warning_text}\nPress march when you are ready.", color=0x3498db)
    embed.add_field(name="Estimated Campaign Cost", value=f"Estimated Gold required: **{min_est} - {max_est} Gold**\n*(※Varies based on enemy defense and random factors. Spent Gold will not be refunded)*")
    
    await interaction.followup.send(content=get_promo_and_tip(), embed=embed, view=AttackView(interaction.user.id, code, world_id), ephemeral=True)

@bot.tree.command(name="status", description="Check your current funds, total military strength, debt, etc.")
async def cmd_status(interaction: discord.Interaction):
    await safe_defer(interaction, ephemeral=True)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return
    guild_id, user_id = str(interaction.guild_id), str(interaction.user.id)
    with get_db_connection() as conn:
        c = conn.cursor()
        check_and_create_user(c, guild_id, world_id, user_id, interaction.user.display_name)
        c.execute("SELECT gold, oil, main_country, debt FROM players WHERE guild_id=? AND world_id=? AND user_id=?", (guild_id, world_id, user_id))
        row = c.fetchone()
        gold, oil, main_country, debt = row[0], row[1], row[2], row[3] if row else (0,0,"",0)
        c.execute("SELECT iso_alpha, defense FROM territories WHERE guild_id=? AND world_id=? AND owner_id=?", (guild_id, world_id, user_id))
        territories = c.fetchall()
    
    tax = sum(TERRITORY_YIELD.get(t[0], 50) for t in territories)
    oil_drain = len(territories) * 25
    oil_status = f"[System Active]\n(Next: Supply 2000 - Maintenance {oil_drain} = **{2000 - oil_drain} L**)" if is_oil_enabled(guild_id, world_id) else "[Inactive]"

    embed = discord.Embed(title=f"National Status [World #{world_id}]", color=0xf1c40f)
    embed.add_field(name="Main Country", value=f"**{main_country}**")
    embed.add_field(name="Funds Held", value=f"**{gold}** Gold")
    embed.add_field(name="Oil Stockpile", value=f"**{oil}** L\n{oil_status}")
    embed.add_field(name="Total Military Strength (Total Defense)", value=f"**{sum(t[1] for t in territories)}**")
    embed.add_field(name="Next Scheduled Income", value=f"Base {BASE_INCOME} + Tax Revenue {tax} = **{BASE_INCOME + tax}**")
    if debt and debt > 0:
        embed.add_field(name="Debt (War Bond Balance)", value=f"**{debt}** Gold\n(Auto-deducted from salary/income)")
    await interaction.followup.send(content=get_promo_and_tip(), embed=embed, ephemeral=True)

@bot.tree.command(name="map", description="Display the tactical map of the specified world")
@app_commands.choices(world_num=[app_commands.Choice(name="World #1", value=1), app_commands.Choice(name="World #2", value=2), app_commands.Choice(name="World #3", value=3)])
async def cmd_map(interaction: discord.Interaction, world_num: app_commands.Choice[int]):
    await safe_defer(interaction)
    w_id = world_num.value
    map_file, occupied_lands = await asyncio.to_thread(_generate_current_map_sync, str(interaction.guild_id), w_id)
    embed = discord.Embed(title=f"World Situation [World #{w_id}]", color=0x3498db)
    owner_dict = {}
    for iso, _, _, _, u_name in occupied_lands: owner_dict.setdefault(u_name, []).append(iso)
    desc = "\n".join([f"**{u_name}**: {', '.join(lands)}" for u_name, lands in owner_dict.items()])
    embed.description = "[Territorial Control]\n" + desc[:4000] if desc else "Currently, no territories are occupied."
    if map_file: await interaction.followup.send(content=get_promo_and_tip(), embed=embed.set_image(url="attachment://war_map.png"), file=map_file)
    else: await interaction.followup.send(content=get_promo_and_tip(), embed=embed)

@bot.tree.command(name="country_management", description="Manage owned territories (e.g. abandon ownership) via GUI")
async def cmd_country_management(interaction: discord.Interaction):
    await safe_defer(interaction, ephemeral=True)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return
    guild_id, user_id = str(interaction.guild_id), str(interaction.user.id)
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT iso_alpha, defense FROM territories WHERE guild_id=? AND world_id=? AND owner_id=?", (guild_id, world_id, user_id))
        territories = c.fetchall()
    if not territories: return await interaction.followup.send("[Error] You do not own any territories that can be managed.", ephemeral=True)
    await interaction.followup.send(f"**Territory Management [World #{world_id}]**\nSelect the territory you want to manage from the menu below.", view=CountryManageMainView(territories, world_id, user_id), ephemeral=True)

@bot.tree.command(name="country_status", description="Check the defense levels of your territories")
async def cmd_country_status(interaction: discord.Interaction):
    await safe_defer(interaction, ephemeral=True)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return
    guild_id, user_id = str(interaction.guild_id), str(interaction.user.id)
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT iso_alpha, defense FROM territories WHERE guild_id=? AND world_id=? AND owner_id=?", (guild_id, world_id, user_id))
        lands = c.fetchall()
    if not lands: return await interaction.followup.send(f"[World #{world_id}] You have no territories.", ephemeral=True)
    embed = discord.Embed(title=f"Defense Status of Owned Territories [World #{world_id}]", description="\n".join([f"・ **{iso}**: Defense {defen}" for iso, defen in lands]), color=0x2ecc71)
    await interaction.followup.send(embed=embed, ephemeral=True)


# --- 複数選択・一括防衛システムの実装 ---
async def execute_defend_logic(interaction: discord.Interaction, target_codes: list[str], amount_per_country: int, world_id: int):
    if amount_per_country <= 0:
        return await interaction.followup.send("[Error] Amount must be 1 or more.", ephemeral=True)
    
    guild_id, user_id = str(interaction.guild_id), str(interaction.user.id)
    total_cost = amount_per_country * len(target_codes)
    
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT gold FROM players WHERE guild_id=? AND world_id=? AND user_id=?", (guild_id, world_id, user_id))
        p_row = c.fetchone()
        if not p_row or p_row[0] < total_cost:
            return await interaction.followup.send(f"[Error] Insufficient funds. (Required: {total_cost} Gold)", ephemeral=True)
            
        c.execute("SELECT COUNT(*) FROM territories WHERE guild_id=? AND world_id=? AND owner_id=?", (guild_id, world_id, user_id))
        my_lands = c.fetchone()[0]
        is_empire = my_lands >= 3
        gained_defense = int(amount_per_country / 0.9) if is_empire else amount_per_country
        
        valid_targets = []
        for code in target_codes:
            c.execute("SELECT owner_id, defense FROM territories WHERE guild_id=? AND world_id=? AND iso_alpha=?", (guild_id, world_id, code))
            t_row = c.fetchone()
            if t_row and t_row[0] == user_id:
                valid_targets.append(code)
                
        if not valid_targets:
            return await interaction.followup.send("[Error] Target territory not found or is not owned by you.", ephemeral=True)
            
        actual_total_cost = amount_per_country * len(valid_targets)
        c.execute("UPDATE players SET gold=gold-? WHERE guild_id=? AND world_id=? AND user_id=?", (actual_total_cost, guild_id, world_id, user_id))
        
        for code in valid_targets:
            c.execute("UPDATE territories SET defense=defense+? WHERE guild_id=? AND world_id=? AND iso_alpha=?", (gained_defense, guild_id, world_id, code))
            
        conn.commit()
    
    empire_msg = "\n[Empire Bonus] Defense cost efficiency increased." if is_empire else ""
    msg = f"[Complete] Invested {amount_per_country} Gold each in {', '.join(valid_targets)}. (Defense increased: +{gained_defense}){empire_msg}"
    await interaction.followup.send(msg + get_promo_and_tip(), ephemeral=True)

class DefendAmountModal(discord.ui.Modal, title="Defense Investment"):
    amount_input = discord.ui.TextInput(label="Investment per Country", placeholder="e.g. 100", required=True)
    def __init__(self, selected_codes, active_world):
        super().__init__()
        self.selected_codes = selected_codes
        self.active_world = active_world

    async def on_submit(self, interaction: discord.Interaction):
        await safe_defer(interaction, ephemeral=True)
        try: amount = int(self.amount_input.value)
        except ValueError: return await interaction.followup.send("[Error] Please enter a valid number.", ephemeral=True)
        await execute_defend_logic(interaction, self.selected_codes, amount, self.active_world)

class DefendMultiSelect(discord.ui.Select):
    def __init__(self, territories, active_world):
        self.active_world = active_world
        options = [discord.SelectOption(label=t[0], description=f"Current Defense: {t[1]}") for t in territories[:25]]
        super().__init__(placeholder="Select territories to invest (multiple allowed)", min_values=1, max_values=len(options), options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(DefendAmountModal(self.values, self.active_world))

class DefendView(discord.ui.View):
    def __init__(self, territories, active_world):
        super().__init__(timeout=60)
        self.add_item(DefendMultiSelect(territories, active_world))

@bot.tree.command(name="defend", description="Invest in territory defense (displays menu if unspecified; use ALL for all territories)")
async def cmd_defend(interaction: discord.Interaction, target: str = None, amount: int = None):
    # 引数が未指定の場合はGUI（セレクトメニュー）を表示
    if target is None or amount is None:
        world_id = await ensure_world_context(interaction)
        if world_id == 0: return
        guild_id, user_id = str(interaction.guild_id), str(interaction.user.id)
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT iso_alpha, defense FROM territories WHERE guild_id=? AND world_id=? AND owner_id=?", (guild_id, world_id, user_id))
            territories = c.fetchall()
        if not territories:
            msg = "[Error] You do not own any territories."
            if interaction.response.is_done(): await interaction.followup.send(msg, ephemeral=True)
            else: await interaction.response.send_message(msg, ephemeral=True)
            return
        if interaction.response.is_done(): await interaction.followup.send("Please select the territories you want to invest in.", view=DefendView(territories, world_id), ephemeral=True)
        else: await interaction.response.send_message("Please select the territories you want to invest in.", view=DefendView(territories, world_id), ephemeral=True)
        return

    # 引数が指定されている場合の直接処理
    await safe_defer(interaction, ephemeral=True)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return

    target_codes = []
    if target.upper() == "ALL":
        guild_id, user_id = str(interaction.guild_id), str(interaction.user.id)
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT iso_alpha FROM territories WHERE guild_id=? AND world_id=? AND owner_id=?", (guild_id, world_id, user_id))
            target_codes = [r[0] for r in c.fetchall()]
    else:
        target_codes = [resolve_country_code(t.strip()) for t in target.split(",") if t.strip()]

    if not target_codes:
        return await interaction.followup.send("[Error] No valid country codes specified.", ephemeral=True)

    await execute_defend_logic(interaction, target_codes, amount, world_id)


@bot.tree.command(name="withdraw", description="Convert defense of your territory back into funds (Gold)")
async def cmd_withdraw(interaction: discord.Interaction, target: str, amount: int):
    await safe_defer(interaction, ephemeral=True)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return
    if amount <= 0: return await interaction.followup.send("[Error] Please specify 1 or more.", ephemeral=True)
    guild_id, user_id, code = str(interaction.guild_id), str(interaction.user.id), resolve_country_code(target)
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT defense FROM territories WHERE guild_id=? AND world_id=? AND iso_alpha=? AND owner_id=?", (guild_id, world_id, code, user_id))
        row = c.fetchone()
        if not row: return await interaction.followup.send("[Error] This is not your territory.", ephemeral=True)
        if row[0] - amount < 100: return await interaction.followup.send(f"[Error] Defense must remain at least 100.", ephemeral=True)
        c.execute("UPDATE territories SET defense=defense-? WHERE guild_id=? AND world_id=? AND iso_alpha=?", (amount, guild_id, world_id, code))
        c.execute("UPDATE players SET gold=gold+? WHERE guild_id=? AND world_id=? AND user_id=?", (amount, guild_id, world_id, user_id))
        conn.commit()
    msg = f"[Complete] Withdrew {amount} defense from {code}."
    await interaction.followup.send(msg + get_promo_and_tip(), ephemeral=True)

@bot.tree.command(name="reallocate", description="Reallocate defense from one of your territories to another")
async def cmd_reallocate(interaction: discord.Interaction, from_country: str, to_country: str, amount: int):
    await safe_defer(interaction, ephemeral=True)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return
    if amount <= 0: return await interaction.followup.send("[Error] Please specify 1 or more.", ephemeral=True)
    guild_id, user_id = str(interaction.guild_id), str(interaction.user.id)
    f_code, t_code = resolve_country_code(from_country), resolve_country_code(to_country)
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT defense FROM territories WHERE guild_id=? AND world_id=? AND iso_alpha=? AND owner_id=?", (guild_id, world_id, f_code, user_id))
        f_row = c.fetchone()
        c.execute("SELECT defense FROM territories WHERE guild_id=? AND world_id=? AND iso_alpha=? AND owner_id=?", (guild_id, world_id, t_code, user_id))
        t_row = c.fetchone()
        if not f_row or not t_row: return await interaction.followup.send("[Error] Both territories must belong to you.", ephemeral=True)
        if f_row[0] - amount < 100: return await interaction.followup.send(f"[Error] Post-transfer, defense of {f_code} must be at least 100.", ephemeral=True)
        c.execute("UPDATE territories SET defense=defense-? WHERE guild_id=? AND world_id=? AND iso_alpha=?", (amount, guild_id, world_id, f_code))
        c.execute("UPDATE territories SET defense=defense+? WHERE guild_id=? AND world_id=? AND iso_alpha=?", (amount, guild_id, world_id, t_code))
        conn.commit()
    msg = f"[Complete] Moved {amount} defense from {f_code} to {t_code}."
    await interaction.followup.send(msg + get_promo_and_tip(), ephemeral=True)

@bot.tree.command(name="code", description="Check the list of available country names and codes")
async def cmd_code(interaction: discord.Interaction):
    await safe_defer(interaction, ephemeral=True)
    unique_codes = {code: name for name, code in COUNTRY_MAP.items() if not name.isascii()}
    embed = discord.Embed(title="Country Code & Name List", description="Both country names and codes are accepted in commands.", color=0x3498db)
    current_text = ""
    for code, name in sorted(unique_codes.items()):
        item = f"`{code}:{name}` "
        if len(current_text) + len(item) > 1000:
            embed.add_field(name="\u200b", value=current_text, inline=False); current_text = item
        else: current_text += item
    if current_text: embed.add_field(name="\u200b", value=current_text, inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="invite_un", description="Invite a specified player to the United Nations (UN)")
async def cmd_invite_un(interaction: discord.Interaction, target: discord.Member):
    await safe_defer(interaction)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return
    if target.bot: return await interaction.followup.send("[Error] Cannot invite bots.")
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO un_invites (guild_id, world_id, user_id) VALUES (?, ?, ?)", (str(interaction.guild_id), world_id, str(target.id)))
        conn.commit()
    await interaction.followup.send(f"[Complete / World #{world_id}] Invited <@{target.id}> to the UN.")

@bot.tree.command(name="join_un", description="Accept the UN invitation and join")
async def cmd_join_un(interaction: discord.Interaction):
    await safe_defer(interaction)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return
    guild_id, user_id = str(interaction.guild_id), str(interaction.user.id)
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT 1 FROM un_invites WHERE guild_id=? AND world_id=? AND user_id=?", (guild_id, world_id, user_id))
        if not c.fetchone(): return await interaction.followup.send("[Error] You have not been invited.")
        c.execute("DELETE FROM un_invites WHERE guild_id=? AND world_id=? AND user_id=?", (guild_id, world_id, user_id))
        c.execute("INSERT OR IGNORE INTO un_members (guild_id, world_id, user_id) VALUES (?, ?, ?)", (guild_id, world_id, user_id))
        conn.commit()
    await interaction.followup.send(f"[Complete / World #{world_id}] <@{user_id}> joined the UN.")

@bot.tree.command(name="leave_un", description="Leave the United Nations (UN)")
async def cmd_leave_un(interaction: discord.Interaction):
    await safe_defer(interaction)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return
    guild_id, user_id = str(interaction.guild_id), str(interaction.user.id)
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT 1 FROM un_members WHERE guild_id=? AND world_id=? AND user_id=?", (guild_id, world_id, user_id))
        if not c.fetchone(): return await interaction.followup.send("[Error] You are not a member.")
        c.execute("DELETE FROM un_members WHERE guild_id=? AND world_id=? AND user_id=?", (guild_id, world_id, user_id))
        conn.commit()
    await interaction.followup.send(f"[Complete] <@{user_id}> left the UN.")

@bot.tree.command(name="un_list", description="Check the list of current UN member nations")
async def cmd_un_list(interaction: discord.Interaction):
    if not interaction.response.is_done(): await interaction.response.defer(ephemeral=True)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT user_id FROM un_members WHERE guild_id=? AND world_id=?", (str(interaction.guild_id), world_id))
        members = c.fetchall()
    if not members: return await interaction.followup.send("There are no member players.", ephemeral=True)
    embed = discord.Embed(title=f"UN Member List [World #{world_id}]", color=0x3498db)
    embed.description = "\n".join([f"・ <@{m[0]}>" for m in members])
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="create_camp", description="Create a new alliance camp")
async def cmd_create_camp(interaction: discord.Interaction, camp_name: str):
    await safe_defer(interaction)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return
    guild_id, user_id = str(interaction.guild_id), str(interaction.user.id)
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT 1 FROM camps WHERE guild_id=? AND world_id=? AND camp_name=?", (guild_id, world_id, camp_name))
        if c.fetchone(): return await interaction.followup.send("[Error] This camp already exists.")
        c.execute("INSERT INTO camps (guild_id, world_id, camp_name, founder_id) VALUES (?, ?, ?, ?)", (guild_id, world_id, camp_name, user_id))
        c.execute("INSERT INTO camp_members (guild_id, world_id, user_id, camp_name) VALUES (?, ?, ?, ?)", (guild_id, world_id, user_id, camp_name))
        conn.commit()
    await interaction.followup.send(f"[Complete] A new camp **[{camp_name}]** has been established!")

@bot.tree.command(name="invite_camp", description="Invite a specified player to your camp")
async def cmd_invite_camp(interaction: discord.Interaction, target: discord.Member, camp_name: str):
    await safe_defer(interaction)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return
    if target.bot: return await interaction.followup.send("[Error] Cannot invite bots.")
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT 1 FROM camp_members WHERE guild_id=? AND world_id=? AND user_id=? AND camp_name=?", (str(interaction.guild_id), world_id, str(interaction.user.id), camp_name))
        if not c.fetchone(): return await interaction.followup.send("[Error] You are not a member of this camp.")
        c.execute("INSERT OR REPLACE INTO camp_invites (guild_id, world_id, user_id, camp_name) VALUES (?, ?, ?, ?)", (str(interaction.guild_id), world_id, str(target.id), camp_name))
        conn.commit()
    await interaction.followup.send(f"[Complete] Invited <@{target.id}> to camp **[{camp_name}]**.")

@bot.tree.command(name="join_camp", description="Join a camp you have been invited to")
async def cmd_join_camp(interaction: discord.Interaction, camp_name: str):
    await safe_defer(interaction)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return
    guild_id, user_id = str(interaction.guild_id), str(interaction.user.id)
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT 1 FROM camp_invites WHERE guild_id=? AND world_id=? AND user_id=? AND camp_name=?", (guild_id, world_id, user_id, camp_name))
        if not c.fetchone(): return await interaction.followup.send("[Error] You have not been invited.")
        c.execute("DELETE FROM camp_invites WHERE guild_id=? AND world_id=? AND user_id=? AND camp_name=?", (guild_id, world_id, user_id, camp_name))
        c.execute("INSERT OR IGNORE INTO camp_members (guild_id, world_id, user_id, camp_name) VALUES (?, ?, ?, ?)", (guild_id, world_id, user_id, camp_name))
        conn.commit()
    await interaction.followup.send(f"[Complete] <@{user_id}> joined camp **[{camp_name}]**.")

@bot.tree.command(name="invite", description="Invite a specified player to your camp (same as /invite_camp)")
async def cmd_invite(interaction: discord.Interaction, target: discord.Member, camp_name: str):
    await cmd_invite_camp.callback(interaction, target, camp_name)

@bot.tree.command(name="camp_list", description="Check the list of current camps")
async def cmd_camp_list(interaction: discord.Interaction):
    if not interaction.response.is_done(): await interaction.response.defer(ephemeral=True)
    world_id = await ensure_world_context(interaction)
    if world_id == 0: return
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT camp_name, founder_id FROM camps WHERE guild_id=? AND world_id=?", (str(interaction.guild_id), world_id))
        camps = c.fetchall()
        if not camps: return await interaction.followup.send("There are currently no camps established.", ephemeral=True)
        
        embed = discord.Embed(title=f"Camp List [World #{world_id}]", color=0x2ecc71)
        for camp_name, founder_id in camps:
            c.execute("SELECT user_id FROM camp_members WHERE guild_id=? AND world_id=? AND camp_name=?", (str(interaction.guild_id), world_id, camp_name))
            members = c.fetchall()
            member_count = len(members)
            embed.add_field(name=f"[{camp_name}]", value=f"Founder: <@{founder_id}>\nMembers: {member_count}", inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="join", description="Display the invite link to the official support server")
async def cmd_join(interaction: discord.Interaction):
    await safe_defer(interaction)
    await interaction.followup.send("Join our official support server here!\nAsk questions, report bugs, or hang out with other players.\nhttps://discord.gg/3vFrHqamgv")

@bot.tree.command(name="world_setting", description="Manually set your active world (for servers without designated channels)")
@app_commands.choices(world_num=[app_commands.Choice(name="World #1", value=1), app_commands.Choice(name="World #2", value=2), app_commands.Choice(name="World #3", value=3)])
async def cmd_world_setting(interaction: discord.Interaction, world_num: app_commands.Choice[int]):
    await safe_defer(interaction, ephemeral=True)
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO user_settings (guild_id, user_id, active_world) VALUES (?, ?, ?)", (str(interaction.guild_id), str(interaction.user.id), world_num.value))
        conn.commit()
    await interaction.followup.send(f"[Settings] Set your active world to **World #{world_num.value}**.", ephemeral=True)

@bot.tree.command(name="command", description="Display all commands categorized and open the execution GUI")
async def cmd_command(interaction: discord.Interaction):
    await safe_defer(interaction, ephemeral=True)
    embed = discord.Embed(title="All Commands (by Category)", color=0x3498db)
    embed.add_field(name="Admin & Settings (For OP)", value="`/op setup` : Initial Setup\n`/op reset` : Reset\n*All accessible via /op", inline=False)
    embed.add_field(name="Main Game & Invasion", value="`/attack` : Attack\n`/targets` : Check targets\n`/gui` : Diplomacy Panel", inline=False)
    embed.add_field(name="Domestic Affairs & Defense", value="`/defend` : Invest in defense\n`/withdraw` : Refund funds\n`/reallocate` : Move defense\n`/user_setting` : Dialogue settings etc.", inline=False)
    embed.add_field(name="Economy, Trade & Info", value="`/work` : Work\n`/war_bonds` : Issue war bonds\n`/status` : Check status\n`/import_oil` : Import oil\n`/convert_resource` : Convert resources", inline=False)
    await interaction.followup.send(embed=embed, view=CommandGUIView(), ephemeral=True)

# ==============================================================================
# Error Handling & Startup Events
# ==============================================================================
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        minutes, seconds = divmod(int(error.retry_after), 60)
        msg = f"Still preparing. Please wait {minutes}m {seconds}s."
        try:
            if interaction.response.is_done(): await interaction.followup.send(msg, ephemeral=True)
            else: await interaction.response.send_message(msg, ephemeral=True)
        except: pass
    elif isinstance(error, app_commands.CheckFailure) or isinstance(error, app_commands.MissingPermissions):
        msg = "[Error] You do not have permission to execute this command (or OP permission required)."
        try:
            if interaction.response.is_done(): await interaction.followup.send(msg, ephemeral=True)
            else: await interaction.response.send_message(msg, ephemeral=True)
        except: pass
    else:
        logger.error(f"Command Error ({interaction.command.name}): \n{traceback.format_exc()}")
        try:
            msg = "[Error] An unexpected error occurred.\n*If it is a 403 Forbidden permission error, please review the bot's channel permissions."
            if interaction.response.is_done(): await interaction.followup.send(msg, ephemeral=True)
            else: await interaction.response.send_message(msg, ephemeral=True)
        except: pass

@bot.event
async def on_ready():
    logger.info(f"Bot is online: {bot.user}")
    
    # 【追加】起動時にSupabaseからデータをダウンロードして最新に復元
    try:
        await bot.loop.run_in_executor(None, db_sync.download_db)
    except Exception as e:
        logger.error(f"[Sync] Error downloading on startup: {e}")

    try: 
        await bot.tree.sync()
        logger.info("Synced slash commands.")
    except Exception as e: logger.error(f"Command sync error: {e}")
    if not scheduled_tasks.is_running(): scheduled_tasks.start()
    if not update_presence.is_running(): update_presence.start()
    
    bot.loop.create_task(console_input_task())

if __name__ == "__main__":
    if not BOT_TOKEN: print("[Error] DISCORD_BOT_TOKEN is not set in the .env file.")
    else: bot.run(BOT_TOKEN)
