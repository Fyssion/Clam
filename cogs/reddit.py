from discord.ext import commands
import discord
import praw
from datetime import datetime as d

reddit_color = 0xFF4301
hyperlink_color = 0x3366BB  # Not used
warning_color = 0xFFCC4D


class Reddit(commands.Cog, name="<:RedditLogo:650197892065263626> Reddit"):
    """
    Get information about subreddits and redditors.
    """

    def __init__(self, bot):
        self.bot = bot
        self.log = self.bot.log

        # Reddit PRAW login
        try:
            self.redd = praw.Reddit(
                client_id=self.bot.reddit_id,
                client_secret=self.bot.reddit_secret,
                user_agent="my user agent",
            )
        except:
            self.bot.l.critical(
                "Failed to log into Reddit! Check if client id and secret are correct."
            )
            import sys

            sys.exit()

        if self.redd.read_only == True:
            self.log.info("Logged into Reddit")
        else:
            self.log.critical("Not logged into Reddit!")
            import sys

            sys.exit()

    @commands.command(
        name="subreddit",
        description="Search for a subreddit",
        aliases=["sub"],
        usage="[subreddit]",
    )
    async def subreddit_command(self, ctx, *args):

        if len(args) < 1:

            self.log.info(f"{str(ctx.author)} used the subreddit command improperly!")
            await ctx.send(
                f"Improper usage!\nProper usage: `robo.subreddit [subreddit]`"
            )

        else:

            sub = " ".join(args)

            em = discord.Embed(
                title=f":mag_right: Searching for `{sub}`...",
                color=reddit_color,
                timestamp=d.utcnow(),
            )
            em.set_footer(
                text=f"Requested by {ctx.author.name}#{ctx.author.discriminator}",
                icon_url=self.bot.user.avatar_url,
            )
            try:
                embed_msg = await ctx.channel.send(embed=em)
            except discord.errors.Forbidden:
                self.log.error(
                    "Bot does not have permission to send messages in channel: '"
                    + str(ctx.channel)
                    + "'"
                )

            self.log.info(str(ctx.author) + " tried to link to '" + sub + "'")

            # My solution for people linking 'wosh' (or any other varient of 'woooosh')
            if (
                sub == "whosh"
                or sub == "wosh"
                or sub == "whoosh"
                or sub == "whooosh"
                or sub == "woosh"
                or sub == "wooosh"
                or "oooo" in sub
                or "wosh" in sub
                or "whosh" in sub
                and sub != "woooosh"
            ):
                if sub != "woooosh":
                    self.wosh = (
                        "\nLooking for [r/woooosh](https://reddit.com/r/woooosh)?"
                    )
                else:
                    self.wosh = ""
            else:
                self.wosh = ""

            # Searching for subreddit to see if it exists
            self.subreddit_search = self.redd.subreddits.search_by_name(
                sub, include_nsfw=True, exact=False
            )

            self.log.debug(str(self.subreddit_search))

            if sub in self.subreddit_search:

                self.subreddit = self.redd.subreddit(sub)

                if self.subreddit.over18 == True:
                    self.isnsfw = "\n:warning:Subreddit is NSFW!:warning:"
                else:
                    self.isnsfw = ""

                em = discord.Embed(
                    title=self.subreddit.title,
                    description=f"[r/{self.subreddit.display_name}](https://reddit.com/r/{self.subreddit.display_name})\n"
                    + self.subreddit.public_description
                    + self.isnsfw
                    + self.wosh,
                    url=f"https://reddit.com/r/{self.subreddit.display_name}",
                    color=reddit_color,
                    timestamp=d.utcnow(),
                )

                em.add_field(name="Subscribers:", value=str(self.subreddit.subscribers))

                # The next if/else statements are a bug patch. Sometimes, subreddit.icon_img returns None instead of a blank string.
                # Disocrd will not accept this as a url, so I change None to a blank string
                if self.subreddit.icon_img == None:
                    self.ico_img = ""
                else:
                    self.ico_img = self.subreddit.icon_img

                em.set_thumbnail(url=self.ico_img)
                em.set_footer(
                    text=f"Requested by {ctx.author.name}#{ctx.author.discriminator}",
                    icon_url=self.bot.user.avatar_url,
                )

                try:
                    await embed_msg.edit(embed=em)
                except discord.errors.Forbidden:
                    self.log.error(
                        f"Bot does not have permission to send messages in channel: '{str(ctx.channel)}'"
                    )

            # If the subreddit is not found in any searches
            else:

                em = discord.Embed(
                    title=":warning:Subreddit not found!",
                    description=f"'{self.sub}'' is not a subreddit."
                    + self.isnsfw
                    + self.wosh,
                    color=warning_color,
                )

                em.set_footer(
                    text=f"Requested by {ctx.author.name}#{ctx.author.discriminator}",
                    icon_url=self.bot.user.avatar_url,
                )

                self.log.warning("Subreddit '" + sub + "' does not exist!")

                try:
                    await embed_msg.edit(embed=em)
                except:
                    self.log.error(
                        "Bot does not have permission to send messages in channel: '"
                        + str(ctx.channel)
                        + "'"
                    )

    @commands.command(
        name="redditor",
        description="Search for a Redditor",
        aliases=["redditer"],
        usage="[user]",
    )
    async def redditor_command(self, ctx, *args):
        if len(args) < 1:

            self.log.info(f"{str(ctx.author)} used the redditor command improperly!")
            await ctx.send(f"Improper usage!\nProper usage: `robo.redditor [user]`")

        else:

            self.usr = " ".join(args)

            em = discord.Embed(
                title=f":mag_right: Searching for `{self.usr}`...",
                color=reddit_color,
                timestamp=d.utcnow(),
            )
            em.set_footer(
                text=f"Requested by {ctx.author.name}#{ctx.author.discriminator}",
                icon_url=self.bot.user.avatar_url,
            )
            try:
                embed_msg = await ctx.channel.send(embed=em)
            except discord.errors.Forbidden:
                self.log.error(
                    "Bot does not have permission to send messages in channel: '"
                    + str(ctx.channel)
                    + "'"
                )

            self.isusr = True
            try:
                self.user = self.redd.redditor(self.usr)
                self.tkarma = (
                    self.user.comment_karma + self.user.link_karma
                )  # For some reason this generates an error
            except:
                self.isusr = False

            self.log.info(str(ctx.author) + " tried to link to '" + self.usr + "'")

            if self.isusr == False:

                em = discord.Embed(
                    title=":warning:Redditor not found!",
                    description=f"'{self.usr}'' is not a redditor.",
                    color=warning_color,
                )
                em.set_footer(
                    text=f"Requested by {ctx.author.name}#{ctx.author.discriminator}",
                    icon_url=self.bot.user.avatar_url,
                )

                self.log.warning("Redditor '" + self.usr + "' does not exist!")

                try:
                    await embed_msg.edit(embed=em)
                except:
                    self.log.error(
                        "Bot does not have permission to send messages in channel: '"
                        + str(ctx.channel)
                        + "'"
                    )

            if self.isusr == True:
                self.user = self.redd.redditor(self.usr)

                if self.user.is_employee == True:
                    self.emp = " <:employee:634152137445867531>\nThis user is a Reddit employee."
                else:
                    self.emp = ""

                self.tkarma = self.user.comment_karma + self.user.link_karma
                em = discord.Embed(
                    title=f"{self.user.name}",
                    description=f"[u/{self.user.name}](https://reddit.com/u/{self.user.name})"
                    + self.emp,
                    url=f"https://reddit.com/u/{self.user.name}",
                    color=reddit_color,
                    timestamp=d.utcnow(),
                )
                em.add_field(name="Karma:", value=str(self.tkarma))
                em.set_thumbnail(url=self.user.icon_img)
                em.set_footer(
                    text=f"Requested by {ctx.author.name}#{ctx.author.discriminator}",
                    icon_url=self.bot.user.avatar_url,
                )

                try:
                    await embed_msg.edit(embed=em)
                except discord.errors.Forbidden:
                    self.log.error(
                        "Bot does not have permission to send messages in channel: '"
                        + str(ctx.channel)
                        + "'"
                    )


def setup(bot):
    bot.add_cog(Reddit(bot))
