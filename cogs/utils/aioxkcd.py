import aiohttp
import json
from datetime import datetime
from random import randint


class XkcdError(Exception):
    pass


class Comic:

    XKCD_URL = "https://www.xkcd.com/"
    IMAGE_URL = "https://imgs.xkcd.com/comics/"

    def __init__(self, data, number, link):
        self.number = number
        self.link = link
        self._unparsed_data = data
        self.data = json.loads(self._unparsed_data.decode())
        self.title = self.data['safe_title']
        self.alt_text = self.data['alt']
        self.image_url = self.data['img']
        self.year = int(self.data["year"])
        self.month = int(self.data["month"])
        self.day = int(self.data["day"])
        self.publish_date = datetime(self.year, self.month, self.day)
        self.date_str = self.publish_date.strftime("%B, %#d, %Y")

    def __str__(self):
        return self.title

    @classmethod
    async def fetch_comic(cls, number):
        if type(number) is str and number.isdigit():
            number = int(number)
        number
        if number <= 0:
            raise XkcdError("That comic does not exist.")

        link = cls.XKCD_URL + str(number)
        xkcd_json = link + "/info.0.json"

        async with aiohttp.ClientSession().get(xkcd_json) as resp:
            unparsed_data = await resp.read()

        return cls(unparsed_data, number, link)


async def get_latest_comic_num():
    async with aiohttp.ClientSession().get("https://xkcd.com/info.0.json") as resp:
        unparsed = await resp.read()
    data = json.loads(unparsed.decode())
    number = data['num']
    return int(number)


async def get_latest_comic():
    latest = await get_latest_comic_num()
    return await Comic.fetch_comic(latest)


async def get_random_comic():
    latest = await get_latest_comic_num()
    number = randint(1, latest)
    return await Comic.fetch_comic(number)


async def get_comic(number: int):
    latest = await get_latest_comic_num()
    if number > latest:
        raise XkcdError("That comic does not exist. Number is too high.")
    return await Comic.fetch_comic(number)
