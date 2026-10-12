# Telegram Board V2

A separate Telegram group/channel scheduler with a Telegram bot dashboard, daily aggregate analytics, and error history. Use only in chats where you have permission to post. Do not use it for unsolicited spam.

## Railway environment variables

Create these in Railway **Variables** (do not put secrets in GitHub):

- `BOT_TOKEN` — token from BotFather
- `API_ID` — Telegram API ID
- `API_HASH` — Telegram API hash
- `SESSION_STRING` — Telethon user session string for the account that can access/post to your intended groups
- `ADMIN_ID` — your numeric Telegram user ID
- Optional: `TZ=Asia/Kolkata`

`SESSION_STRING` must be generated from an account you own/control and should be kept secret.

## Deploy

1. Create a NEW GitHub repository named `telegram-board-v2`.
2. Upload all files in this folder.
3. In Railway, create a NEW project/service from that new repository.
4. Add the environment variables above.
5. Deploy as a worker. Check the deployment logs for `Telegram user session connected`.
6. Open your bot in Telegram and send `/start` from the account whose numeric ID matches `ADMIN_ID`.

## Bot usage

- Tap **Load/Manage Groups**, then **Refresh from Telegram**.
- Tap each group/channel to toggle it on/off.
- Tap **Set Message**, then send the text/link.
- Use `/interval 10` to set the interval in minutes (minimum 2).
- Tap **Send Test** to test on the first selected destination.
- Tap **Start Schedule** / **Stop Schedule**.
- **Today's Analytics** shows total attempts across all selected destinations, successes, failures, selected group count and last activity.
- **Error Report** shows recent recorded failures.

## Important limitations

- This starter version sends text and links. Media, sticker and custom-emoji upload/forwarding needs a separate media-aware workflow.
- “Sent successfully” means Telegram accepted the send request; it does not prove every group member saw it.
- Telegram does not reliably expose messages deleted by other users to every connected account. The dashboard can only report deletion events the account actually receives; it cannot guarantee detection of every deletion.
- Railway's normal filesystem may be ephemeral. For long-term analytics/history, attach a persistent volume and set `DB_PATH` to a path on that volume.
- Keep a sensible interval and obey Telegram rate limits and each group's rules. The code records failures instead of bypassing restrictions.
