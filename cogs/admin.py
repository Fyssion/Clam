import discord
from discord.ext import commands

from datetime import datetime as d
import traceback
import json
import psutil


class Admin(commands.Cog):
    """Admin commands and features"""

    def __init__(self, bot):
        self.bot = bot
        self.log = self.bot.log

        with open("active_dms.json", "r") as f:
            self.active_dms = json.load(f)

    @commands.command(
        name="reload",
        description="Reload an extension",
        aliases=['load'],
        usage="[cog]",
        hidden=True
    )
    @commands.is_owner()
    async def _reload(self, ctx, cog="all"):
        if cog == "all":
            msg = ""

            for ext in self.bot.cogs_to_load:
                try:
                    self.bot.reload_extension(ext)
                    msg += f"**cool_ok_sign:699837382433701998: Reloaded** `{ext}`\n\n"
                    self.log.info(f"Extension '{cog.lower()}' successfully reloaded.")

                except Exception as e:
                    traceback_data = ''.join(traceback.format_exception(type(e), e, e.__traceback__, 1))
                    msg += (f"**:warning: Extension `{ext}` not loaded.**\n"
                            f"```py\n{traceback_data}```\n\n")
                    self.log.warning(f"Extension 'cogs.{cog.lower()}' not loaded.\n"
                                     f"{traceback_data}")
            return await ctx.send(msg)

        try:
            self.bot.reload_extension(cog.lower())
            await ctx.send(f"**cool_ok_sign:699837382433701998: Reloaded** `{cog.lower()}`")
            self.log.info(f"Extension '{cog.lower()}' successfully reloaded.")
        except Exception as e:
            traceback_data = ''.join(traceback.format_exception(type(e), e, e.__traceback__, 1))
            await ctx.send(f"**:warning: Extension `{cog.lower()}` not loaded.**\n```py\n{traceback_data}```")
            self.log.warning(f"Extension 'cogs.{cog.lower()}' not loaded.\n{traceback_data}")

    @commands.group(name="cog")
    @commands.is_owner()
    async def _cog(self, ctx):
        pass

    @_cog.command(name="reload")
    @commands.is_owner()
    async def _add_cog(self, ctx, cog):
        self.bot.add_cog(cog)
        self.bot.cogs_to_load.append(cog)
        self.bot.ordered_cogs.append(self.bot.cogs.keys()[-1])
        return await ctx.send("Cog added.")

    def readable(self, value):
        gigs = round(value // 1000000000)
        if gigs <= 0:
            megs = round(value // 1000000)
            return f"{megs}mb"
        return f"{gigs}gb"

    @commands.group(name="process", hidden=True, aliases=["computer", "comp", "cpu", "ram"])
    @commands.is_owner()
    async def _process(self, ctx):
        em = discord.Embed(title="Current Process Stats", color=discord.Color.teal(),
                           timestamp=d.utcnow())
        em.add_field(name="CPU", value=f"{psutil.cpu_percent()}% used with {psutil.cpu_count()} CPU(s)")
        mem = psutil.virtual_memory()
        em.add_field(
            name="Virtual Memory",
            value=f"{mem.percent}% used\n{self.readable(mem.used)}/{self.readable(mem.total)}"
        )
        disk = psutil.disk_usage('/')
        em.add_field(
            name="Disk",
            value=f"{disk.percent}% used\n{self.readable(disk.used)}/{self.readable(disk.total)}"
        )

        await ctx.send(embed=em)

    @commands.group(name="error", hidden=True, aliases=["e"])
    @commands.is_owner()
    async def _error(self, ctx):
        pass

    @_error.command()
    async def previous(self, ctx):
        if not self.bot.previous_error:
            return await ctx.send("No previous error cached.")
        e = self.bot.previous_error
        error = ''.join(traceback.format_exception(type(e), e, e.__traceback__, 1))
        await ctx.send(f"```py\n{error}```")

    @commands.command(
        name="logout",
        description="Logs out and shuts down bot",
        hidden=True
    )
    @commands.is_owner()
    async def logout_command(self, ctx):
        self.log.info("Logging out of Discord.")
        await ctx.send("Logging out :wave:")
        await self.bot.session.close()
        await self.bot.logout()

    @commands.group(description="DMs with the bot", aliases=["dms"],
                    invoke_without_command=True)
    @commands.is_owner()
    async def dm(self, ctx):
        await self.all_dms(ctx)

    @dm.command(name="all", description="View all current DMs.")
    @commands.is_owner()
    async def all_dms(self, ctx):
        if not self.active_dms:
            return await ctx.send("No active DMs.")
        dms = "Current active DMs"
        for dm in self.active_dms:
            pass
        await ctx.send(dms)

    @dm.command(description="Create a new DM with a user.", aliases=["new"])
    @commands.is_owner()
    async def create(self, ctx, member: discord.Member):
        pass

    @dm.command(description="Remove a DM with a user.", aliases=["delete", "stop"])
    @commands.is_owner()
    async def remove(self, ctx, member: discord.Member = None):
        pass

    @dm.command(description="Toggle broadcasting DMs")
    @commands.is_owner()
    async def broadcast(self, ctx, state: bool):
        pass

    @commands.Cog.listener("on_message")
    async def dm_listener(self, message):
        if isinstance(message.channel, discord.DMChannel) and not message.author.bot:
            channel = self.bot.get_channel(679841169248747696)
            em = discord.Embed(description=message.clean_content,
                               color=discord.Color.blue(), timestamp=d.utcnow())
            em.set_author(name=message.author, icon_url=message.author.avatar_url)
            em.set_footer(text="Incoming DM")
            return await channel.send(embed=em)
        pass


def setup(bot):
    bot.add_cog(Admin(bot))
