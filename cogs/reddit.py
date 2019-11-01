from discord.ext import commands
import discord
import praw

isnsfw = ""
wosh = ""
reddit_color = 0xFF4301
hyperlink_color = 0x3366BB # Not used
warning_color = 0xFFCC4D


class Reddit(commands.Cog):
    
    def __init__(self, bot):
        self.bot = bot

        # Reddit PRAW login
        try:
            self.redd = praw.Reddit(client_id = self.bot.reddit_id,
                                client_secret = self.bot.reddit_secret,
                                user_agent = 'my user agent')
        except:
            self.bot.l.critical("Failed to log into Reddit! Check if client id and secret are correct.")
            import sys
            sys.exit()

        if self.redd.read_only == True:
            self.bot.l.info("Logged into Reddit")
        else:
            self.bot.l.critical("Not logged into Reddit!")
            import sys
            sys.exit()

        self.issub = False
        self.isnsfw = ""
        self.wosh = ""

        

    @commands.command(
        name = "subreddit",
        description = "Search for a subreddit.",
        aliases = ['sub'],
        usage = "[subreddit]"
    )
    async def subreddit_command(self, ctx, *, sub):
        self.bot.l.info(str(ctx.author) + " tried to link to '" + sub + "'")

        self.wosh = ""

        # My solution for people linking 'wosh' (or any other varient of 'woooosh')
        if sub == "whosh" or sub == "wosh" or sub == "whoosh" or sub == "whooosh" or sub == "woosh" or sub == "wooosh"  or "oooo" in sub or "wosh" in sub or "whosh" in sub and sub != "woooosh":
            if sub != "woooosh":
                self.wosh = "\nLooking for [r/woooosh](https://reddit.com/r/woooosh)?"

        # Searching for subreddit to see if it exists
        self.subreddit_search = self.redd.subreddits.search_by_name(sub, include_nsfw=True, exact=False)
        self.bot.l.debug(str(self.subreddit_search))
        if sub in self.subreddit_search:
            self.isnsfw = "\n:warning:Subreddit is NSFW!:warning:"
            self.issub = True


        # If the subreddit is not found in any searches
        if self.issub == False:
            em_title = ":warning:Subreddit not found!"
            em_disc = "r/" + sub + " is not a subreddit." + self.isnsfw + self.wosh
            em = discord.Embed(title = em_title, description = em_disc, color=warning_color)
            em.set_footer(text = f"Requested by {ctx.author.name}#{ctx.author.discriminator}")
            self.bot.l.warning("Subreddit '" + sub + "' does not exist!")
            
            try:
                await ctx.channel.send(embed=em)
            except:
                self.bot.l.error("Bot does not have permission to send messages in channel: '" + str(ctx.channel) + "'")
            
            self.issub = False
            self.isnsfw = ""

        # Fetches subreddit's name and display name only if subreddit was found in search
        elif self.issub == True:
            self.subreddit = self.redd.subreddit(sub)

            if self.subreddit.over18 == True:
                self.isnsfw = "\n:warning:Subreddit is NSFW!:warning:"
            else:
                self.isnsfw = ""
            
            em_sub_title = "[r/" + self.subreddit.display_name + "](https://reddit.com/r/" + self.subreddit.display_name + ")\n" + self.subreddit.public_description + self.isnsfw + self.wosh
            em_title = self.subreddit.title
            em_url = "https://reddit.com/r/" + self.subreddit.display_name
            em = discord.Embed(title = em_title, description=em_sub_title, url = em_url, color=reddit_color)
            em.add_field(name = "Subscribers:", value = str(self.subreddit.subscribers))

            # The next if/else statements are a bug patch. Sometimes, subreddit.icon_img returns None instead of a blank string.
            # Disocrd will not accept this as a url, so I change None to a blank string
            if self.subreddit.icon_img  == None:
                ico_img = ""
            else:
                ico_img = self.subreddit.icon_img
            
            em.set_thumbnail(url = ico_img)
            em.set_footer(text = f"Requested by {ctx.author.name}#{ctx.author.discriminator}")

            try:
                await ctx.channel.send(embed=em)
            except discord.errors.Forbidden:
                self.bot.l.error("Bot does not have permission to send messages in channel: '" + str(ctx.channel) + "'")
            
            self.issub = False
            self.isnsfw = ""



def setup(bot):
    bot.add_cog(Reddit(bot))
