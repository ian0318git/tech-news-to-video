"""Chat and conversation CLI commands.

Commands:
    ask        Ask a notebook a question
    configure  Configure chat persona and response settings
    history    Get conversation history or clear local cache
"""

import logging

import click
from rich.table import Table

from ..client import NotebookLMClient
from ..types import ChatMode
from .helpers import (
    console,
    get_current_conversation,
    get_current_notebook,
    json_output_response,
    require_notebook,
    resolve_notebook_id,
    resolve_source_ids,
    set_current_conversation,
    with_client,
)

logger = logging.getLogger(__name__)


def _determine_conversation_id(
    *,
    new_conversation: bool,
    explicit_conversation_id: str | None,
    explicit_notebook_id: str | None,
    resolved_notebook_id: str,
    json_output: bool,
) -> str | None:
    """Determine which conversation ID to use for the ask command.

    Returns None if a new conversation should be started, otherwise returns
    the conversation ID to continue.
    """
    if new_conversation:
        if not json_output:
            console.print("[dim]Starting new conversation...[/dim]")
        return None

    if explicit_conversation_id:
        return explicit_conversation_id

    # Check if user switched notebooks via --notebook flag
    cached_notebook = get_current_notebook()
    if explicit_notebook_id and cached_notebook and resolved_notebook_id != cached_notebook:
        if not json_output:
            console.print("[dim]Different notebook specified, starting new conversation...[/dim]")
        return None

    return get_current_conversation()


async def _get_latest_conversation_from_history(
    client, notebook_id: str, json_output: bool
) -> str | None:
    """Fetch the most recent conversation ID from notebook history.

    Returns None if history is unavailable or empty.
    """
    try:
        history = await client.chat.get_history(notebook_id, limit=1)
        if history and history[0]:
            last_conv = history[0][-1]
            conv_id = last_conv[0] if isinstance(last_conv, list) else str(last_conv)
            if not json_output:
                console.print(f"[dim]Continuing conversation {conv_id[:8]}...[/dim]")
            return conv_id
    except Exception as e:
        logger.debug(
            "Failed to fetch conversation history (%s): %s",
            type(e).__name__,
            e,
        )
        if not json_output:
            console.print("[dim]Starting new conversation (history unavailable)[/dim]")
    return None


def register_chat_commands(cli):
    """Register chat commands on the main CLI group."""

    @cli.command("ask")
    @click.argument("question")
    @click.option(
        "-n",
        "--notebook",
        "notebook_id",
        default=None,
        help="Notebook ID (uses current if not set)",
    )
    @click.option("--conversation-id", "-c", default=None, help="Continue a specific conversation")
    @click.option("--new", "new_conversation", is_flag=True, help="Start a new conversation")
    @click.option(
        "--source",
        "-s",
        "source_ids",
        multiple=True,
        help="Limit to specific source IDs (can be repeated)",
    )
    @click.option(
        "--json", "json_output", is_flag=True, help="Output as JSON (includes references)"
    )
    @click.option("--save-as-note", is_flag=True, help="Save response as a note")
    @click.option("--note-title", default=None, help="Note title (use with --save-as-note)")
    @with_client
    def ask_cmd(
        ctx,
        question,
        notebook_id,
        conversation_id,
        new_conversation,
        source_ids,
        json_output,
        save_as_note,
        note_title,
        client_auth,
    ):
        """Ask a notebook a question.

        By default, continues the last conversation. Use --new to start fresh.
        The answer includes inline citations like [1], [2] that reference sources.
        Use --json to get structured output with source IDs for each reference.

        \b
        Example:
          notebooklm ask "what are the main themes?"
          notebooklm ask --new "start fresh question"
          notebooklm ask -c <id> "continue this one"
          notebooklm ask -s src_001 -s src_002 "question about specific sources"
          notebooklm ask "explain X" --json             # Get answer with source references
          notebooklm ask "explain X" --save-as-note     # Save response as a note
        """
        nb_id = require_notebook(notebook_id)

        async def _run():
            async with NotebookLMClient(client_auth) as client:
                nb_id_resolved = await resolve_notebook_id(client, nb_id)
                effective_conv_id = _determine_conversation_id(
                    new_conversation=new_conversation,
                    explicit_conversation_id=conversation_id,
                    explicit_notebook_id=notebook_id,
                    resolved_notebook_id=nb_id_resolved,
                    json_output=json_output,
                )

                # If no conversation ID yet, try to get the most recent one from history
                if effective_conv_id is None and not new_conversation:
                    effective_conv_id = await _get_latest_conversation_from_history(
                        client, nb_id_resolved, json_output
                    )

                sources = await resolve_source_ids(client, nb_id_resolved, source_ids)
                result = await client.chat.ask(
                    nb_id_resolved, question, source_ids=sources, conversation_id=effective_conv_id
                )

                if result.conversation_id:
                    set_current_conversation(result.conversation_id)

                if json_output:
                    from dataclasses import asdict

                    data = asdict(result)
                    # Exclude raw_response from CLI output for brevity
                    del data["raw_response"]
                    json_output_response(data)
                    if not save_as_note:
                        return
                else:
                    console.print("[bold cyan]Answer:[/bold cyan]")
                    console.print(result.answer)
                    if result.is_follow_up:
                        console.print(
                            f"\n[dim]Conversation: {result.conversation_id} (turn {result.turn_number or '?'})[/dim]"
                        )
                    else:
                        console.print(f"\n[dim]New conversation: {result.conversation_id}[/dim]")

                if save_as_note:
                    if not result.answer:
                        console.print("[yellow]Warning: No answer to save as note[/yellow]")
                        return
                    try:
                        title = note_title or f"Chat: {question[:50]}"
                        note = await client.notes.create(nb_id_resolved, title, result.answer)
                        console.print(
                            f"\n[dim]Saved as note: {note.title} ({note.id[:8]}...)[/dim]"
                        )
                    except Exception as e:
                        console.print(f"[yellow]Warning: Failed to save note: {e}[/yellow]")

        return _run()

    @cli.command("configure")
    @click.option(
        "-n",
        "--notebook",
        "notebook_id",
        default=None,
        help="Notebook ID (uses current if not set)",
    )
    @click.option(
        "--mode",
        "chat_mode",
        type=click.Choice(["default", "learning-guide", "concise", "detailed"]),
        default=None,
        help="Predefined chat mode",
    )
    @click.option("--persona", default=None, help="Custom persona prompt (up to 10,000 chars)")
    @click.option(
        "--response-length",
        type=click.Choice(["default", "longer", "shorter"]),
        default=None,
        help="Response verbosity",
    )
    @with_client
    def configure_cmd(ctx, notebook_id, chat_mode, persona, response_length, client_auth):
        """Configure chat persona and response settings.

        \b
        Modes:
          default        General purpose (default behavior)
          learning-guide Educational focus with learning-oriented responses
          concise        Brief, to-the-point responses
          detailed       Verbose, comprehensive responses

        \b
        Examples:
          notebooklm configure --mode learning-guide
          notebooklm configure --persona "Act as a chemistry tutor"
          notebooklm configure --mode detailed --response-length longer
        """
        nb_id = require_notebook(notebook_id)

        async def _run():
            from ..rpc import ChatGoal, ChatResponseLength

            async with NotebookLMClient(client_auth) as client:
                nb_id_resolved = await resolve_notebook_id(client, nb_id)
                if chat_mode:
                    mode_map = {
                        "default": ChatMode.DEFAULT,
                        "learning-guide": ChatMode.LEARNING_GUIDE,
                        "concise": ChatMode.CONCISE,
                        "detailed": ChatMode.DETAILED,
                    }
                    await client.chat.set_mode(nb_id_resolved, mode_map[chat_mode])
                    console.print(f"[green]Chat mode set to: {chat_mode}[/green]")
                    return

                goal = ChatGoal.CUSTOM if persona else None
                length = None
                if response_length:
                    length_map = {
                        "default": ChatResponseLength.DEFAULT,
                        "longer": ChatResponseLength.LONGER,
                        "shorter": ChatResponseLength.SHORTER,
                    }
                    length = length_map[response_length]

                await client.chat.configure(
                    nb_id_resolved, goal=goal, response_length=length, custom_prompt=persona
                )

                parts = []
                if persona:
                    parts.append(
                        f'persona: "{persona[:50]}..."'
                        if len(persona) > 50
                        else f'persona: "{persona}"'
                    )
                if response_length:
                    parts.append(f"response length: {response_length}")
                result = (
                    f"Chat configured: {', '.join(parts)}"
                    if parts
                    else "Chat configured (no changes)"
                )
                console.print(f"[green]{result}[/green]")

        return _run()

    @cli.command("history")
    @click.option(
        "-n",
        "--notebook",
        "notebook_id",
        default=None,
        help="Notebook ID (uses current if not set)",
    )
    @click.option("--limit", "-l", default=20, help="Number of conversations to show")
    @click.option("--clear", "clear_cache", is_flag=True, help="Clear local conversation cache")
    @click.option("--save", "save_as_note", is_flag=True, help="Save history as a note")
    @click.option(
        "-c",
        "--conversation-id",
        default=None,
        help="With --save: save only this specific conversation",
    )
    @click.option("-t", "--note-title", "note_title", default=None, help="Note title (with --save)")
    @with_client
    def history_cmd(
        ctx, notebook_id, limit, clear_cache, save_as_note, conversation_id, note_title, client_auth
    ):
        """Get conversation history or save it as a note.

        \b
        Example:
          notebooklm history                      # Show history for current notebook
          notebooklm history -n nb123             # Show history for specific notebook
          notebooklm history --clear              # Clear local cache
          notebooklm history --save               # Save all history as a note
          notebooklm history --save -c <id>       # Save one conversation as a note
          notebooklm history --save --note-title "Summary"  # Save with custom title
        """

        async def _run():
            async with NotebookLMClient(client_auth) as client:
                if clear_cache:
                    result = client.chat.clear_cache()
                    if result:
                        console.print("[green]Local conversation cache cleared[/green]")
                    else:
                        console.print("[yellow]No cache to clear[/yellow]")
                    return

                nb_id = require_notebook(notebook_id)
                nb_id_resolved = await resolve_notebook_id(client, nb_id)
                # When saving, fetch a large history to avoid truncating with the display limit
                fetch_limit = 1000 if save_as_note else limit
                history = await client.chat.get_history(nb_id_resolved, limit=fetch_limit)
                conversations = history[0] if history and isinstance(history[0], list) else []

                if save_as_note:
                    if not conversations:
                        raise click.ClickException(
                            "No conversation history found for this notebook."
                        )
                    if conversation_id:
                        conv = _find_conversation(conversations, conversation_id)
                        if conv is None:
                            raise click.ClickException(
                                f"Conversation '{conversation_id}' not found. "
                                "Run 'notebooklm history' to see available IDs."
                            )
                        content = _format_single_conversation(conv)
                        title = (
                            note_title
                            or f"Chat: {str(conv[1])[:50] if len(conv) > 1 and conv[1] else 'Conversation'}"
                        )
                    else:
                        content = _format_all_conversations(conversations)
                        title = note_title or "Chat History"
                    note = await client.notes.create(nb_id_resolved, title, content)
                    console.print(f"[green]Saved as note: {note.title} ({note.id[:8]}...)[/green]")
                    return

                if not conversations:
                    console.print("[yellow]No conversation history[/yellow]")
                    return

                console.print("[bold cyan]Conversation History:[/bold cyan]")
                table = Table()
                table.add_column("#", style="dim")
                table.add_column("Conversation ID", style="cyan")
                table.add_column("Question", style="white", max_width=40)
                table.add_column("Answer preview", style="dim", max_width=40)
                for i, conv in enumerate(conversations, 1):
                    if not isinstance(conv, list) or len(conv) < 1:
                        continue
                    conv_id = str(conv[0])
                    question = str(conv[1])[:40] if len(conv) > 1 and conv[1] else ""
                    answer = str(conv[2])[:40] if len(conv) > 2 and conv[2] else ""
                    table.add_row(str(i), conv_id, question, answer)
                console.print(table)
                console.print(
                    "\n[dim]Use 'notebooklm ask -c <id>' to continue a conversation. "
                    "Use 'notebooklm history --save' to save as a note.[/dim]"
                )

        return _run()


def _find_conversation(conversations: list, conversation_id: str) -> list | None:
    """Find a conversation entry by ID."""
    for conv in conversations:
        if isinstance(conv, list) and conv and str(conv[0]) == conversation_id:
            return conv
    return None


def _format_single_conversation(conv: list) -> str:
    """Format one conversation as note content."""
    question = str(conv[1]) if len(conv) > 1 and conv[1] else ""
    answer = str(conv[2]) if len(conv) > 2 and conv[2] else ""
    parts = []
    if question:
        parts.append(f"**Q:** {question}")
    if answer:
        parts.append(f"**A:** {answer}")
    return "\n\n".join(parts)


def _format_all_conversations(conversations: list) -> str:
    """Format all conversations as note content."""
    sections = []
    for i, conv in enumerate(conversations, 1):
        if not isinstance(conv, list) or not conv:
            continue
        section = f"## Conversation {i}\n\n{_format_single_conversation(conv)}"
        sections.append(section)
    return "\n\n---\n\n".join(sections)
