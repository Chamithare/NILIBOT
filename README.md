# Telegram Album-Sharing Bot (Final V1)

Features:
- Admins DM the bot with many selected media (Telegram splits them into native albums of up to 10).
- Bot stores every album and creates a unique link per album.
- Admins get a list of album links in DM.
- Admins can publish album links in the group using `/publish <album_key>` or by pasting the key (bot will create a button).
- Group users click the button -> bot posts the album (media group) into the group (no extra text).
- Auto-delete posted albums after configured seconds (default 300).
- Anti-spam Whitelist mode (toggle /mode_on and /mode_off). Use /allow and /disallow to manage.

## Quick deploy (Railway)
1. Create repository and push files.
2. Copy `.env.example` -> `.env` and fill values.
3. Deploy on Railway (or any host). Railway will install dependencies from `requirements.txt`.
4. Ensure bot is admin in both your DB channel and the target group (needs permission to send and delete messages).
5. Use t.me/yourbot to test.

## Admin flow
1. Admin selects many files and sends them as a single upload (Telegram will automatically split into media-groups of up to 10). The bot will store each resulting media-group as a separate album and reply with album keys/links in DM.
2. Admin publishes an album in the group with `/publish <album_key>` — bot posts a button message.
3. Users click button to receive album in the group. Album auto-deletes after N seconds.

