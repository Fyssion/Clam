# Clam

My personal Discord bot.

## About

Clam was originally created as a fun, simple testing bot.
I used it to experiment with new programming concepts and learn different technologies.
These days, Clam is used by my friends for music, a starboard, moderation, and more.
Unfortunately, the bot is private due to Discord's verification policies.
However, there are some [installation instructions](#running) below.

## Features

Here's a brief list of Clam's most prominent features:

- Music
  - Clam uses [youtube-dl](ytdl) and its own player system to play music in voice channels.
- Moderation
  - Clam includes an auto moderator, logging tools, and other moderation-related commands.
- Starboard
  - This feature was ported from [R. Danny](rdanny) after it could no longer be invited.
- Reaction roles (and self roles)
- Highlight
  - Clam contains a fully-featured [Highlight](hl) remake that notifies you when your trigger words are said in chat.
- Other
  - Clam also has games, tags, reminders, and other tools, utilities, and playful commands.
  Clam also has an events feature, but it became obsolete after Discord introduced their own version.

### Clam in Action

Here are some GIFs I recorded some time ago demonstrating Clam's features.

<img src="https://i.imgur.com/QmwI8CI.gif" alt="Commands GIF" width="400"/>
<img src="https://i.imgur.com/tzLbb32.gif" alt="Music commands GIF" width="400"/>

## Running

Clam was not designed to be run by anyone except me, so I do not advise running the bot.
However, I have provided installation instructions for the intrepid.

> Python 3.8+, PostgreSQL 9.5+, and [Poetry](https://python-poetry.org/) are **required** for installation.

```sh
# Clone the repository from GitHub and enter the server directory.
git clone https://github.com/Fyssion/Clam.git
cd Clam

# OPTIONAL: create and activate a virtual env to house the requirements.
python3 -m venv venv
source venv/bin/activate

# Install the requirements.
poetry install

# Configure the bot.
# Create and edit a config file based on the Config section below.

# Setup the PostgreSQL database.
# See the Database Setup section below for more info.

# Run the bot.
python3 -m clam
```

### Config

You'll need quite a few API keys to run the bot.

Create a file called `config.yml` in the base directory,
and paste in the template below.

```yml
bot-token: bot token
database-uri: postgresql://user:pass@localhost:5432/db

# The channel where the bot will broadcast errors and other things.
# This is different than the status hook.
console: console channel id

# Required API keys
google-api-key: google api key
wolfram-api-key: wolfram api key
cleverbot-api-key: cleverbot api key

# Optional options

# The URL to a webhook that will broadcast connection status
status-hook: status webhook url

# Debug mode. Ignore this unless you know what you're doing.
# debug: 0
```

### Database Setup

To setup PostgreSQL, use the following SQL commands using `psql`:

```sql
CREATE ROLE clam WITH LOGIN PASSWORD 'yourpw';
CREATE DATABASE clam OWNER clam;
CREATE EXTENSION pg_trgm;
```

You'll then need to run `python3 -m clam db init` to initialize the database.
This must be done before running the bot.
For all database management options, run `python3 -m clam db --help`.

## Acknowledgements

Thanks to Danny for creating [discord.py](dpy) and [R. Danny](rdanny).
Also, many thanks towards [youtube-dl](ytdl) and its maintainers for their amazing work over the years.
Without these people and projects, Clam would not be possible.

[ytdl]: https://github.com/ytdl-org/youtube-dl
[rdanny]: https://github.com/Rapptz/RoboDanny
[hl]: https://discord.bots.gg/bots/292212176494657536
[dpy]: https://github.com/Rapptz/discord.py
