
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote as urlquote
from discord.ext import commands, tasks
from bs4 import BeautifulSoup as bs
import aiomcrcon as rcon
import functools
import aiohttp
import discord
import asyncio
import random
import base64
import arrow
import json
import os


class Minecraft(commands.Cog):
    def __init__(self, bot):
        self.mosaic = __import__('util.mosaic').mosaic  # so I can pull and use the new code from the new changes

        self.bot = bot
        self.d = self.bot.d

        self.db = self.bot.get_cog('Database')

        self.ses = aiohttp.ClientSession(loop=self.bot.loop)

        self.d.mcserver_list = []

        self.update_server_list.start()
        self.clear_rcon_cache.start()

    def cog_unload(self):
        del self.mosaic
        self.update_server_list.cancel()
        self.bot.loop.create_task(self.ses.close())

    @tasks.loop(hours=2)
    async def update_server_list(self):
        self.bot.logger.info('scraping mc-lists.org...')

        servers_nice = []

        for i in range(1, 26):
            async with self.ses.get(f'https://mc-lists.org/pg.{i}') as res:
                soup = bs(await res.text(), 'html.parser')
                elems = soup.find(class_='ui striped table servers serversa').find_all('tr')

                for elem in elems:
                    split = str(elem).split('\n')
                    url = split[9][9:-2]
                    ip = split[16][46:-2].replace('https://', '').replace('http://', '')
                    servers_nice.append((ip, url,))

        self.d.mcserver_list = list(set(servers_nice)) + self.d.additional_mcservers

        self.bot.logger.info('finished scraping mc-lists.org')

    @update_server_list.before_loop
    async def before_update_server_list(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=30)
    async def clear_rcon_cache(self):
        for key in list(self.d.rcon_connection_cache):
            if (arrow.utcnow() - self.d.rcon_connection_cache[key][1]).seconds > 10*60:
                self.d.rcon_connection_cache.pop(key, None)

    @commands.command(name='mcimage', aliases=['mcpixelart', 'mcart', 'mcimg'])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def mcpixelart(self, ctx):
        files = ctx.message.attachments

        if len(files) < 1:
            await self.bot.send(ctx, ctx.l.minecraft.mcimage.stupid_1)
            return
        else:
            img = files[0]

        if img.filename.lower()[-4:] not in ('.jpg', '.png',) and not img.filename.lower()[-5:] in ('.jpeg'):
            await self.bot.send(ctx, ctx.l.minecraft.mcimage.stupid_2)
            return

        try:
            img.height
        except Exception:
            await self.bot.send(ctx, ctx.l.minecraft.mcimage.stupid_3)
            return

        detailed = ('large' in ctx.message.content or 'high' in ctx.message.content)

        with ctx.typing():
            with ThreadPoolExecutor() as pool:
                mosaic_gen_partial = functools.partial(self.mosaic.generate, await img.read(use_cached=True), 1600, detailed)
                _, img_bytes = await self.bot.loop.run_in_executor(pool, mosaic_gen_partial)

            filename = f'tmp/{ctx.message.id}-{img.width}x{img.height}.png'

            with open(filename, 'wb+') as tmp:
                tmp.write(img_bytes)

            await ctx.send(file=discord.File(filename, filename=img.filename))

            os.remove(filename)

    @commands.command(name='mcping', aliases=['mcstatus'])
    @commands.cooldown(1, 2.5, commands.BucketType.user)
    async def mcping(self, ctx, host=None, port: int = None):
        """Checks the status of a given Minecraft server"""

        if host is None:
            combined = (await self.db.fetch_guild(ctx.guild.id))['mcserver']
            if combined is None:
                await self.bot.send(ctx, ctx.l.minecraft.mcping.shortcut_error.format(ctx.prefix))
                return
        else:
            port_str = ''
            if port is not None and port != 0:
                port_str = f':{port}'
            combined = f'{host}{port_str}'

        with ctx.typing():
            async with self.ses.get(f'https://api.iapetus11.me/mc/mcstatus/{combined}', headers={'Authorization': self.d.vb_api_key}) as res:  # fetch status from api
                jj = await res.json()

        if not jj['success'] or not jj['online']:
            embed = discord.Embed(color=self.d.cc, title=ctx.l.minecraft.mcping.title_offline.format(self.d.emojis.offline, combined))
            await ctx.send(embed=embed)
            return

        player_list = jj.get('players_names', [])
        if player_list is None: player_list = []

        players_online = jj['players_online']  # int@

        embed = discord.Embed(color=self.d.cc, title=ctx.l.minecraft.mcping.title_online.format(self.d.emojis.online, combined))

        embed.add_field(name=ctx.l.minecraft.mcping.latency, value=jj['latency'])
        ver = jj['version'].get('brand', 'Unknown')
        embed.add_field(name=ctx.l.minecraft.mcping.version, value=('Unknown' if ver is None else ver))

        player_list_cut = []

        for p in player_list:
            if not ('§' in p or len(p) > 16 or len(p) < 3 or ' ' in p or '-' in p):
                player_list_cut.append(p)

        player_list_cut = player_list_cut[:24]

        if len(player_list_cut) < 1:
            embed.add_field(
                name=ctx.l.minecraft.mcping.field_online_players.name.format(players_online, jj['players_max']),
                value=ctx.l.minecraft.mcping.field_online_players.value,
                inline=False
            )
        else:
            extra = ''
            if len(player_list_cut) < players_online:
                extra = ctx.l.minecraft.mcping.and_other_players.format(players_online - len(player_list_cut))

            embed.add_field(
                name=ctx.l.minecraft.mcping.field_online_players.name.format(players_online, jj['players_max']),
                value='`' + '`, `'.join(player_list_cut) + '`' + extra,
                inline=False
            )

        embed.set_image(url=f'https://api.iapetus11.me/mc/servercard/{combined}?v={random.random()*100000}')

        if jj['favicon'] is not None:
            embed.set_thumbnail(url=f'https://api.iapetus11.me/mc/serverfavicon/{combined}')

        await ctx.send(embed=embed)

    @commands.command(name='randommc', aliases=['randommcserver', 'randomserver'])
    @commands.cooldown(1, 2, commands.BucketType.user)
    async def random_mc_server(self, ctx):
        """Checks the status of a random Minecraft server"""

        s = random.choice(self.d.mcserver_list)
        combined = s[0]

        with ctx.typing():
            async with self.ses.get(f'https://api.iapetus11.me/mc/mcstatus/{combined}', headers={'Authorization': self.d.vb_api_key}) as res:  # fetch status from api
                jj = await res.json()

        if not jj['success'] or not jj['online']:
            self.d.mcserver_list.pop(self.d.mcserver_list.index(s))
            await self.random_mc_server(ctx)
            return

        player_list = jj.get('players_names', [])
        if player_list is None: player_list = []

        players_online = jj['players_online']  # int@

        embed = discord.Embed(color=self.d.cc, title=ctx.l.minecraft.mcping.title_plain.format(self.d.emojis.online, combined))

        if s[1] is not None:
            embed.description = ctx.l.minecraft.mcping.learn_more.format(s[1])

        embed.add_field(name=ctx.l.minecraft.mcping.latency, value=jj['latency'])
        ver = jj['version'].get('brand', 'Unknown')
        embed.add_field(name=ctx.l.minecraft.mcping.version, value=('Unknown' if ver is None else ver))

        player_list_cut = []

        for p in player_list:
            if not ('§' in p or len(p) > 16 or len(p) < 3 or ' ' in p or '-' in p):
                player_list_cut.append(p)

        player_list_cut = player_list_cut[:24]

        if len(player_list_cut) < 1:
            embed.add_field(
                name=ctx.l.minecraft.mcping.field_online_players.name.format(players_online, jj['players_max']),
                value=ctx.l.minecraft.mcping.field_online_players.value,
                inline=False
            )
        else:
            extra = ''
            if len(player_list_cut) < players_online:
                extra = ctx.l.minecraft.mcping.and_other_players.format(players_online - len(player_list_cut))

            embed.add_field(
                name=ctx.l.minecraft.mcping.field_online_players.name.format(players_online, jj['players_max']),
                value='`' + '`, `'.join(player_list_cut) + '`' + extra,
                inline=False
            )

        embed.set_image(url=f'https://api.iapetus11.me/mc/servercard/{combined}?v={random.random()*100000}')

        if jj['favicon'] is not None:
            embed.set_thumbnail(url=f'https://api.iapetus11.me/mc/serverfavicon/{combined}')

        await ctx.send(embed=embed)

    @commands.command(name='stealskin', aliases=['getskin', 'skin', 'mcskin'])
    @commands.cooldown(1, 2.5, commands.BucketType.user)
    async def steal_skin(self, ctx, player):
        """"steals" the skin of a Minecraft player"""

        with ctx.typing():
            res = await self.ses.get(f'https://api.mojang.com/users/profiles/minecraft/{player}')

        if res.status == 204:
            await self.bot.send(ctx, ctx.l.minecraft.invalid_player)
            return

        uuid = (await res.json()).get('id')

        if uuid is None:
            await self.bot.send(ctx, ctx.l.minecraft.invalid_player)
            return

        res_profile = await self.ses.get(f'https://sessionserver.mojang.com/session/minecraft/profile/{uuid}?unsigned=false')
        profile_content = await res_profile.json()

        if 'error' in profile_content or len(profile_content['properties']) == 0:
            await self.bot.send(ctx, ctx.l.minecraft.stealskin.error_1)
            return

        try:
            decoded_jj = json.loads(base64.b64decode(profile_content['properties'][0]['value']))
            skin_url = decoded_jj['textures']['SKIN']['url']
        except Exception:
            await self.bot.send(ctx, ctx.l.minecraft.stealskin.error_1)
            return

        embed = discord.Embed(color=self.d.cc, description=ctx.l.minecraft.stealskin.embed_desc.format(player, skin_url))
        embed.set_thumbnail(url=skin_url)
        embed.set_image(url=f'https://mc-heads.net/body/{player}')

        await ctx.send(embed=embed)

    @commands.command(name='achievement', aliases=['mcachieve'])
    async def minecraft_achievement(self, ctx, *, text):
        embed = discord.Embed(color=self.d.cc)
        embed.set_image(url=f'https://api.iapetus11.me/mc/achievement/{urlquote(text[:26])}')
        await ctx.send(embed=embed)

    @commands.command(name='uuidtoname', aliases=['uuidtousername', 'uuid2name'])
    @commands.cooldown(1, 2, commands.BucketType.user)
    async def uuid_to_username(self, ctx, uuid):
        """Turns a Minecraft uuid into a username"""

        with ctx.typing():
            res = await self.ses.get(f'https://api.mojang.com/user/profiles/{uuid}/names')

        if res.status == 204:
            await self.bot.send(ctx, ctx.l.minecraft.invalid_player)
            return

        jj = await res.json()

        try:
            name = jj[-1]['name']
        except KeyError:
            await self.bot.send(ctx, ctx.l.minecraft.invalid_player)
            return

        await self.bot.send(ctx, f'**{uuid}**: `{name}`')

    @commands.command(name='nametouuid', aliases=['usernametouuid', 'name2uuid'])
    @commands.cooldown(1, 2, commands.BucketType.user)
    async def username_to_uuid(self, ctx, username):
        """Turns a Minecraft username into a Minecraft uuid"""

        with ctx.typing():
            res = await self.ses.post('https://api.mojang.com/profiles/minecraft', json=[username])

        jj = await res.json()

        if not jj or len(jj) < 1 or res.status == 204:
            await self.bot.send(ctx, ctx.l.minecraft.invalid_player)
            return

        uuid = jj[0]['id']

        await self.bot.send(ctx, f'**{username}**: `{uuid}`')

    @commands.command(name='nametoxuid', aliases=['grabxuid', 'benametoxuid', 'bename'])
    @commands.cooldown(1, 2, commands.BucketType.user)
    async def name_to_xuid(self, ctx, *, username):
        """Turns a Minecraft BE username/gamertag into an xuid"""

        with ctx.typing():
            res = await self.ses.get(f'https://xapi.us/v2/xuid/{urlquote(username)}', headers={'X-AUTH': self.d.xapi_key})

        if res.status != 200:
            await self.bot.send(ctx, ctx.l.minecraft.invalid_player)
            return

        xuid = f'{"0"*8}-{"0000-"*3}{hex(int(await res.text())).strip("0x")}'

        await self.bot.send(ctx, f'**{username}**: `{xuid}` / `{xuid[20:].replace("-", "").upper()}`')

    @commands.command(name='mccolors', aliases=['minecraftcolors', 'chatcolors', 'colorcodes'])
    async def color_codes(self, ctx):
        """Shows the Minecraft chat color codes"""

        embed = discord.Embed(color=self.d.cc, description=ctx.l.minecraft.mccolors.embed_desc)

        embed.set_author(name=ctx.l.minecraft.mccolors.embed_author_name)

        cs = ctx.l.minecraft.mccolors.formatting_codes

        embed.add_field(
            name=ctx.l.minecraft.mccolors.colors,
            value=f'<:red:697541699706028083> **{cs.red}** `§c`\n'
                  f'<:yellow:697541699743776808> **{cs.yellow}** `§e`\n'
                  f'<:green:697541699316219967> **{cs.green}** `§a`\n'
                  f'<:aqua:697541699173613750> **{cs.aqua}** `§b`\n'
                  f'<:blue:697541699655696787> **{cs.blue}** `§9`\n'
                  f'<:light_purple:697541699546775612> **{cs.light_purple}** `§d`\n'
                  f'<:white:697541699785719838> **{cs.white}** `§f`\n'
                  f'<:gray:697541699534061630> **{cs.gray}** `§7`\n'
        )

        embed.add_field(
            name=ctx.l.minecraft.mccolors.more_colors,
            value=f'<:dark_red:697541699488055426> **{cs.dark_red}** `§4`\n'
                  f'<:gold:697541699639050382> **{cs.gold}** `§6`\n'
                  f'<:dark_green:697541699500769420> **{cs.dark_green}** `§2`\n'
                  f'<:dark_aqua:697541699475472436> **{cs.dark_aqua}** `§3`\n'
                  f'<:dark_blue:697541699488055437> **{cs.dark_blue}** `§1`\n'
                  f'<:dark_purple:697541699437592666> **{cs.dark_purple}** `§5`\n'
                  f'<:dark_gray:697541699471278120> **{cs.dark_gray}** `§8`\n'
                  f'<:black:697541699496444025> **{cs.black}** `§0`\n'
        )

        embed.add_field(
            name=ctx.l.minecraft.mccolors.formatting,
            value=f'<:bold:697541699488186419> **{cs.bold}** `§l`\n'
                  f'<:strikethrough:697541699768942711> ~~{cs.strikethrough}~~ `§m`\n'
                  f'<:underline:697541699806953583> {cs.underline} `§n`\n'
                  f'<:italic:697541699152379995> *{cs.italic}* `§o`\n'
                  f'<:obfuscated:697541699769204736> ||{cs.obfuscated}|| `§k`\n'
                  f'<:reset:697541699697639446> {cs.reset} `§r`\n'
        )

        await ctx.send(embed=embed)

    @commands.command(name='buildidea', aliases=['idea'])
    async def build_idea(self, ctx):
        """Sends a random "build idea" which you could create"""

        prefix = random.choice(self.d.build_ideas['prefixes'])
        idea = random.choice(self.d.build_ideas['ideas'])

        await self.bot.send(ctx, f'{prefix} {idea}!')

    async def close_rcon_con(self, key, gid):
        try:
            await self.d.rcon_connection_cache[key][0].close()
        except Exception:
            pass

        self.d.rcon_connection_cache.pop(key, None)

        await self.db.set_guild_attr(gid, 'mcserver_rcon', None)  # port could be invalid, so reset it

    @commands.command(name='rcon', aliases=['mccmd', 'servercmd', 'servercommand', 'scmd'])
    @commands.max_concurrency(1, per=commands.BucketType.user, wait=False)
    @commands.cooldown(1, 1, commands.BucketType.user)
    @commands.guild_only()
    async def rcon_command(self, ctx, *, cmd):
        author_check = (lambda m: ctx.author.id == m.author.id and ctx.author.dm_channel.id == m.channel.id)
        db_guild = await self.db.fetch_guild(ctx.guild.id)

        if db_guild['mcserver'] is None:
            await self.bot.send(ctx, 'You have to set a Minecraft server for this guild via the `/config mcserver` command first.')
            return

        key = (ctx.guild.id, ctx.author.id, db_guild['mcserver'])
        cached = self.d.rcon_connection_cache.get(key)

        if cached is None:
            try:
                await self.bot.send(ctx.author, 'Type in the remote console password (rcon.password in the server.properties file) here. This password can be stored for up to 10 minutes past the last rcon command.')
            except Exception:
                await self.bot.send(ctx, 'I need to be able to DM you, either something went wrong or I don\'t have the permissions to.')
                return

            try:
                auth_msg = await self.bot.wait_for('message', check=author_check, timeout=60)
            except asyncio.TimeoutError:
                await self.bot.send(ctx.author, 'I\'ve stopped waiting for a response.')
                return

            if db_guild['mcserver_rcon'] is None:
                try:
                    await self.bot.send(ctx.author, 'Now type in the RCON port (rcon.port in the server.properties file)')
                except Exception:
                    await self.bot.send(ctx, 'I need to be able to DM you, either something went wrong or I don\'t have the permissions to.')
                    return

                try:
                    port_msg = await self.bot.wait_for('message', check=author_check, timeout=60)
                except asyncio.TimeoutError:
                    await self.bot.send(ctx.author, 'I\'ve stopped waiting for a response.')
                    return

                port = 25575
                try:
                    port = int(port_msg.content)
                except Exception:
                    port = 25575

                if 0 > port > 65535:
                    port = 25575

                await self.db.set_guild_attr(ctx.guild.id, 'mcserver_rcon', port)  # update value in db
            else:
                port = db_guild['mcserver_rcon']

            try:
                s = db_guild['mcserver'].split(':')[0]+f':{port}'
                self.d.rcon_connection_cache[key] = (rcon.Client(s, auth_msg.content, 2.5, loop=self.bot.loop), arrow.utcnow())
                await self.d.rcon_connection_cache[key][0].setup()
            except rcon.Errors.ConnectionFailedError:
                await self.bot.send(ctx, 'Connection to the server failed, is RCON enabled?')
                await self.close_rcon_con(key, ctx.guild.id)
                return
            except rcon.Errors.InvalidAuthError:
                await self.bot.send(ctx.author, 'The provided RCON password/authentication is invalid')
                await self.close_rcon_con(key, ctx.guild.id)
                return

            rcon_con = self.d.rcon_connection_cache[key][0]
        else:
            rcon_con = cached[0]
            self.d.rcon_connection_cache[key] = (rcon_con, arrow.utcnow())  # update time

        try:
            resp = await rcon_con.send_cmd(cmd[:1446])  # shorten to avoid unecessary timeouts
        except asyncio.TimeoutError:
            await self.bot.send(ctx, 'A timeout occurred while sending that command to the server')
            await self.close_rcon_con(key, ctx.guild.id)
        except Exception:
            await self.bot.send(ctx, f'For some reason, an error ocurred while sending that command to the server')
            await self.close_rcon_con(key, ctx.guild.id)
        else:
            resp_text = ''
            for i in range(0, len(resp[0])):
                if resp[0][i] != '§' and (i == 0 or resp[0][i-1] != '§'):
                    resp_text += resp[0][i]

            await ctx.send('```{}```'.format(resp_text.replace('\\n', '\n')))


def setup(bot):
    bot.add_cog(Minecraft(bot))
