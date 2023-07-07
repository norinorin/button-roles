try:
    import dotenv
except ImportError:
    pass
else:
    dotenv.load_dotenv()

import os
from enum import IntEnum, auto

import hikari
import lightbulb

app = lightbulb.BotApp(
    os.getenv("DISCORD_TOKEN"),
    cache_settings=hikari.impl.CacheSettings(
        components=hikari.api.CacheComponents.ROLES  # for getting role names and perms
        | hikari.api.CacheComponents.GUILD_CHANNELS  # also for perms
        | hikari.api.CacheComponents.GUILDS  # perms :D
        | hikari.api.CacheComponents.ME,
    ),
)


class Mode(IntEnum):
    NORMAL = auto()
    UNIQUE = auto()


async def _handle_message_send_request(event: hikari.InteractionCreateEvent):
    assert isinstance(event.interaction, hikari.ModalInteraction)
    await event.interaction.create_initial_response(
        hikari.ResponseType.DEFERRED_MESSAGE_CREATE, flags=hikari.MessageFlag.EPHEMERAL
    )
    msg = await app.rest.create_message(
        event.interaction.channel_id, event.interaction.components[0][0].value
    )
    await event.interaction.edit_initial_response(
        f"Message created! {msg.make_link(event.interaction.guild_id)} Right click the message and invoke the Edit Roles command!"
    )


async def _handle_message_edit_request(event: hikari.InteractionCreateEvent):
    assert isinstance(event.interaction, hikari.ModalInteraction)
    await event.interaction.create_initial_response(
        hikari.ResponseType.DEFERRED_MESSAGE_CREATE, flags=hikari.MessageFlag.EPHEMERAL
    )
    msg = await app.rest.edit_message(
        event.interaction.channel_id,
        event.interaction.custom_id[5:],
        event.interaction.components[0][0].value,
    )
    await event.interaction.edit_initial_response(f"Successfully edited the message!")


async def _handle_update_buttons_request(interaction: hikari.ComponentInteraction):
    await interaction.create_initial_response(
        hikari.ResponseType.DEFERRED_MESSAGE_UPDATE
    )
    target_id = interaction.custom_id[3:]
    roles = [*interaction.resolved.roles.values()][::-1]
    chunked_roles = [roles[i : i + 5] for i in range(0, len(roles), 5)]
    rows = [app.rest.build_message_action_row() for _ in range(len(chunked_roles))]
    for i, roles in enumerate(chunked_roles):
        for role in roles:
            rows[i].add_interactive_button(
                hikari.ButtonStyle.PRIMARY,
                f"r{interaction.custom_id[1]}-{role.id}",
                emoji=role.make_icon_url(size=256) or hikari.UNDEFINED,
                label=role.name,
            )

    await app.rest.edit_message(interaction.channel_id, target_id, components=rows)
    await interaction.edit_initial_response(
        "Successfully updated the roles!", components=[]
    )


def _get_roles_from_buttons(rows):
    role_ids = set()
    for row in rows:
        for component in row:
            role_ids.add(int(component.custom_id[3:]))
    return role_ids


def _check_unique(member, roles):
    conflicting = [
        app.cache.get_role(i).name for i in (set(member.role_ids) & set(roles))
    ]
    if conflicting:
        err = "a conflicting role" if len(conflicting) == 1 else "conflicting roles"
        raise RuntimeError(f"you have {err}! ({', '.join(conflicting)})")


async def _handle_role_request(interaction: hikari.ComponentInteraction):
    await interaction.create_initial_response(
        hikari.ResponseType.DEFERRED_MESSAGE_CREATE, flags=hikari.MessageFlag.EPHEMERAL
    )
    action = "add"
    role_id = int(interaction.custom_id[3:])
    is_unique = int(interaction.custom_id[1]) == Mode.UNIQUE
    try:
        if role_id not in interaction.member.role_ids:
            if is_unique:
                _check_unique(
                    interaction.member,
                    _get_roles_from_buttons(interaction.message.components),
                )
            await interaction.member.add_role(role_id)
        else:
            action = "remove"
            await interaction.member.remove_role(role_id)
    except Exception as e:
        await interaction.edit_initial_response(f"Failed to {action} the role: {e}")
        return

    await interaction.edit_initial_response(
        f"Successfully {action.rstrip('e')}ed {app.cache.get_role(role_id)}"
    )


async def _handle_mode_change(interaction: hikari.ComponentInteraction):
    select = app.rest.build_message_action_row().add_select_menu(
        hikari.ComponentType.ROLE_SELECT_MENU,
        f"u{interaction.values[0]}-{interaction.custom_id[5:]}",
        min_values=1,
        max_values=25,
    )
    await interaction.create_initial_response(
        hikari.ResponseType.MESSAGE_UPDATE, "Select the roles", component=select
    )


def _has_permissions(perms: hikari.Permissions):
    return lightbulb.add_checks(
        lightbulb.has_guild_permissions(perms)
        | lightbulb.Check(lambda ctx: ctx.member.id == ctx.get_guild().owner_id)
    )


def _editable(error):
    def _check(ctx):
        if ctx.options.target.author.id != app.get_me().id:
            raise RuntimeError(error)
        return True

    return lightbulb.add_checks(lightbulb.Check(_check))


@app.listen()
async def on_interaction(event: hikari.InteractionCreateEvent):
    cmd = getattr(event.interaction, "custom_id", "")
    if cmd == "msg":
        await _handle_message_send_request(event)
    elif cmd.startswith("edit"):
        await _handle_message_edit_request(event)
    elif isinstance(event.interaction, hikari.ComponentInteraction):
        cmd = event.interaction.custom_id
        if cmd.startswith("u"):
            await _handle_update_buttons_request(event.interaction)
        elif cmd.startswith("r"):
            await _handle_role_request(event.interaction)
        elif cmd.startswith("mode"):
            await _handle_mode_change(event.interaction)


@app.listen()
async def on_error(event: lightbulb.CommandErrorEvent):
    if isinstance(event.exception, lightbulb.CheckFailure):
        await event.context.respond(
            event.exception.__cause__, flags=hikari.MessageFlag.EPHEMERAL
        )
        return

    raise event.exception


@app.command
@_has_permissions(hikari.Permissions.MANAGE_ROLES)
@lightbulb.command("send", "Send a new message which will contain the buttons")
@lightbulb.implements(lightbulb.SlashCommand)
async def new(ctx: lightbulb.SlashContext):
    text_input = app.rest.build_modal_action_row().add_text_input(
        "message",
        "Message",
        style=hikari.TextInputStyle.PARAGRAPH,
        required=True,
        max_length=2048,
    )
    await ctx.interaction.create_modal_response(
        "Type the message here", "msg", component=text_input
    )


@app.command
@_has_permissions(hikari.Permissions.MANAGE_ROLES)
@_editable(
    "Can only add buttons to a message sent by the bot. Please create a new message using the /send command."
)
@lightbulb.command("Edit Roles", "Edit the self-assignable roles")
@lightbulb.implements(lightbulb.MessageCommand)
async def edit_roles(ctx: lightbulb.MessageContext):
    select = (
        app.rest.build_message_action_row()
        .add_text_menu(f"mode-{ctx.options.target.id}")
        .add_option(
            "Normal", Mode.NORMAL, description="Users get to pick multiple roles"
        )
        .add_option(
            "Unique", Mode.UNIQUE, description="Users can only pick 1 role at a time"
        )
    )
    await ctx.respond(
        "Pick the mode", flags=hikari.MessageFlag.EPHEMERAL, component=select.parent
    )


@app.command
@_has_permissions(hikari.Permissions.MANAGE_ROLES)
@_editable("Can only remove buttons from a message sent by the bot.")
@lightbulb.command("Remove Buttons", "Remove buttons from the message")
@lightbulb.implements(lightbulb.MessageCommand)
async def remove_buttons(ctx: lightbulb.MessageContext):
    await ctx.options.target.edit(components=[])
    await ctx.respond("Removed the buttons!", flags=hikari.MessageFlag.EPHEMERAL)


@app.command
@_has_permissions(hikari.Permissions.MANAGE_MESSAGES)
@_editable("Can only edit messages sent by the bot.")
@lightbulb.command("Edit Message", "Edit a message")
@lightbulb.implements(lightbulb.MessageCommand)
async def remove_buttons(ctx: lightbulb.MessageContext):
    text_input = app.rest.build_modal_action_row().add_text_input(
        "message",
        "New message",
        style=hikari.TextInputStyle.PARAGRAPH,
        required=True,
        max_length=2048,
    )
    await ctx.interaction.create_modal_response(
        "Type the new message here",
        f"edit-{ctx.options.target.id}",
        component=text_input,
    )


app.run()
