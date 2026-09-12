import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import os
import time

# ============================================================
# CONFIGURATION - modifie ici si tu veux changer les paliers
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN")

# Cooldown anti-spam en secondes entre deux gains d'XP
XP_COOLDOWN = 60

# Seuils d'XP nécessaires pour chaque palier (index 0 = palier de départ)
XP_THRESHOLDS = [0, 150, 600, 2000, 5000]

# XP gagné en vocal : X points toutes les Y minutes passées en vocal (hors AFK, hors seul(e))
VOICE_XP_AMOUNT = 1
VOICE_XP_INTERVAL_MINUTES = 5

# Noms EXACTS des rôles Discord (copiés tels quels, emojis inclus)
BRANCHES = {
    "pirate": [
        "☠️pirate",
        "☠️super nova",
        "☠️chasseur de primes",
        "☠️shichibukai",
        "☠️commandant de yonko",
    ],
    "marine": [
        "🌍marine",
        "🌍sous-officier",
        "🌍vice-amiral",
        "🌍agent du cp",
        "🌍chevalier divin",
    ],
}

DATA_FILE = "data.json"

# ============================================================
# STOCKAGE DES DONNEES (fichier JSON simple)
# ============================================================

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

data = load_data()
last_message_time = {}  # anti-spam en mémoire, pas besoin de sauvegarder

def get_user_entry(guild_id, user_id):
    gid = str(guild_id)
    uid = str(user_id)
    if gid not in data:
        data[gid] = {}
    if uid not in data[gid]:
        data[gid][uid] = {"branch": None, "xp": 0, "tier": 0}
    return data[gid][uid]

def tier_from_xp(xp):
    tier = 0
    for i, threshold in enumerate(XP_THRESHOLDS):
        if xp >= threshold:
            tier = i
    return tier

# ============================================================
# BOT SETUP
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Connecté en tant que {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)} commande(s) slash synchronisée(s)")
    except Exception as e:
        print(f"Erreur de synchronisation : {e}")
    if not voice_xp_loop.is_running():
        voice_xp_loop.start()

# ============================================================
# MESSAGE DE BIENVENUE
# ============================================================

@bot.event
async def on_member_join(member: discord.Member):
    channel = member.guild.system_channel
    if channel is None:
        # Pas de salon système configuré : on cherche le premier salon où le bot peut écrire
        for c in member.guild.text_channels:
            if c.permissions_for(member.guild.me).send_messages:
                channel = c
                break
    if channel is None:
        return

    embed = discord.Embed(
        title=f"⚓ Bienvenue {member.display_name} !",
        description=(
            "Deux voies s'offrent à toi sur ce serveur.\n\n"
            "Choisis ta branche avec **/pirate** ou **/marine**, puis discute "
            "(texte ou vocal) pour progresser dans les rangs !"
        ),
        color=discord.Color.gold(),
    )
    embed.add_field(name="☠️ Branche Pirate", value=" → ".join(BRANCHES["pirate"]), inline=False)
    embed.add_field(name="🌍 Branche Marine", value=" → ".join(BRANCHES["marine"]), inline=False)

    try:
        await channel.send(content=member.mention, embed=embed)
    except discord.Forbidden:
        pass

# ============================================================
# GAIN D'XP SUR CHAQUE MESSAGE
# ============================================================

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    key = (message.guild.id, message.author.id)
    now = time.time()

    entry = get_user_entry(message.guild.id, message.author.id)

    if entry["branch"] is not None:
        last_time = last_message_time.get(key, 0)
        if now - last_time >= XP_COOLDOWN:
            last_message_time[key] = now
            await add_xp(message.guild, message.author, entry, 1, message.channel)

    await bot.process_commands(message)

async def add_xp(guild, member, entry, amount, channel_for_announcement):
    entry["xp"] += amount
    new_tier = tier_from_xp(entry["xp"])
    if new_tier > entry["tier"]:
        await promote_member(guild, member, entry, new_tier, channel_for_announcement)
    save_data(data)

# ============================================================
# XP EN VOCAL (tâche de fond, toutes les X minutes)
# ============================================================

@tasks.loop(minutes=VOICE_XP_INTERVAL_MINUTES)
async def voice_xp_loop():
    for guild in bot.guilds:
        announce_channel = guild.system_channel
        if announce_channel is None:
            for c in guild.text_channels:
                if c.permissions_for(guild.me).send_messages:
                    announce_channel = c
                    break

        for voice_channel in guild.voice_channels:
            if guild.afk_channel and voice_channel.id == guild.afk_channel.id:
                continue
            # On ignore les salons vocaux où une seule personne est présente (pas de vraie interaction)
            real_members = [m for m in voice_channel.members if not m.bot]
            if len(real_members) < 2:
                continue
            for member in real_members:
                entry = get_user_entry(guild.id, member.id)
                if entry["branch"] is not None:
                    await add_xp(guild, member, entry, VOICE_XP_AMOUNT, announce_channel)

@voice_xp_loop.before_loop
async def before_voice_xp_loop():
    await bot.wait_until_ready()

async def promote_member(guild, member, entry, new_tier, channel):
    branch = entry["branch"]
    role_names = BRANCHES[branch]
    old_role_name = role_names[entry["tier"]]
    new_role_name = role_names[new_tier]

    old_role = discord.utils.get(guild.roles, name=old_role_name)
    new_role = discord.utils.get(guild.roles, name=new_role_name)

    if old_role and old_role in member.roles:
        await member.remove_roles(old_role)
    if new_role:
        await member.add_roles(new_role)

    entry["tier"] = new_tier

    if channel:
        await channel.send(
            f"🎉 Félicitations {member.mention} ! Tu passes au rang **{new_role_name}** !"
        )

# ============================================================
# VUE DE CONFIRMATION (changement de branche)
# ============================================================

class ConfirmSwitchView(discord.ui.View):
    def __init__(self, member, new_branch):
        super().__init__(timeout=60)
        self.member = member
        self.new_branch = new_branch
        self.confirmed = None

    @discord.ui.button(label="Confirmer, je repars à zéro", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.member.id:
            await interaction.response.send_message("Ce bouton n'est pas pour toi.", ephemeral=True)
            return
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.member.id:
            await interaction.response.send_message("Ce bouton n'est pas pour toi.", ephemeral=True)
            return
        self.confirmed = False
        self.stop()
        await interaction.response.defer()

# ============================================================
# COMMANDE : /branches (explication)
# ============================================================

@bot.tree.command(name="branches", description="Explique les deux branches de rôles et la progression")
async def branches_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="⚔️ Deux voies s'offrent à toi",
        description="Choisis ta branche avec /pirate ou /marine, puis discute sur le serveur pour progresser !",
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="☠️ Branche Pirate",
        value=" → ".join(BRANCHES["pirate"]),
        inline=False,
    )
    embed.add_field(
        name="🌍 Branche Marine",
        value=" → ".join(BRANCHES["marine"]),
        inline=False,
    )
    embed.set_footer(text="Tu peux changer de branche, mais tu repars au rang de départ de la nouvelle branche.")
    await interaction.response.send_message(embed=embed)

# ============================================================
# COMMANDE GENERIQUE POUR REJOINDRE / CHANGER DE BRANCHE
# ============================================================

async def join_branch(interaction: discord.Interaction, branch: str):
    guild = interaction.guild
    member = interaction.user
    entry = get_user_entry(guild.id, member.id)

    if entry["branch"] == branch:
        await interaction.response.send_message(
            f"Tu es déjà dans la branche **{branch}** !", ephemeral=True
        )
        return

    if entry["branch"] is not None:
        # Changement de branche = confirmation nécessaire, il perd sa progression
        view = ConfirmSwitchView(member, branch)
        await interaction.response.send_message(
            f"⚠️ Tu es actuellement **{BRANCHES[entry['branch']][entry['tier']]}**.\n"
            f"Si tu rejoins la branche **{branch}**, tu repars au rang de départ "
            f"(**{BRANCHES[branch][0]}**) et tu perds toute ta progression actuelle.\n\n"
            f"Es-tu sûr(e) ?",
            view=view,
            ephemeral=True,
        )
        await view.wait()
        if not view.confirmed:
            return

        # Retirer tous les anciens rôles de l'ancienne branche
        old_role_name = BRANCHES[entry["branch"]][entry["tier"]]
        old_role = discord.utils.get(guild.roles, name=old_role_name)
        if old_role and old_role in member.roles:
            await member.remove_roles(old_role)

    entry["branch"] = branch
    entry["xp"] = 0
    entry["tier"] = 0
    save_data(data)

    new_role = discord.utils.get(guild.roles, name=BRANCHES[branch][0])
    if new_role:
        await member.add_roles(new_role)

    msg = f"🎉 Bienvenue dans la branche **{branch}** ! Tu commences en tant que **{BRANCHES[branch][0]}**."
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="pirate", description="Rejoindre (ou revenir vers) la branche Pirate")
async def pirate_cmd(interaction: discord.Interaction):
    await join_branch(interaction, "pirate")

@bot.tree.command(name="marine", description="Rejoindre (ou revenir vers) la branche Marine")
async def marine_cmd(interaction: discord.Interaction):
    await join_branch(interaction, "marine")

# ============================================================
# COMMANDE : /profil (voir sa progression)
# ============================================================

@bot.tree.command(name="profil", description="Affiche ta branche, ton rang et ta progression")
async def profil_cmd(interaction: discord.Interaction):
    entry = get_user_entry(interaction.guild.id, interaction.user.id)

    if entry["branch"] is None:
        await interaction.response.send_message(
            "Tu n'as pas encore choisi de branche. Utilise /pirate ou /marine pour commencer !",
            ephemeral=True,
        )
        return

    branch = entry["branch"]
    tier = entry["tier"]
    xp = entry["xp"]
    role_names = BRANCHES[branch]
    current_role = role_names[tier]

    embed = discord.Embed(title=f"Profil de {interaction.user.display_name}", color=discord.Color.blue())
    embed.add_field(name="Branche", value=branch.capitalize(), inline=True)
    embed.add_field(name="Rang actuel", value=current_role, inline=True)
    embed.add_field(name="XP", value=str(xp), inline=True)

    if tier < len(XP_THRESHOLDS) - 1:
        next_threshold = XP_THRESHOLDS[tier + 1]
        next_role = role_names[tier + 1]
        remaining = next_threshold - xp
        embed.add_field(
            name="Prochain rang",
            value=f"{next_role} (encore {remaining} XP)",
            inline=False,
        )
    else:
        embed.add_field(name="Prochain rang", value="Tu as atteint le rang maximum !", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

# ============================================================
# COMMANDE : /classement
# ============================================================

@bot.tree.command(name="classement", description="Affiche le top 10 des membres avec le plus d'XP")
@app_commands.describe(branche="Filtrer par branche (optionnel)")
@app_commands.choices(branche=[
    app_commands.Choice(name="Pirate", value="pirate"),
    app_commands.Choice(name="Marine", value="marine"),
])
async def classement_cmd(interaction: discord.Interaction, branche: app_commands.Choice[str] = None):
    gid = str(interaction.guild.id)
    guild_data = data.get(gid, {})

    entries = []
    for uid, entry in guild_data.items():
        if entry["branch"] is None:
            continue
        if branche is not None and entry["branch"] != branche.value:
            continue
        entries.append((uid, entry))

    entries.sort(key=lambda x: x[1]["xp"], reverse=True)
    top = entries[:10]

    if not top:
        await interaction.response.send_message("Personne n'a encore rejoint de branche !", ephemeral=True)
        return

    lines = []
    for i, (uid, entry) in enumerate(top, start=1):
        member = interaction.guild.get_member(int(uid))
        name = member.display_name if member else f"Membre {uid}"
        icon = "☠️" if entry["branch"] == "pirate" else "🌍"
        role_name = BRANCHES[entry["branch"]][entry["tier"]]
        lines.append(f"**{i}.** {icon} {name} — {role_name} ({entry['xp']} XP)")

    title = "🏆 Classement"
    if branche is not None:
        title += f" — {branche.name}"

    embed = discord.Embed(title=title, description="\n".join(lines), color=discord.Color.orange())
    await interaction.response.send_message(embed=embed)

# ============================================================
# LANCEMENT
# ============================================================

if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("La variable d'environnement DISCORD_TOKEN n'est pas définie.")
    bot.run(TOKEN)
