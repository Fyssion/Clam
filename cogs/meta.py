import asyncio
import functools
import json
import inspect
import math
import os.path
import sys
import traceback
from inspect import isawaitable
from string import Formatter

import discord
from discord.ext import commands, menus

from jishaku.models import copy_context_with

from .utils import colors
from .utils.checks import has_manage_guild
from .utils.emojis import OK_SIGN
from .utils.errors import Blacklisted, PrivateCog
from .utils.formats import plural
from .utils.menus import MenuPages
from .utils.utils import get_lines_of_code


def strfdelta(tdelta, fmt):
    """
    Similar to strftime from datetime.datetime
    Returns formatted string of a timedelta
    """
    f = Formatter()
    d = {}
    lang = {"D": 86400, "H": 3600, "M": 60, "S": 1}
    k = map(lambda x: x[1], list(f.parse(fmt)))
    rem = int(tdelta.total_seconds())

    for i in ("D", "H", "M", "S"):
        if i in k and i in lang.keys():
            d[i], rem = divmod(rem, lang[i])

    return f.format(fmt, **d)


class HelpPages(menus.ListPageSource):
    def __init__(self, data, embed, more_info):
        self.embed_base = embed
        self.original_description = embed.description
        self.more_info = more_info
        super().__init__(data, per_page=10)

    async def format_page(self, menu, entries):
        offset = menu.current_page * self.per_page
        em = self.embed_base
        em.description = self.original_description
        max_pages = self.get_max_pages()
        page_count = f"Page {menu.current_page + 1}/{max_pages}"
        em.set_author(name=page_count)
        em.set_footer(
            text=f"{page_count} \N{BULLET} Note that you can only view commands that you can use"
        )

        commands = [c for i, c in enumerate(entries, start=offset)]
        formatted = "\n".join(commands)

        if max_pages > 1 and menu.current_page + 1 != max_pages:
            more_pages = "\n*More commands on the next page -->*"

        else:
            more_pages = ""

        if formatted:
            em.description += f"\n{formatted}{more_pages}\n\n{self.more_info}"
        else:
            em.description += f"\n\n{self.more_info}"
        return em


class ClamHelpCommand(commands.HelpCommand):
    def i_category(self, ctx):
        return (
            "For more info on a specific category, "
            f"use: `{self.context.bot.guild_prefix(self.context.guild)}help <category>`‍"
        )

    def i_cmd(self, ctx):
        return (
            "For more info on a specific command, "
            f"use: `{self.context.bot.guild_prefix(self.context.guild)}help <command>`‍"
        )

    def get_command_signature(self, command):
        sig = command.signature
        name = command.qualified_name
        prefix = self.context.guild_prefix

        result = f"{prefix}{name}"

        if sig:
            result += f" {sig}"

        return result

    @property
    def arg_help(self):
        return "Key: `<required>` `[optional]`\nRemove `<>` and `[]` when using the command."

    def get_base_embed(self):
        ctx = self.context
        bot = ctx.bot
        em = discord.Embed(
            title=f"Help for {bot.user.name}",
            color=colors.PRIMARY,
        )
        em.set_thumbnail(url=ctx.bot.user.avatar_url)
        em.set_footer(text="Note that you can only view commands that you can use")
        return em

    async def send_bot_help(self, mapping):
        ctx = self.context
        bot = ctx.bot

        em = self.get_base_embed()
        em.description = (
            f"{bot.description}\n\n"
            f"**Default Prefix: `{bot.guild_prefix(ctx.guild)}`** "
            f"Example: `{bot.guild_prefix(ctx.guild)}help`\n"
            f"Use `{bot.guild_prefix(ctx.guild)}prefixes` to view all prefixes for this server.\n"
            f"{self.i_category(ctx)}\n"
        )

        if bot.debug:
            em.description = (
                f"```css\n[DEBUG mode {bot.debug} is active]\n```\n" + em.description
            )

        settings = bot.get_cog("Settings")

        async def blocked(name):
            if settings and ctx.guild:
                perms = await settings.get_cog_permissions(ctx.guild.id)

                return perms.is_cog_blocked(name, ctx.channel.id)

            else:
                return False

        def private(cog):
            if hasattr(cog, "private"):
                if ctx.guild and ctx.guild.id in (
                    454469821376102410,
                    621123303343652867,
                ):
                    return False

                elif (
                    hasattr(cog, "private_user_overrides")
                    and ctx.author.id in cog.private_user_overrides
                ):
                    return False

                elif (
                    ctx.guild
                    and hasattr(cog, "private_guild_overrides")
                    and ctx.guild.id in cog.private_guild_overrides
                ):
                    return False

                return True

            return False

        cog_names = []
        for cog_name in bot.ordered_cogs:
            cog = bot.get_cog(cog_name)
            if (
                hasattr(cog, "hidden")
                or private(cog)
                or cog.qualified_name == "Jishaku"
                or await blocked(cog.qualified_name)
            ):
                if ctx.author.id != bot.owner_id:
                    continue

            if hasattr(cog, "emoji"):
                cog_names.append(f"{cog.emoji} {cog.qualified_name}")
            else:
                cog_names.append(f":grey_question: {cog.qualified_name}")

        if len(cog_names) >= 8:
            middle = math.ceil(len(cog_names) / 2)
            first_half = cog_names[:middle]
            second_half = cog_names[middle:]

            em.add_field(name="Categories", value="\n".join(first_half), inline=True)
            em.add_field(name="\u200b", value="\n".join(second_half), inline=True)

        else:
            em.add_field(name="Categories", value="\n".join(cog_names), inline=True)

        dev = bot.get_user(224513210471022592)
        em.add_field(
            name="More Info",
            value=(f"Created by {dev}\nusing discord.py v{discord.__version__}"),
            inline=True,
        )

        await ctx.send(embed=em)

    def format_commands(self, commands):
        formatted_commands = []

        for command in commands:
            signature = self.get_command_signature(command)
            description = command.description or command.brief or command.short_doc

            formatted_command = f"**`{signature}`**"

            if description:
                formatted_command += f" - {description}"

            formatted_commands.append(formatted_command)

        return formatted_commands

    def format_aliases(self, command):
        formatted_aliases = []

        for alias in command.aliases:
            formatted_alias = f"`{self.context.guild_prefix}"
            formatted_alias += (
                f"{command.full_parent_name} " if command.parent is not None else ""
            )

            formatted_alias += alias + "`"
            formatted_aliases.append(formatted_alias)

        return f"Aliases: {', '.join(formatted_aliases)}"

    def format_command(self, command, ctx):
        signature = self.get_command_signature(command)

        formatted_command = f"**`{signature}`**"

        if command.description:
            formatted_command += f" - {command.description}"

        if command.aliases:
            formatted_command += f"\n{self.format_aliases(command)}"

        if command.help:
            formatted_command += f"\n{command.help.format(prefix=ctx.prefix)}\n"

        if not isinstance(command, commands.Group) or not command.commands:
            formatted_command += f"\n\n{self.arg_help}"

        return formatted_command

    async def send_cog_help(self, cog):
        ctx = self.context

        filtered = await self.filter_commands(cog.get_commands(), sort=True)
        commands = self.format_commands(filtered)

        em = self.get_base_embed()

        if hasattr(cog, "emoji"):
            em.description = f"**{cog.emoji} {cog.qualified_name}**"
        else:
            em.description = f"**{cog.qualified_name}**"

        cog_name = cog.__class__.__name__
        if os.path.isfile(f"assets/cogs/{cog_name}.png"):
            url = f"https://raw.githubusercontent.com/Fyssion/Clam/main/assets/cogs/{cog_name}.png"
            em.set_thumbnail(url=url)

        if cog.description:
            em.description += f"\n{cog.description.format(prefix=ctx.prefix)}\n"

        more_info = f"{self.arg_help}\n{self.i_cmd(ctx)}"

        pages = MenuPages(
            source=HelpPages(commands, em, more_info),
            clear_reactions_after=True,
        )
        await pages.start(ctx)

    async def send_group_help(self, group):
        ctx = self.context

        filtered = await self.filter_commands(group.commands, sort=True)
        commands = self.format_commands(filtered)

        em = self.get_base_embed()

        em.description = self.format_command(group, ctx)

        cog_name = group.cog.__class__.__name__
        if os.path.isfile(f"assets/cogs/{cog_name}.png"):
            url = f"https://raw.githubusercontent.com/Fyssion/Clam/main/assets/cogs/{cog_name}.png"
            em.set_thumbnail(url=url)

        if filtered:
            em.description += f"\n\n**Subcommands ({len(filtered)} total):**"

        more_info = f"{self.arg_help}\n{self.i_cmd(ctx)}"

        pages = MenuPages(
            source=HelpPages(commands, em, more_info),
            clear_reactions_after=True,
        )
        await pages.start(ctx)

    async def send_command_help(self, command):
        ctx = self.context

        em = self.get_base_embed()

        em.set_footer(text=em.Empty)

        cog_name = command.cog.__class__.__name__
        if os.path.isfile(f"assets/cogs/{cog_name}.png"):
            url = f"https://raw.githubusercontent.com/Fyssion/Clam/main/assets/cogs/{cog_name}.png"
            em.set_thumbnail(url=url)

        em.description = self.format_command(command, ctx)

        await ctx.send(embed=em)

    async def on_help_command_error(self, ctx, error):
        traceback.print_exception(
            type(error), error, error.__traceback__, file=sys.stderr
        )
        if isinstance(error, PrivateCog):
            return await ctx.send("You don't have access to that cog.")

    async def command_callback(self, ctx, *, command=None):
        # I am only overriding this because I want to add
        # case insensitivity for cogs

        await self.prepare_help_command(ctx, command)
        bot = ctx.bot

        if command is None:
            mapping = self.get_bot_mapping()
            return await self.send_bot_help(mapping)

        # Check if the query is an actual cog
        cog = bot.get_cog(command)
        if cog is not None:
            return await self.send_cog_help(cog)

        # Check if the query was a cog even if it was lowercase
        # and save it for later use
        for name in bot.cogs:
            if name.lower() == command.lower():
                cog = bot.cogs[name]
                break

        if cog and hasattr(cog, "display_over_commands"):
            return await self.send_cog_help(cog)

        maybe_coro = discord.utils.maybe_coroutine

        # At this point, the command could either be a cog
        # or a command
        keys = command.split(" ")
        cmd = bot.all_commands.get(keys[0])
        if cmd is None:
            string = await maybe_coro(
                self.command_not_found, self.remove_mentions(keys[0])
            )

            # At this point, the command was not found
            # If the cog exists, send that
            if cog is not None:
                return await self.send_cog_help(cog)

            return await self.send_error_message(string)

        for key in keys[1:]:
            try:
                found = cmd.all_commands.get(key)
            except AttributeError:
                string = await maybe_coro(
                    self.subcommand_not_found, cmd, self.remove_mentions(key)
                )
                return await self.send_error_message(string)
            else:
                if found is None:
                    string = await maybe_coro(
                        self.subcommand_not_found, cmd, self.remove_mentions(key)
                    )
                    return await self.send_error_message(string)
                cmd = found

        if isinstance(cmd, commands.Group):
            return await self.send_group_help(cmd)
        else:
            return await self.send_command_help(cmd)

        if cog is not None:
            return await self.send_cog_help(cog)


class CommandConverter(commands.Converter):
    async def convert(self, ctx, arg):
        arg = arg.lower()

        valid_commands = {c.qualified_name: c for c in ctx.bot.walk_commands()}

        if arg not in valid_commands.keys():
            raise commands.BadArgument(f"Command `{arg}` is not a valid command.")

        return valid_commands.get(arg)


class Meta(commands.Cog):
    """Everything to do with the bot itself."""

    def __init__(self, bot):
        self.bot = bot
        self.emoji = ":robot:"
        self.log = self.bot.log

        with open("prefixes.json", "r") as f:
            self.bot.guild_prefixes = json.load(f)

        self._original_help_command = bot.help_command
        bot.help_command = ClamHelpCommand()
        bot.help_command.cog = self

    def cog_unload(self):
        self.bot.help_command = self._original_help_command

    def i_category(self, ctx):
        return (
            "For more info on a specific category, "
            f"use: `{self.bot.guild_prefix(ctx.guild)}help [category]`‍"
        )

    def i_cmd(self, ctx):
        return (
            "For more info on a specific command, "
            f"use: `{self.bot.guild_prefix(ctx.guild)}help [command]`‍"
        )

    @commands.Cog.listener("on_message")
    async def on_mention_msg(self, message):
        if self.bot.debug.full:
            return

        content = message.content
        id = self.bot.user.id
        if content == f"<@{id}>" or content == f"<@!{id}>":
            dev = self.bot.get_user(224513210471022592)
            await message.channel.send(
                f"Hi there! :wave: I'm a bot made by {dev}."
                "\nTo find out more about me, type:"
                f" `{self.bot.guild_prefix(message.guild)}help`"
            )

    # @commands.Cog.listener("on_error")
    # async def _dm_dev(self, event):
    #     e = sys.exc_info()
    #     full =''.join(traceback.format_exception(type(e), e, e.__traceback__, 1))
    #     owner = self.bot.get_user(self.bot.owner_id)
    #     await owner.send(f"Error in {event}:```py\n{full}```")

    async def send_unexpected_error(self, ctx, error):
        formatted = "".join(
            traceback.format_exception(type(error), error, error.__traceback__, 1)
        )
        self.bot.error_cache.append(error)

        em = discord.Embed(
            title=":warning: Unexpected Error",
            color=discord.Color.gold(),
        )

        description = (
            "An unexpected error has occured:"
            f"```py\n{error}```\n"
            "The developer has been notified."
            "\nConfused? Join my [support server.](https://www.discord.gg/eHxvStNJb7)"
        )

        em.description = description
        em.set_footer(icon_url=self.bot.user.avatar_url)

        await ctx.send(embed=em)

        extra_info = f"Command: `{ctx.command.qualified_name}`"
        extra_info += f"\nGuild: `{ctx.guild}`"
        extra_info += f"\nChannel: `{ctx.channel}`"
        extra_info += f"\nInvoker: `{ctx.author}`"
        extra_info += f"\nOriginal message: `{discord.utils.escape_markdown(ctx.message.content)}`"

        extra_info += f"\n\nError cache position: `{len(self.bot.error_cache) - 1}`"

        if ctx.args:
            args = [str(a) for a in ctx.args]
            extra_info += f"\nArgs: `{', '.join(args)}`"

        if ctx.kwargs:
            kwargs = [str(a) for a in ctx.kwargs]
            extra_info += f"\nKwargs: `{', '.join(kwargs)}`"

        extra_info += f"\n\nAn unexpected error has occured: ```py\n{error}```\n"
        em.description = extra_info

        await ctx.console.send(embed=em)

    async def info_reaction(self, message, ctx, error):
        info_emoji = "\N{INFORMATION SOURCE}"
        await message.add_reaction(info_emoji)

        def check(pd):
            return (
                pd.message_id == message.id
                and pd.user_id == ctx.author.id
                and str(pd.emoji) == info_emoji
            )

        try:
            await self.bot.wait_for(
                "raw_reaction_add", check=check, timeout=180  # 3 min
            )
        except asyncio.TimeoutError:
            try:
                await message.remove_reaction(info_emoji, self.bot.user)
            except discord.NotFound:
                pass
            return

        try:
            await message.clear_reaction(info_emoji)

        except discord.Forbidden:
            await message.remove_reaction(info_emoji, self.bot.user)

        await ctx.send_help(ctx.command)

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        stats = self.bot.get_cog("Stats")
        if stats:
            await stats.register_command(ctx)

        if hasattr(ctx, "handled"):
            return

        ignored_errors = [PrivateCog, Blacklisted, commands.NotOwner]

        for ignored_error in ignored_errors:
            if isinstance(error, ignored_error):
                return

            message = None

        if isinstance(error, commands.NoPrivateMessage):
            message = await ctx.send(
                f"{ctx.tick(False)} This command can't be used in DMs. Sorry."
            )

        elif isinstance(error, commands.ArgumentParsingError):
            message = await ctx.send(f"{ctx.tick(False)} {error}")

        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(
                f"{ctx.tick(False)} You are on cooldown. Try again after {int(error.retry_after)} seconds."
            )

        elif isinstance(error, commands.MaxConcurrencyReached):
            await ctx.send(ctx.tick(False, str(error)))

        elif isinstance(error, commands.errors.BotMissingPermissions):
            perms = ""

            for perm in error.missing_perms:
                formatted = (
                    str(perm).replace("_", " ").replace("guild", "server").capitalize()
                )
                perms += f"\n- `{formatted}`"

            message = await ctx.send(
                f"{ctx.tick(False)} I am missing some required permission(s):{perms}"
            )

        elif isinstance(error, commands.errors.BadArgument):
            message = await ctx.send(f"{ctx.tick(False)} {error}")

        elif isinstance(error, commands.errors.MissingRequiredArgument):
            message = await ctx.send(
                f"{ctx.tick(False)} Missing a required argument: `{error.param.name}`"
            )

        elif (
            isinstance(error, commands.CommandInvokeError)
            and str(ctx.command) == "help"
        ):
            pass

        elif isinstance(error, commands.CommandInvokeError):
            original = error.original
            # if True: # for debugging
            if not isinstance(original, discord.HTTPException):
                print(
                    "Ignoring exception in command {}:".format(ctx.command),
                    file=sys.stderr,
                )
                traceback.print_exception(
                    type(error), error, error.__traceback__, file=sys.stderr
                )

                await self.send_unexpected_error(ctx, error)
                return

        if message:
            await self.bot.loop.create_task(self.info_reaction(message, ctx, error))

    def get_guild_prefixes(self, guild):
        if not guild:
            return "`c.` or when mentioned"
        guild = guild.id
        if str(guild) in self.bot.guild_prefixes.keys():
            prefixes = [f"`{p}`" for p in self.bot.guild_prefixes.get(str(guild))]
            prefixes.append("or when mentioned")
            return ", ".join(prefixes)
        return " ".join(self.bot.prefixes)

    @commands.command(aliases=["diagnose"])
    async def troubleshoot(self, ctx, *, command: CommandConverter):
        """Troubleshoot any errors with a command"""
        alt_ctx = await copy_context_with(ctx, content=f"{ctx.prefix}{command}")

        errors = []

        if not command.enabled:
            errors.append("Command is disabled by bot owner.")

        bot_checks = self.bot._checks

        if bot_checks:
            for predicate in bot_checks:
                try:
                    pred = predicate(alt_ctx)
                    if isawaitable(pred):
                        await pred
                except Exception as e:
                    errors.append(str(e))

        cog = command.cog
        if cog is not None:
            local_check = commands.Cog._get_overridden_method(cog.cog_check)
            if local_check is not None:
                try:
                    ret = await discord.utils.maybe_coroutine(local_check, alt_ctx)
                    if not ret:
                        errors.append("The category check has failed.")
                except Exception as e:
                    errors.append(str(e))

        predicates = command.checks
        if predicates:
            for predicate in predicates:
                try:
                    pred = predicate(alt_ctx)
                    if isawaitable(pred):
                        await pred
                except Exception as e:
                    errors.append(str(e))

        if not errors:
            return await ctx.send(
                ctx.tick(
                    True, "All checks passed for this command. It should run normally!"
                )
            )

        # removing duplicates
        res = []
        [res.append(e) for e in errors if e not in res]

        res[0] = ctx.tick(False, res[0])
        em = discord.Embed(
            title="Troubleshoot Results",
            description=f"\n{ctx.tick(False)} ".join(res),
            color=discord.Color.blue(),
        )

        await ctx.send(embed=em)

    @commands.command(description="Greet me!", aliases=["hello"])
    async def hi(self, ctx):
        dev = self.bot.get_user(224513210471022592)
        await ctx.send(
            f"Hi there! :wave: I'm a bot made by {dev}."
            f"\nTo find out more about me, type: `{ctx.guild_prefix}help`"
        )

    @commands.command(description="Get a link to my website", aliases=["site"])
    @commands.is_owner()
    async def website(self, ctx):
        await ctx.send("My website: https://clambot.xyz")

    @commands.group(
        description="Find out what I'm made of!", invoke_without_command=True
    )
    async def code(self, ctx):
        partial = functools.partial(get_lines_of_code)
        lines = await self.bot.loop.run_in_executor(None, partial)
        await ctx.send(lines)

    @code.command(
        name="all", description="Include comments and newlines", aliases=["comments"]
    )
    async def code_all(self, ctx):
        partial = functools.partial(get_lines_of_code, comments=True)
        lines = await self.bot.loop.run_in_executor(None, partial)
        await ctx.send(lines)

    @commands.command(name="invite", description="Invite me to your server")
    async def invite_command(self, ctx):
        permissions = discord.Permissions(
            view_audit_log=True,
            manage_roles=True,
            manage_channels=True,
            manage_nicknames=True,
            ban_members=True,
            kick_members=True,
            manage_messages=True,
            read_messages=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            read_message_history=True,
            use_external_emojis=True,
            add_reactions=True,
        )
        invite = discord.utils.oauth_url(self.bot.user.id, permissions=permissions)

        message = (
            "Hey you!\n\n"
            "This bot is currently private because the owner has chosen not to verfiy. "
            "Why? Because verifiying means giving Discord your personal ID (e.g. a passport). "
            "However, opting out of verification comes with some major drawbacks:\n"
            "If not verified, the bot cannot exceed 99 guilds or it will become unusable.\n\n"
            "Want an exception? Contact the owner in their [support server.](https://discord.gg/eHxvStNJb7)\n\n"
        )

        em = discord.Embed(description=message, color=colors.PRIMARY)

        if ctx.author.id == self.bot.owner_id:
            await ctx.send(f"Invite link: <{invite}>")

        else:
            await ctx.send(embed=em)

    @commands.command(description="Get a link to my support server")
    @commands.is_owner()
    async def support(self, ctx):
        # If the bot isn't Clam or Clam DEV,
        # send a 'friendly' message with a link
        # to my site
        if self.bot.user.id in [639234650782564362, 683129055092015107]:
            await ctx.send("Support Server Invite: https://discord.gg/eHxvStNJb7")
        else:
            return await ctx.send(
                "The person who made this bot copy/pasted my code :(\nHere's the original: <https://clambot.xyz/>"
            )

    @commands.group(
        invoke_without_command=True,
        aliases=["prefixes"],
    )
    @commands.guild_only()
    async def prefix(self, ctx):
        """Shows this server's prefixes."""
        prefixes = self.bot.prefixes.get(ctx.guild.id)
        formatted = [f"{plural(len(prefixes)):Prefix|Prefixes}:"]

        for i, prefix in enumerate(prefixes):
            formatted.append(f"{i+1}. `{prefix}`")

        await ctx.send("\n".join(formatted))

    @prefix.command(name="add")
    @commands.guild_only()
    @has_manage_guild()
    async def prefix_add(self, ctx, prefix):
        """Adds a prefix for this server."""
        prefixes = self.bot.prefixes.get(ctx.guild.id)

        if prefix in prefixes:
            return await ctx.send("That prefix is already registered.")

        await self.bot.prefixes.add(ctx.guild.id, prefix)
        await ctx.send(ctx.tick(True, f"Added prefix `{prefix}` for this server."))

    @prefix.command(name="remove")
    @commands.guild_only()
    @has_manage_guild()
    async def prefix_remove(self, ctx, prefix):
        """Removes a prefix for this server."""
        prefixes = self.bot.prefixes.get(ctx.guild.id)

        if prefix in prefixes:
            await self.bot.prefixes.remove(ctx.guild.id, prefixes.index(prefix))

        else:
            # allow the user to input the index of the prefix
            try:
                prefix_index = int(prefix) - 1
            except ValueError:
                raise commands.BadArgument("That prefix is not registered for this server.")
            else:
                if len(prefixes) <= prefix_index:
                    raise commands.BadArgument("That prefix is not registered for this server.")

                prefix = await self.bot.prefixes.remove(ctx.guild.id, prefix_index)

        await ctx.send(ctx.tick(True, f"Removed prefix `{prefix}` for this server."))

    @prefix.command(name="default")
    @commands.guild_only()
    @has_manage_guild()
    async def prefix_default(self, ctx, prefix):
        """Sets a default prefix for this server.

        The default prefix is just the first prefix.
        """
        await self.bot.prefixes.set_default(ctx.guild.id, prefix)
        await ctx.send(f"{ctx.tick(True)} Set default prefix to `{prefix}` for this server.")

    @prefix.command(name="reset", aliases=["clear"])
    @commands.guild_only()
    @has_manage_guild()
    async def prefixes_reset(self, ctx):
        """Resets the prefixes for this server to the default."""
        confirm = await ctx.confirm(
            "Are you sure you want to reset this server's prefixes?\n"
            "**This action is irreversible.**"
        )
        if not confirm:
            return await ctx.send("Aborted.")

        await self.bot.prefixes.clear(ctx.guild.id)
        await ctx.send(ctx.tick(True, "Reset this server's prefixes."))

    @commands.command()
    async def source(self, ctx, *, command: str = None):
        """Displays my full source code or for a specific command.
        To display the source code of a subcommand you can separate it
        by periods, e.g. tag.create for the create subcommand of the tag
        command or by spaces.
        """

        source_url = "https://github.com/Fyssion/Clam"
        branch = "main"
        if command is None:
            return await ctx.send(source_url)

        if command == "help":
            src = type(self.bot.help_command)
            module = src.__module__
            filename = inspect.getsourcefile(src)
        else:
            obj = self.bot.get_command(command.replace(".", " "))
            if obj is None:
                return await ctx.send(f"{ctx.tick(False)} Could not find command.")

            # since we found the command we're looking for, presumably anyway, let's
            # try to access the code itself
            src = obj.callback.__code__
            module = obj.callback.__module__
            filename = src.co_filename

        lines, firstlineno = inspect.getsourcelines(src)
        if not module.startswith("discord"):
            # not a built-in command
            location = os.path.relpath(filename).replace("\\", "/")
        else:
            location = module.replace(".", "/") + ".py"
            source_url = "https://github.com/Rapptz/discord.py"
            branch = "master"

        if "Fyssion/Clam" in source_url:
            license_notice = (
                "Please note that most of this code is subject to the [MPL-2.0 license.](https://www.mozilla.org/MPL/2.0)\n"
                "This means you are required to license any copied or modified source code under MPL-2.0.\n"
                "For move info, visit <https://www.mozilla.org/MPL/2.0/FAQ>"
            )

            license_notice_embed = discord.Embed(description=license_notice, color=0x2f3136)

        else:
            license_notice_embed = None

        final_url = f"<{source_url}/blob/{branch}/{location}#L{firstlineno}-L{firstlineno + len(lines) - 1}>"
        await ctx.send(final_url, embed=license_notice_embed)

    @commands.command(name="backup_reload")
    @commands.is_owner()
    async def _reload(self, ctx, cog="all"):
        if cog == "all":
            msg = ""

            for ext in self.bot.cogs_to_load:
                try:
                    self.bot.reload_extension(ext)
                    msg += (
                        f"**{OK_SIGN} Reloaded** `{ext}`\n\n"
                    )
                    self.log.info(f"Extension '{cog.lower()}' successfully reloaded.")

                except Exception as e:
                    traceback_data = "".join(
                        traceback.format_exception(type(e), e, e.__traceback__, 1)
                    )
                    msg += (
                        f"**:warning: Extension `{ext}` not loaded.**\n"
                        f"```py\n{traceback_data}```\n\n"
                    )
                    self.log.warning(
                        f"Extension 'cogs.{cog.lower()}' not loaded.\n"
                        f"{traceback_data}"
                    )
            return await ctx.send(msg)

        try:
            self.bot.reload_extension(cog.lower())
            await ctx.send(
                f"**{OK_SIGN} Reloaded** `{cog.lower()}`"
            )
            self.log.info(f"Extension '{cog.lower()}' successfully reloaded.")
        except Exception as e:
            traceback_data = "".join(
                traceback.format_exception(type(e), e, e.__traceback__, 1)
            )
            await ctx.send(
                f"**:warning: Extension `{cog.lower()}` not loaded.**\n```py\n{traceback_data}```"
            )
            self.log.warning(
                f"Extension 'cogs.{cog.lower()}' not loaded.\n{traceback_data}"
            )


def setup(bot):
    bot.add_cog(Meta(bot))
