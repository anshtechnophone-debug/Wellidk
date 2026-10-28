# XYRON Command Reference

Generated from the enabled command registry: 95 commands in 26 categories.

Access labels describe XYRON's command-level gate. Administrative operations additionally re-check the configured owner's current group membership and the bot's required Telegram rights. XYRON bot admins (non-owner) are mute-only: they cannot ban or kick, and can mute at most one member per group every 10 minutes. The configured owner is exempt from this cooldown and retains full moderation access.

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

| Command | Description | Example | Access |\n| --- | --- | --- | --- |\n| `/spamprotector <on|off>` | Owner-only automatic mute after 15 messages within 30 seconds; mute lasts 10 minutes | `/spamprotector on` | configured owner |\n\nThe configured owner can also send `Xyron Spam protector onn` or `Xyron Spam protector off` in the group. This setting is stored per group.\n\n
| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/antiflood <on\|off> [threshold] [seconds] [repeat threshold]` | Configure message flood deletion | `/antiflood on 5 10 3` | group admin |

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

## Fun

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/riddle` | Start a local riddle | `/riddle` | any member |
| `/riddleanswer <word>` | Answer the current riddle | `/riddleanswer crane` | any member |
| `/quickmath` | Start a local arithmetic challenge | `/quickmath` | any member |
| `/mathanswer <number>` | Answer the arithmetic challenge | `/mathanswer 42` | any member |
| `/rps <rock\|paper\|scissors>` | Play non-wagering Rock Paper Scissors | `/rps rock` | any member |

## Games

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/hangman` | Start a local Hangman game | `/hangman` | any member |
| `/letter <a-z>` | Guess a Hangman letter | `/letter a` | any member |
| `/hangcancel` | Cancel Hangman | `/hangcancel` | any member |
| `/numbergame` | Start a local number guessing game | `/numbergame` | any member |
| `/numberguess <1-100>` | Submit a number guess | `/numberguess 42` | any member |
| `/numbercancel` | Cancel Number Guess | `/numbercancel` | any member |
| `/questioncancel <type>` | Cancel a question game | `/questioncancel trivia` | any member |
| `/tictactoe` | Start Tic-Tac-Toe against XYRON | `/tictactoe` | any member |
| `/tttmove <1-9>` | Play a Tic-Tac-Toe square | `/tttmove 5` | any member |
| `/tttcancel` | Cancel Tic-Tac-Toe | `/tttcancel` | any member |
| `/teamword` | Start a cooperative group word game | `/teamword` | any member |
| `/teamguess <word>` | Contribute a team word guess | `/teamguess crane` | any member |
| `/teamwordcancel` | Cancel the group Team Word game | `/teamwordcancel` | any member |

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
| `/help` | Browse commands by category | `/help` | any member |
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
| `/gamerank <hangman\|scramble\|number>` | Show a local game leaderboard | `/gamerank hangman` | any member |

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

## Quiz Games

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/trivia` | Start a local trivia question | `/trivia` | any member |
| `/triviaanswer <a\|b\|c>` | Answer the current trivia question | `/triviaanswer b` | any member |
| `/truefalse` | Start a true/false question | `/truefalse` | any member |
| `/tfanswer <true\|false>` | Answer true or false | `/tfanswer true` | any member |

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

## Word Games

| Command | Description | Example | Access |
| --- | --- | --- | --- |
| `/wordseek [4\|5\|6]` | Start a local Word Seek game | `/wordseek 5` | any member |
| `/guess <word>` | Submit a Word Seek guess | `/guess crane` | any member |
| `/wordcancel` | Cancel your active Word Seek game | `/wordcancel` | any member |
| `/scramble` | Start a scrambled word game | `/scramble` | any member |
| `/unscramble <word>` | Submit a scrambled word answer | `/unscramble crane` | any member |
| `/scramblecancel` | Cancel the scramble game | `/scramblecancel` | any member |
| `/wordchain` | Start a local Word Chain game | `/wordchain` | any member |
| `/chain <word>` | Submit the next Word Chain word | `/chain crane` | any member |
| `/wordchaincancel` | Cancel Word Chain | `/wordchaincancel` | any member |
| `/anagram` | Start a single-answer Anagram challenge | `/anagram` | any member |
| `/anagramanswer <word>` | Submit the Anagram answer | `/anagramanswer crane` | any member |
