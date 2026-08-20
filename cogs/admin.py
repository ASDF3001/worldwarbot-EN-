import discord
from discord import app_commands
from discord.ext import commands
from main import (
    get_db_connection, safe_defer, is_slash_op_or_admin, ensure_world_context
)

class AdminCog(commands.Cog):
    @app_commands.command(name="op_setup", description="Initial setup: Automatically creates a category and 3 channels for the game.")
    @is_slash_op_or_admin()
    async def cmd_setup(self, interaction: discord.Interaction):
        await safe_defer(interaction)
        category = await interaction.guild.create_category("World War Bot")
        ch1 = await category.create_text_channel("world-war-1")
        ch2 = await category.create_text_channel("world-war-2")
        ch3 = await category.create_text_channel("world-war-3")
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO server_channels (guild_id, world1_ch, world2_ch, world3_ch, notify_ch, notify_enabled) VALUES (?, ?, ?, ?, ?, 1)", (str(interaction.guild_id), str(ch1.id), str(ch2.id), str(ch3.id), str(ch1.id)))
            conn.commit()
            
        try:
            await ch1.send("This is the dedicated channel for **World #1**.\nUse `/command` to see the command list and start your conquest!")
            await ch2.send("This is the dedicated channel for **World #2**.\nUse `/command` to see the command list and start your conquest!")
            await ch3.send("This is the dedicated channel for **World #3**.\nUse `/command` to see the command list and start your conquest!")
        except:
            pass

        embed = discord.Embed(
            title="✅ Initial Setup Completed!",
            description="The battlefield is ready for World War Bot.",
            color=0x2ecc71
        )
        embed.add_field(name="📁 Created Category", value="`World War Bot`", inline=False)
        embed.add_field(name="💬 Created Channels", value=f"{ch1.mention} (World #1)\n{ch2.mention} (World #2)\n{ch3.mention} (World #3)", inline=False)
        embed.add_field(name="🔔 Notification Channel", value=f"Set to {ch1.mention}.\n(Can be changed later with `/op_reboot_setting`)", inline=False)
        embed.set_footer(text="Type commands in these channels to begin!")
        
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="op_adj", description="Toggle adjacency penalty (higher costs when attacking non-adjacent lands).")
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
        await interaction.followup.send(f"[Setting] Adjacency penalty set to **{'ON' if new_val==1 else 'OFF'}**.")

    @app_commands.command(name="op_reset_interval", description="Change automatic world wipe interval in days (0 to disable).")
    @is_slash_op_or_admin()
    async def cmd_reset_interval(self, interaction: discord.Interaction, days: int):
        await safe_defer(interaction)
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO server_channels (guild_id) VALUES (?)", (str(interaction.guild_id),))
            c.execute("UPDATE server_channels SET reset_interval=? WHERE guild_id=?", (days, str(interaction.guild_id)))
            conn.commit()
        msg = f"[Setting] Server auto-reset interval set to **every {days} days**." if days > 0 else "[Setting] Server auto-reset is now **OFF (Manual only)**."
        await interaction.followup.send(msg)

    @app_commands.command(name="op_reset", description="Immediately resets all data for the active world.")
    @is_slash_op_or_admin()
    async def cmd_reset(self, interaction: discord.Interaction):
        await safe_defer(interaction)
        world_id = await ensure_world_context(interaction)
        if world_id == 0: return
        with get_db_connection() as conn:
            c = conn.cursor()
            for table in ['players', 'territories', 'alliances', 'wars', 'un_members', 'un_invites', 'camps', 'camp_members', 'camp_invites', 'peace_treaties', 'unlocked_trophies']:
                c.execute(f"DELETE FROM {table} WHERE guild_id=? AND world_id=?", (str(interaction.guild_id), world_id))
            conn.commit()
        await interaction.followup.send(f"[Done] Manually reset all data in [World #{world_id}]. A new history begins.")

    @app_commands.command(name="op_op_setting", description="Grant or revoke OP permissions for a member.")
    @app_commands.choices(mode=[app_commands.Choice(name="Grant (ON)", value=1), app_commands.Choice(name="Revoke (OFF)", value=0)])
    @is_slash_op_or_admin()
    async def cmd_op_setting(self, interaction: discord.Interaction, target: discord.Member, mode: app_commands.Choice[int]):
        await safe_defer(interaction)
        add_op = (mode.value == 1)
        with get_db_connection() as conn:
            c = conn.cursor()
            if add_op:
                c.execute("INSERT OR IGNORE INTO server_ops (guild_id, user_id) VALUES (?, ?)", (str(interaction.guild_id), str(target.id)))
                msg = f"[Success] Granted OP permissions to {target.mention}."
            else:
                c.execute("DELETE FROM server_ops WHERE guild_id=? AND user_id=?", (str(interaction.guild_id), str(target.id)))
                msg = f"[Success] Revoked OP permissions from {target.mention}."
            conn.commit()
        await interaction.followup.send(msg)

    @app_commands.command(name="op_oil_setting", description="Toggle oil consumption system.")
    @app_commands.choices(world=[app_commands.Choice(name="All Worlds (0)", value=0), app_commands.Choice(name="World #1", value=1), app_commands.Choice(name="World #2", value=2), app_commands.Choice(name="World #3", value=3)])
    @app_commands.choices(mode=[app_commands.Choice(name="Enable (ON)", value=1), app_commands.Choice(name="Disable (OFF)", value=0)])
    @is_slash_op_or_admin()
    async def cmd_oil_setting(self, interaction: discord.Interaction, world: app_commands.Choice[int], mode: app_commands.Choice[int]):
        await safe_defer(interaction)
        guild_id = str(interaction.guild_id)
        w_val, m_val = world.value, mode.value
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO server_channels (guild_id) VALUES (?)", (guild_id,))
            if w_val == 0:
                c.execute("UPDATE server_channels SET oil_enabled_w1=?, oil_enabled_w2=?, oil_enabled_w3=? WHERE guild_id=?", (m_val, m_val, m_val, guild_id))
            else:
                col = f"oil_enabled_w{w_val}"
                c.execute(f"UPDATE server_channels SET {col}=? WHERE guild_id=?", (m_val, guild_id))
            conn.commit()
        target_name = "All Worlds" if w_val == 0 else f"World #{w_val}"
        await interaction.followup.send(f"[Setting] Oil system for {target_name} set to **{'ON' if m_val==1 else 'OFF'}**.")

    @app_commands.command(name="op_channel_setting", description="Manually link a channel to a specific World.")
    @app_commands.choices(world=[app_commands.Choice(name="World #1", value=1), app_commands.Choice(name="World #2", value=2), app_commands.Choice(name="World #3", value=3)])
    @is_slash_op_or_admin()
    async def cmd_channel_setting(self, interaction: discord.Interaction, world: app_commands.Choice[int], channel: discord.TextChannel):
        await safe_defer(interaction)
        guild_id = str(interaction.guild_id)
        col = f"world{world.value}_ch"
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO server_channels (guild_id) VALUES (?)", (guild_id,))
            c.execute(f"UPDATE server_channels SET {col}=? WHERE guild_id=?", (str(channel.id), guild_id))
            conn.commit()
        await interaction.followup.send(f"[Setting] Linked {channel.mention} to **World #{world.value}**.")

    @app_commands.command(name="op_reboot_setting", description="Configure notification channel and daily payout / wipe notices.")
    @app_commands.choices(mode=[app_commands.Choice(name="Enable (ON)", value=1), app_commands.Choice(name="Disable (OFF)", value=0)])
    @is_slash_op_or_admin()
    async def cmd_reboot_setting(self, interaction: discord.Interaction, mode: app_commands.Choice[int], channel: discord.TextChannel = None):
        await safe_defer(interaction)
        guild_id = str(interaction.guild_id)
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO server_channels (guild_id) VALUES (?)", (guild_id,))
            if channel:
                c.execute("UPDATE server_channels SET notify_ch=?, notify_enabled=? WHERE guild_id=?", (str(channel.id), mode.value, guild_id))
            else:
                c.execute("UPDATE server_channels SET notify_enabled=? WHERE guild_id=?", (mode.value, guild_id))
            conn.commit()
        status_str = "ON" if mode.value == 1 else "OFF"
        ch_str = f" to {channel.mention}" if channel else ""
        await interaction.followup.send(f"[Setting] Daily notification set to **{status_str}**{ch_str}.")

async def setup(bot):
    await bot.add_cog(AdminCog(bot))
