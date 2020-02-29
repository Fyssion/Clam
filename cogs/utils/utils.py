import random
# from nltk.corpus import wordnet
import asyncio
import contextlib
from discord.ext import commands
import discord
from discord import Client, Embed, File, Member, Message, Reaction, TextChannel, Webhook
from discord.abc import Snowflake
from typing import Optional, Sequence, Union
import io
import zlib

class SphinxObjectFileReader:
    # Inspired by Sphinx's InventoryFileReader
    BUFSIZE = 16 * 1024

    def __init__(self, buffer):
        self.stream = io.BytesIO(buffer)

    def readline(self):
        return self.stream.readline().decode('utf-8')

    def skipline(self):
        self.stream.readline()

    def read_compressed_chunks(self):
        decompressor = zlib.decompressobj()
        while True:
            chunk = self.stream.read(self.BUFSIZE)
            if len(chunk) == 0:
                break
            yield decompressor.decompress(chunk)
        yield decompressor.flush()

    def read_compressed_lines(self):
        buf = b''
        for chunk in self.read_compressed_chunks():
            buf += chunk
            pos = buf.find(b'\n')
            while pos != -1:
                yield buf[:pos].decode('utf-8')
                buf = buf[pos + 1:]
                pos = buf.find(b'\n')



# async def start():
#     wordnet.synsets("test")
    

# async def thesaurize(msg):
#     isInput = False
#     IncorrectMsg = ""

#     args = msg.split(" ")

#     if len(args) < 2:
#         minReplace = 0
#     else:
#         minReplace = 1

#     # Replace random # of items in the list with random item from GList

#     newMsg = args
#     toBeReplaced = []
#     for i in range(random.randrange(minReplace, len(args))):

#         isVaild = False
#         while isVaild == False:

            
#             num = random.randrange(0, len(args))
            
            
#             if num in toBeReplaced:
#                 pass
#             elif len(args[num]) < 4:
#                 pass
#             else:
#                 toBeReplaced.append(num)
#                 isVaild = True
#                 newWord = (wordnet.synsets(args[num]))#[0].lemmas()[0].name()
#                 if len(newWord) <= 0:
#                     pass
#                 else:
#                     newWord = newWord[0].lemmas()[0].name()

#                     newMsg[num] = newWord

#                 break


#     return " ".join(args)

async def wait_for_deletion(
    message: Message,
    user_ids: Sequence[Snowflake],
    deletion_emoji: str = '❌',
    timeout: int = 60,
    attach_emojis: bool = True,
    client: Optional[Client] = None
) -> None:
    """
    Wait for up to `timeout` seconds for a reaction by any of the specified `user_ids` to delete the message.
    An `attach_emojis` bool may be specified to determine whether to attach the given
    `deletion_emojis` to the message in the given `context`
    A `client` instance may be optionally specified, otherwise client will be taken from the
    guild of the message.
    """
    if message.guild is None and client is None:
        raise ValueError("Message must be sent on a guild")

    bot = client or message.guild.me

    if attach_emojis:

        await message.add_reaction(deletion_emoji)

    def check(reaction: Reaction, user: Member) -> bool:
        """Check that the deletion emoji is reacted by the approprite user."""
        return (
            reaction.message.id == message.id
            and reaction.emoji == deletion_emoji
            and user.id in user_ids
        )

    # with contextlib.suppress(asyncio.TimeoutError):
    #     await bot.wait_for('reaction_add', check=check, timeout=timeout)
        # for emoji in deletion_emojis:
        #     await message.add_reaction(emoji)
        # await message.delete()
    try:
        await bot.wait_for('reaction_add', check=check, timeout=timeout)
        await message.delete()
    except asyncio.TimeoutError:

        await message.remove_reaction(deletion_emoji, discord.Object(bot.user.id))


def hover_link(ctx, msg, text="`?`"):
    return (f"[{text}](https://www.discordapp.com/"
            f"channels/{ctx.guild.id}/{ctx.channel.id} "
            f""""{msg}")""")
