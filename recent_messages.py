import re


async def replace_mentions(message, bot_user_id):
    pattern = re.compile(r"<@!?(\d+)>|@everyone|@here")
    mentions = {m.id: m.display_name for m in message.mentions}
    text = message.content
    parts = []
    cursor = 0
    for match in pattern.finditer(text):
        parts.append(text[cursor : match.start()])
        token = match.group(0)
        user_id = match.group(1)
        if user_id:
            uid = int(user_id)
            if uid == bot_user_id:
                parts.append("Xander")
            else:
                mention_name = (mentions or {}).get(uid)
                if mention_name:
                    parts.append(mention_name)
                else:
                    parts.append(token)
        elif token == "@everyone":
            parts.append("everyone")
        else:
            parts.append("here")
        cursor = match.end()
    parts.append(text[cursor:])
    return "".join(parts)


async def collect_recent_messages(message, bot_user_id, history_limit=10, reply_chain_limit=5):
    messages_by_id = {}
    async for item in message.channel.history(limit=history_limit, before=message):
        cleaned = await replace_mentions(item, bot_user_id)
        messages_by_id[item.id] = {
            "id": item.id,
            "created_at": item.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "speaker": ("Xander" if item.author.id == bot_user_id else item.author.display_name),
            "text": cleaned,
        }

    current = message
    for _ in range(reply_chain_limit):
        ref = current.reference
        if not ref:
            break
        prev_msg = ref.resolved or await message.channel.fetch_message(ref.message_id)
        cleaned = await replace_mentions(prev_msg, bot_user_id)
        messages_by_id[prev_msg.id] = {
            "id": prev_msg.id,
            "created_at": prev_msg.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "speaker": ("Xander" if prev_msg.author.id == bot_user_id else prev_msg.author.display_name),
            "text": cleaned,
        }
        current = prev_msg

    merged = sorted(messages_by_id.values(), key=lambda row: row["created_at"])
    lines = []
    for row in merged:
        lines.append(f"[{row['created_at']}] {row['speaker']}: {row['text']}")
    return "\n".join(lines)
