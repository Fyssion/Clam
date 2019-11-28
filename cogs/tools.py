from discord.ext import commands
import discord
from datetime import datetime as d


def snowstamp(snowflake):
    timestamp = (int(snowflake) >> 22) + 1420070400000
    timestamp /= 1000

    return d.utcfromtimestamp(timestamp).strftime('%b %d, %Y at %#I:%M %p')


class Tools(commands.Cog):
    
    def __init__(self, bot):
        self.bot = bot
        self.log = self.bot.log


    @commands.command(
        name = "userinfo",
        description = "Get information about a user",
        aliases = ["memberinfo"],
        usage = "[user]"
    )
    async def userinfo_command(self, ctx, *, user = None):

        if user and len(ctx.message.mentions) == 0:
            user = ctx.guild.get_member_named(user)
            if not user:
                self.log.info(f"{str(ctx.author)} unsuccessfully used the userinfo command on a nonexistent user")
                return await ctx.send(":warning: Member not found! Search for members with their username or nickname.")
        elif user:
            user = ctx.message.mentions[0]

        user = user or ctx.author

        if user == ctx.author:
            self.log.info(f"{str(ctx.author)} successfully used the userinfo command on themself")
        else:
            self.log.info(f"{str(ctx.author)} successfully used the userinfo command on '{user}'")

        member = ctx.guild.get_member(user.id)

        desc = ""
        if user == self.bot.user:
            desc += "\n:wave:Hey, that's me!"
        if user.bot == True:
            desc += "\n:robot: This user is a bot."
        if user.id == ctx.guild.owner_id:
            desc += "\n<:owner:649355683598303260> This user is the server owner."
        if user.id == self.bot.owner_id:
            desc += "\n:gear: This user owns this bot."
        if member.premium_since:
            desc += f"\n<:boost:649644112034922516> This user has been boosting this server since {member.premium_since.strftime('%b %d, %Y at %#I:%M %p')}."

        author = str(user)
        if member.nick:
            author += f" ({member.nick})"
        author += f" - {str(user.id)}"

        self.em = discord.Embed(
            description = desc,
            timestamp = d.utcnow()
        )
        if user.color.value:
            self.em.color = user.color

        self.em.set_thumbnail(
            url = user.avatar_url
            )
        self.em.set_author(
            name = author,
            icon_url = user.avatar_url
            )
        self.em.set_footer(
                    text = f"Requested by {ctx.author.name}#{ctx.author.discriminator}",
                    icon_url = self.bot.user.avatar_url
                    )
        self.em.add_field(
            name = "Account Created",
            value = snowstamp(user.id),
            inline = True
        )
        self.em.add_field(
            name = "Joined Server",
            value = member.joined_at.strftime('%b %d, %Y at %#I:%M %p'),
            inline = True
        )
        roles = ""
        for role in member.roles[1:]:
            roles += f"{role.mention} "
        self.em.add_field(
            name = "Roles",
            value = roles,
            inline = False
        )
        await ctx.send(embed = self.em)


    @commands.command(
        name = "serverinfo",
        description = "Get information about the current server.",
        aliases = ["guildinfo"]
    )
    async def serverinfo_command(self, ctx):
        self.log.info(f"{str(ctx.author)} used the serverinfo command")

        desc = ""
        if ctx.guild.description:
            desc += f"\n{ctx.guild.description}\n"
        if ctx.guild.large == True:
            desc += "\n:information_source: This guild is considered large (over 250 members)."
    

        self.em = discord.Embed(
            description = desc,
            timestamp = d.utcnow()
        )

        self.em.set_thumbnail(
            url = ctx.guild.icon_url
            )
        if ctx.guild.banner_url:
            self.em.set_image(
                url = ctx.guild.banner_url
            )
        self.em.set_author(
            name = f"{ctx.guild.name} ({ctx.guild.id})",
            icon_url = ctx.guild.icon_url
            )
        self.em.set_footer(
                    text = f"Requested by {ctx.author.name}#{ctx.author.discriminator}",
                    icon_url = self.bot.user.avatar_url
                    )
        self.em.add_field(
            name = "<:owner:649355683598303260> Owner",
            value = ctx.guild.owner.mention,
            inline = True
        )
        self.em.add_field(
            name = ":clock1: Server Created",
            value = snowstamp(ctx.guild.id),
            inline = True
        )
        self.em.add_field(
            name = "<:boost:649644112034922516> Nitro Boosts",
            value = f"Tier {ctx.guild.premium_tier} with {ctx.guild.premium_subscription_count} boosts.",
            inline = False
        )
        # roles = ""
        # for role in member.roles[1:]:
        #     roles += f"{role.mention} "
        # self.em.add_field(
        #     name = "Roles",
        #     value = roles,
        #     inline = False
        # )
        await ctx.send(embed = self.em)

    @commands.command(
        name = "snowstamp",
        description = "Get timestamp from a Discord snowflake.",
        usage = "[snowflake]",
        hidden = True
    )
    async def snowstamp_command(self, ctx, snowflake = None):
        if snowflake == None:
            return await ctx.send("Please specify a snowflake to convert.")
        
        await ctx.send(snowstamp(snowflake))

    @commands.command(
        name = "embed",
        description = "Create a custom embed and send it to a specified channel.",
        aliases = ['em'],
        hidden = True
    )
    async def embed_command(self, ctx):

        def check(ms):
            # Look for the message sent in the same channel where the command was used
            # As well as by the user who used the command.
            return ms.channel == ctx.author.dm_channel and ms.author == ctx.author

        if (ctx.channel).__class__.__name__ == "DMChannel":
            await ctx.send("Please use this command in a server.")
            return

        await ctx.send("Check your DMs!", delete_after = 5)
        await ctx.author.send("**Create an embed:**\nWhat server would you like to send the embed to? Type `here` to send the embed where you called the command.")

        self.msg = await self.bot.wait_for("message", check = check)

        if self.msg == 'here':
            self.em_guild = ctx.guild
        else:
            await ctx.author.send("Custom servers not supported yet :(\nServer set to where you called the command.")
            self.em_guild = ctx.guild

        # Check to see if bot has permission to view perms

        await ctx.author.send(f"Server set to `{self.em_guild.name}`.\nWhat channel would you like to send to?")

        self.msg = await self.bot.wait_for("message", check = check)

        # Check for permission here

        # while hasPermissionToSend == False:

    @commands.command(
        name = "eval",
        description = "Evaluates code",
        hidden = True
    )
    async def eval_command(self, ctx, *, body: str):

        env = {
            'bot': self.bot,
            'ctx': ctx,
            'channel': ctx.channel,
            'author': ctx.author,
            'guild': ctx.guild,
            'message': ctx.message,
            '_': self._last_result
        }

        env.update(globals())

        body = self.cleanup_code(body)
        stdout = io.StringIO()

        to_compile = f'async def func():\n{textwrap.indent(body, "  ")}'

        try:
            exec(to_compile, env)
        except Exception as e:
            return await ctx.send(f'```py\n{e.__class__.__name__}: {e}\n```')

        func = env['func']
        try:
            with redirect_stdout(stdout):
                ret = await func()
        except Exception as e:
            value = stdout.getvalue()
            await ctx.send(f'```py\n{value}{traceback.format_exc()}\n```')
        else:
            value = stdout.getvalue()
            try:
                await ctx.message.add_reaction('\u2705')
            except:
                pass

            if ret is None:
                if value:
                    await ctx.send(f'```py\n{value}\n```')
            else:
                self._last_result = ret
                await ctx.send(f'```py\n{value}{ret}\n```')


def setup(bot):
    bot.add_cog(Tools(bot))
