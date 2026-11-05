# XYRON Command Reference

Generated from the current enabled command registry. Use `/help` in XYRON's private chat to browse the live command list.

Access labels describe XYRON's command-level gate. `/help` is private-chat only and requires official-channel membership. Group administration commands also check the actor's Telegram rights and XYRON's required permissions. XYRON bot admins (non-owner) are mute-only: they cannot ban or kick, and can mute at most one member per group every 10 minutes. The configured owner is exempt from this cooldown and retains full moderation access.

## Admin Logs

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/modlog [count]` | Show recent moderation actions | `/modlog 5` | group admin |
| `/modstats` | Show moderation event counts for 30 days | `/modstats` | group admin |
| `/appeals` | List open moderation appeals | `/appeals` | group admin |
| `/reviewappeal <id> <accepted\|rejected\|needs_info> [note]` | Review an appeal without auto-changing moderation | `/reviewappeal 1 accepted [note]` | group admin |

## Admin Tools

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/adminlist` | List current administrators | `/adminlist` | group admin |
| `/approve <reply\|user_id>` | Exempt a member from configured protections | `/approve 123456789` | group admin |
| `/unapprove <reply\|user_id>` | Remove a member exemption | `/unapprove 123456789` | group admin |

## XYRON Bot Admin Management

| Action | Description | Example | Access |
| --- | --- | --- | --- |
| `/botadminlist` in a private chat | List all XYRON bot admins | `/botadminlist` | configured owner |
| `/botadminlist` in a group | List only XYRON bot admins Telegram confirms are members of that group | `/botadminlist` | configured owner |
| Reply to a user's message with `demote` | Remove that user from XYRON's bot-admin list | Reply to their message, then send `demote` | configured owner |

## Anti-Spam

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/spamprotector <on|off>` | Enable or disable automatic spam muting | `/spamprotector on` | configured owner |
| `/smartgc [on|off]` | Configure abusive-language protection, NSFW detection, media cleanup, and approved-user exemptions | `/smartgc` | group admin |

## Group Setup

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/verification <on|off>` | Turn new-member human verification on or off | `/verification off` | group admin |

## Ban and Unban

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/ban <reply\|user_id> [reason]` | Ban a member | `/ban 123456789 spam` | group admin |
| `/dban <reply> [reason]` | Ban and delete replied message | `Reply to the target's message, then send /dban spam` | group admin |
| `/sban <reply\|user_id> [reason]` | Silently ban a member | `/sban 123456789 spam` | group admin |
| `/tban <reply\|user_id> <duration> [reason]` | Temporarily ban a member | `/tban 123456789 30m spam` | group admin |
| `/unban <user_id>` | Unban a member | `/unban 123456789` | group admin |
| `/banlist` | List bans tracked by moderation history | `/banlist` | group admin |

## Filters

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/filter <phrase> <response>` | Reply when a phrase appears | `/filter hello Thanks for joining` | group admin |
| `/filters` | List saved response filters | `/filters` | any member |
| `/stop <phrase>` | Remove a saved filter | `/stop hello` | group admin |

## Games

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/wordseek [4|5|6]` | Start a Word Seek game | `/wordseek 5` | any member |
| `/new4`, `/new5`, `/new6` | Start a 4-, 5-, or 6-letter Word Seek game | `/new5` | any member |
| `/guess <word>` | Submit a Word Seek guess | `/guess crane` | any member |
| `/wordcancel` | Cancel your active Word Seek game | `/wordcancel` | starter or group admin |
| `/wordrank` | Show the Word Seek leaderboard | `/wordrank` | any member |
| `/tictactoe` | Start Tic-Tac-Toe | `/tictactoe` | any member |
| `/tttmove <1-9>` | Make a Tic-Tac-Toe move | `/tttmove 5` | any member |
| `/tttcancel` | Cancel Tic-Tac-Toe | `/tttcancel` | game participant |

## Family Life

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/family`, `/familyinfo` | View a Family profile | `/family` | any member |
| `/familytree` | View your Family connections | `/familytree` | any member |
| `/familyrank`, `/familytop` | View Family leaderboards | `/familyrank` | any member |
| `/familyhelp` | Show Family Life instructions | `/familyhelp` | any member |
| `/wife`, `/husband` | Propose marriage to a replied-to member | Reply, then `/wife` | any member |
| `/divorce` | Request a mutual divorce | Reply, then `/divorce` | any member |
| `/son`, `/daughter`, `/father`, `/mother` | Create a parent/child relationship | Reply, then `/son` | any member |
| `/brother`, `/sister`, `/friend` | Add a sibling or friend | Reply, then `/friend` | any member |
| `/remove` | Remove a Family connection | Reply, then `/remove` | any member |
| `/familyreset` | Reset your Family profile | `/familyreset` | profile owner |

## General

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/start` | Start XYRON | `/start` | any member |
| `/ping` | Check bot responsiveness | `/ping` | any member |
| `/health` | Check database and background workers | `/health` | any member |
| `/modules` | Show implemented module status | `/modules` | any member |

## Group Information

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/groupinfo` | Show current group information | `/groupinfo` | any member |

## Help and About

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/help` | Browse commands by category in private chat | `/help` | channel member; private chat only |
| `/about` | About XYRON | `/about` | any member |

## Kick and Restrict

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/kick <reply\|user_id> [reason]` | Remove a member | `/kick 123456789 spam` | group admin |
| `/dkick <reply> [reason]` | Remove a member and delete replied message | `Reply to the target's message, then send /dkick spam` | group admin |
| `/skick <reply\|user_id> [reason]` | Silently remove a member | `/skick 123456789 spam` | group admin |
| `/kickme` | Remove yourself from this group | `/kickme` | any member |

## Leaderboards

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/wordrank` | Show this chat's Word Seek leaderboard | `/wordrank` | any member |

## Locks

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/lock <links\|forwards\|stickers\|media\|bots\|commands>` | Enable a supported message lock | `/lock links` | group admin |
| `/unlock <lock>` | Disable a message lock | `/unlock links` | group admin |
| `/locks` | List enabled message locks | `/locks` | any member |

## Message Management

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/del (reply)` | Delete the replied-to message | `/del (reply)` | group admin |
| `/purge [1-100] (reply to first)` | Delete up to 100 messages from a reply onward | `/purge 10 (reply to first)` | group admin |

## Mute and Unmute

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/mute <reply\|id> [duration] [reason]` | Mute permanently or with a duration | `/mute 123456789 30m spam` | group admin |
| `/dmute <reply> [reason]` | Restrict and delete replied message | `Reply to the target's message, then send /dmute spam` | group admin |
| `/smute <reply\|user_id> [reason]` | Silently restrict a member | `/smute 123456789 spam` | group admin |
| `/tmute <reply\|user_id> <duration> [reason]` | Temporarily restrict a member | `/tmute 123456789 30m spam` | group admin |
| `/unmute <reply\|user_id>` | Safely restore saved/default permissions | `/unmute 123456789` | group admin |
| `/mutelist` | List XYRON-tracked mute restrictions | `/mutelist` | group admin |

## Notes

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/save <name> <response>` | Save a named response | `/save welcome Thanks for joining` | group admin |
| `/get <name>` | Retrieve a saved response | `/get welcome` | any member |
| `/notes` | List saved response names | `/notes` | any member |

## Owner Tools

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/settings` | Inspect eligible groups and bot permissions | `/settings` | owner only |

## Reminders

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/remind <duration> <message>` | Set a durable personal reminder | `/remind 30m drink water` | any member |
| `/reminders` | List your pending reminders | `/reminders` | any member |
| `/cancelreminder <id>` | Cancel your reminder | `/cancelreminder 1` | any member |

## Reports

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/report [reason]` | Report a replied-to message | `Reply to the message, then send /report possible harassment` | any member |
| `/reports` | List open group reports | `/reports` | group admin |
| `/reviewreport <report_id> <reviewed\|dismissed\|actioned>` | Set a report review status | `/reviewreport 1 reviewed` | group admin |
| `/appeal <message>` | Submit an appeal for admin review | `/appeal drink water` | any member |

## Rules

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/rules` | Show group rules | `/rules` | any member |
| `/setrules <text>` | Set this group's rules | `/setrules Be respectful` | group admin |

## User Information

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/info <user_id> or reply` | Show a group member's status | `/info 123456789 or reply` | any member |

## User Tools

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/id [reply]` | Show Telegram IDs | `/id` | any member |

## Warnings

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/warn <reply\|user_id> [reason]` | Issue a warning | `/warn 123456789 spam` | group admin |
| `/warns <reply\|user_id>` | Show a member's warning count | `/warns 123456789` | group admin |
| `/resetwarn <reply\|user_id>` | Clear warnings | `/resetwarn 123456789` | group admin |
| `/setwarnlimit <1-20>` | Set warning threshold | `/setwarnlimit 3` | group admin |

## Welcome and Goodbye

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/setwelcome <template>` | Set or clear the member welcome | `/setwelcome Welcome {first_name}` | group admin |
| `/setgoodbye <template>` | Set or clear the member goodbye | `/setgoodbye Welcome {first_name}` | group admin |

