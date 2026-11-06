# XYRON Command Reference

This reference is aligned with the enabled command catalogue in `app/core/command_registry.py` on branch `public-launch`. The interactive `/help` menu in XYRON's private chat is generated from the same catalogue. Commands are grouped by purpose; each entry includes a plain-language explanation, a copyable example, and who can use it.

## Before using commands

- **Reply** means reply directly to the target member's message, then send the command. This is the most reliable way to select someone.
- **Numeric ID** means the person's Telegram user ID. XYRON cannot look up an arbitrary username that it has never observed; if username lookup fails, use a reply or numeric ID.
- Durations use `s` (seconds), `m` (minutes), `h` (hours), `d` (days), or `w` (weeks), for example `30m`, `2h`, or `1d`.
- `[optional]` means that part can be left out. Text in `<angle brackets>` is a value you replace, not text to type literally.
- Group administration also depends on Telegram permissions and XYRON's owner/admin checks. The bot must have the required permission for the action.
- XYRON bot admins are not Telegram admins. They can use the permitted mute tools only, with a one-member-per-group cooldown of 10 minutes; they cannot ban or kick. The configured XYRON owner is exempt from that cooldown.
- `/help` works only in XYRON's private chat and is available after joining the official channel. Group help is intentionally disabled.

## General

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/start` | Start XYRON in this chat. | `/start` | Everyone |
| `/ping` | Check whether XYRON is responding. | `/ping` | Everyone |
| `/health` | Check database connectivity and background worker status. | `/health` | Everyone |
| `/modules` | Show which XYRON modules are implemented or unavailable. | `/modules` | Everyone |

## Owner Tools

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/broadcast` | Send a replied-to message to known users who have started XYRON privately. The number is the delay in seconds between sends. | `Reply to a message, then send /broadcast 5` | XYRON owner |
| `/broadcast_stop` | Stop the current owner broadcast. | `/broadcast_stop` | XYRON owner |
| `/settings` | Show groups where the XYRON owner can manage settings and inspect bot permissions. | `/settings` | XYRON owner |

## Quotes

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/q` | Create a quote sticker from a message you reply to. Optional number selects the quote style. | `Reply to a message, then send /q 2` | Everyone |
| `/qrand` | Show a random saved quote from this group. | `/qrand` | Everyone |
| `/qtop` | Show this group's highest-rated saved quotes. | `/qtop` | Everyone |

## Help and About

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/help` | Browse commands by category | `/help` | Channel members in private chat |
| `/about` | Show information about XYRON. | `/about` | Everyone |

## Leaderboards

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/daily` | Show the ten most active chatters today. | `/daily` | Everyone |
| `/wordrank` | Show this chat's Word Seek leaderboard | `/wordrank` | Everyone |
| `/familytop` | Show the global Family Life leaderboard ranked by couple protection points. | `/familytop` | Everyone |

## User Tools

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/id` | Show your Telegram user ID. Reply to someone to show their ID. | `/id` | Everyone |

## Admin Tools

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/admin` | Add a XYRON bot admin for this group. Reply to the person or use their username/numeric ID. | `/admin @username` | Group owner |
| `/promote` | Promote a member with XYRON's standard admin permissions. Reply to them, or provide a username/numeric ID. | `Reply to a member, then send /promote` or `/promote @username` | Group owner or admin with Telegram promotion permission |
| `/unadmin` | Remove a XYRON bot admin from this group. Reply to the person or use their username/numeric ID. | `/unadmin @username` | Group owner |
| `/botadminlist` | List XYRON bot admins who are currently members of this group. | `/botadminlist` | Group owner |
| `/adminlist` | List the group's current Telegram administrators. | `/adminlist` | Group admins; XYRON bot admins are mute-only |
| `/approve` | Exempt a member from configured group protections. | `/approve @username` | Group admins; XYRON bot admins are mute-only |
| `/unapprove` | Remove a member's exemption from configured group protections. | `/unapprove @username` | Group admins; XYRON bot admins are mute-only |

## Anti-Spam

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/smartgc` | Open Smart GC settings, or enable/disable its protections. | `/smartgc on` | Group admins; XYRON bot admins are mute-only |
| `/spamprotector` | Turn the owner's automatic spam protector on or off. | `/spamprotector on` | XYRON owner |
| `/antiflood` | Configure flood-message deletion. Example enables it with the shown thresholds. | `/antiflood on 5 10 3` | Group admins; XYRON bot admins are mute-only |

## Group Setup

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/verification` | Turn new-member verification on or off. | `/verification off` | Group admins; XYRON bot admins are mute-only |

## Message Management

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/pin` | Pin the message you reply to. | `Reply to a message, then send /pin` | Group admins; XYRON bot admins are mute-only |
| `/del` | Delete the message you reply to. | `Reply to a message, then send /del` | Group admins; XYRON bot admins are mute-only |
| `/purge` | Delete up to the specified number of messages, starting from the message you reply to. | `Reply to the first message, then send /purge 10` | Group admins; XYRON bot admins are mute-only |

## Ban and Unban

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/ban` | Ban a member from the group. | `/ban @username spam` | Group admins; XYRON bot admins are mute-only |
| `/dban` | Ban the user whose message you reply to, then delete that replied-to message. | `Reply to a message, then send /dban spam` | Group admins; XYRON bot admins are mute-only |
| `/sban` | Ban a member and delete the command message after a successful action. | `/sban @username spam` | Group admins; XYRON bot admins are mute-only |
| `/tban` | Ban a member temporarily for the specified duration. | `/tban @username 30m spam` | Group admins; XYRON bot admins are mute-only |
| `/unban` | Remove a ban. Use a reply or numeric Telegram user ID; arbitrary username lookup is not available. | `/unban 123456789` | Group admins; XYRON bot admins are mute-only |
| `/unbanall` | Unban imported IDs in the configured group (group owner only) | `/unbanall` | Group owner |
| `/banlist` | List bans recorded in XYRON's moderation history. | `/banlist` | Group admins; XYRON bot admins are mute-only |

## Kick and Restrict

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/kick` | Remove a member from the group. They may rejoin if they have an invite link. | `/kick @username spam` | Group admins; XYRON bot admins are mute-only |
| `/dkick` | Remove the user whose message you reply to, then delete that replied-to message. | `Reply to a message, then send /dkick spam` | Group admins; XYRON bot admins are mute-only |
| `/skick` | Remove a member and delete the command message after a successful action. | `/skick @username spam` | Group admins; XYRON bot admins are mute-only |
| `/kickme` | Remove yourself from this group. | `/kickme` | Everyone |

## Mute and Unmute

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/mute` | Restrict a member from sending messages. Add a duration for a timed mute; omit it for a permanent mute. | `/mute @username 30m spam` | Group admins; XYRON bot admins are mute-only |
| `/dmute` | Mute the user whose message you reply to, then delete that replied-to message. | `Reply to a message, then send /dmute spam` | Group admins; XYRON bot admins are mute-only |
| `/smute` | Mute a member and delete the command message after a successful action. | `/smute @username spam` | Group admins; XYRON bot admins are mute-only |
| `/tmute` | Temporarily restrict a member for the specified duration. | `/tmute @username 30m spam` | Group admins; XYRON bot admins are mute-only |
| `/unmute` | Remove XYRON's mute and restore the saved/default permissions. | `/unmute @username` | Group admins; XYRON bot admins are mute-only |
| `/mutelist` | List mute restrictions tracked by XYRON. | `/mutelist` | Group admins; XYRON bot admins are mute-only |

## Warnings

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/warn` | Give a member a warning. At the configured limit, XYRON applies the configured warning action. | `/warn @username spam` | Group admins; XYRON bot admins are mute-only |
| `/warns` | Show a member's current warning count. | `/warns @username` | Group admins; XYRON bot admins are mute-only |
| `/resetwarn` | Clear a member's warnings. | `/resetwarn @username` | Group admins; XYRON bot admins are mute-only |
| `/setwarnlimit` | Set how many warnings trigger the automatic warning action. | `/setwarnlimit 3` | Group admins; XYRON bot admins are mute-only |

## Admin Logs

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/modlog` | Show recent moderation actions. Add a number to choose how many entries to display. | `/modlog 5` | Group admins; XYRON bot admins are mute-only |
| `/modstats` | Show moderation action totals for the last 30 days. | `/modstats` | Group admins; XYRON bot admins are mute-only |
| `/appeals` | List open moderation appeals | `/appeals` | Group admins; XYRON bot admins are mute-only |
| `/reviewappeal` | Set an appeal's review result. This does not automatically change moderation. | `/reviewappeal 1 accepted Please review completed` | Group admins; XYRON bot admins are mute-only |

## Reports

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/report` | Report a message to the group's admins. Reply to the message and optionally include a reason. | `Reply to a message, then send /report spam` | Everyone |
| `/reports` | List open group reports | `/reports` | Group admins; XYRON bot admins are mute-only |
| `/reviewreport` | Set a report's review status. | `/reviewreport 1 reviewed` | Group admins; XYRON bot admins are mute-only |
| `/appeal` | Send an appeal for admin review. This does not automatically undo moderation. | `/appeal Please review this decision` | Everyone |

## Word Games

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/new4` | Start a Word Seek game with a 4-letter word. | `/new4` | Everyone |
| `/new5` | Start a Word Seek game with a 5-letter word. | `/new5` | Everyone |
| `/new6` | Start a Word Seek game with a 6-letter word. | `/new6` | Everyone |
| `/wordseek` | Start Word Seek. Choose a 4-, 5-, or 6-letter word. | `/wordseek 5` | Everyone |
| `/guess` | Submit a guess in your active Word Seek game. | `/guess crane` | Everyone |
| `/wordcancel` | Cancel your own active Word Seek game. | `/wordcancel` | The player who started that game |

## Family Life

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/family` | View your Family profile. Reply to someone with /family to view theirs. | `/family` | Everyone |
| `/familytree` | Show your family connections | `/familytree` | Everyone |
| `/familytop` | Open the main global Family Life leaderboard, ranked by couple protection points. | `/familytop` | Everyone |
| `/cp` | Open your Couple page and use its protection action to earn daily protection points. | `/cp` | Everyone |
| `/familyhelp` | Show instructions for Family Life commands and requests. | `/familyhelp` | Everyone |
| `/familyinfo` | View the Family profile of the person whose message you reply to. | `Reply to a message, then send /familyinfo` | Everyone |
| `/wife` | Send a marriage proposal to the person whose message you reply to. They must accept it. | `Reply to a message, then send /wife` | Everyone |
| `/husband` | Send a marriage proposal to the person whose message you reply to. They must accept it. | `Reply to a message, then send /husband` | Everyone |
| `/divorce` | Request a mutual divorce from your spouse by replying to their message. | `Reply to your spouse, then send /divorce` | Everyone |
| `/son` | Add the person you reply to as your son. | `Reply to a message, then send /son` | Everyone |
| `/daughter` | Add the person you reply to as your daughter. | `Reply to a message, then send /daughter` | Everyone |
| `/father` | Add the person you reply to as your father. | `Reply to a message, then send /father` | Everyone |
| `/mother` | Add the person you reply to as your mother. | `Reply to a message, then send /mother` | Everyone |
| `/brother` | Add the person you reply to as your brother. | `Reply to a message, then send /brother` | Everyone |
| `/sister` | Add the person you reply to as your sister. | `Reply to a message, then send /sister` | Everyone |
| `/friend` | Add the person you reply to as a friend. | `Reply to a message, then send /friend` | Everyone |
| `/familyreset` | Reset all Family relationship records. This is irreversible and only the XYRON owner can use it. | `/familyreset` | XYRON owner |

## Rules

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/rules` | Show group rules | `/rules` | Everyone |
| `/setrules` | Set the rules for this group. Use /rules to view them later. | `/setrules Be respectful and follow the admins' instructions` | Group admins; XYRON bot admins are mute-only |

## Notes

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/save` | Save a named reply that members can retrieve later. | `/save welcome Thanks for joining!` | Group admins; XYRON bot admins are mute-only |
| `/get` | Show a saved response by its name. | `/get welcome` | Everyone |
| `/notes` | List saved response names in this group. | `/notes` | Everyone |

## Filters

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/filter` | Save an automatic reply for a phrase. Put the phrase first, followed by the response. | `/filter hello Welcome to the group!` | Group admins; XYRON bot admins are mute-only |
| `/filters` | List the group's saved phrase filters. | `/filters` | Everyone |
| `/stop` | Remove a saved phrase filter. | `/stop hello` | Group admins; XYRON bot admins are mute-only |

## Locks

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/lock` | Block a supported message type in this group. | `/lock links` | Group admins; XYRON bot admins are mute-only |
| `/unlock` | Allow a previously locked message type again. | `/unlock links` | Group admins; XYRON bot admins are mute-only |
| `/locks` | List the message types currently locked in this group. | `/locks` | Everyone |

## Welcome and Goodbye

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/setwelcome` | Set the message sent when a member joins. Use {first_name} to insert their name. | `/setwelcome Welcome {first_name}!` | Group admins; XYRON bot admins are mute-only |
| `/setgoodbye` | Set the message sent when a member leaves. Use {first_name} to insert their name. | `/setgoodbye Goodbye {first_name}!` | Group admins; XYRON bot admins are mute-only |

## User Information

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/info` | Show a group member's status. Use a reply, username, or numeric ID. | `/info @username` | Everyone |

## Group Information

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/groupinfo` | Show information about this group. | `/groupinfo` | Everyone |

## Games

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/tictactoe` | Start a two-player Tic-Tac-Toe challenge in this group. Another member joins, then both players tap the board to make moves. | `/tictactoe` | Everyone |
| `/tttcancel` | Cancel the active Tic-Tac-Toe game if you are one of its players. | `/tttcancel` | Either Tic-Tac-Toe player |

## Reminders

| Command | What it does | Example | Who can use it |
| --- | --- | --- | --- |
| `/remind` | Create a one-time reminder. Use a duration such as 30m, 2h, or 1d. | `/remind 30m drink water` | Everyone |
| `/reminders` | List your pending reminders. | `/reminders` | Everyone |
| `/cancelreminder` | Cancel one of your reminders using its ID. | `/cancelreminder 1` | Everyone |

## Related text and button actions

- **Tic-Tac-Toe:** start with `/tictactoe`, then the other player taps **Join Game**. Both players make moves by tapping the board squares. `/tttcancel` cancels the game for either player. There is no `/tttmove` command in the help menu; the old command name only returns a note telling users to use the buttons.
- **Quote recall:** after a quote is saved, send `/q_<quote_id>` in the same group to recall it. Example: `/q_12`.
- **Rules shortcuts:** send `rules`, `#rules`, or `hash rules` as a standalone group message to request the group's rules.
- **Demote a XYRON bot admin:** the group owner replies to that person's message and sends `demote`.
- **Spam protector shortcut:** the XYRON owner can send `xyron spam protector on` or `xyron spam protector off` in a group.
- **Plain moderation shortcuts:** exact standalone `mute`, `unmute`, `kick`, `ban`, and `unban` text is also recognized in group chats; using slash commands is clearer and recommended.

## Important behavior notes

- `d` variants (`/dban`, `/dkick`, `/dmute`) act on the replied-to member and then try to delete that replied-to message. They require the bot's **Delete messages** permission in addition to the moderation permission.
- `s` variants (`/sban`, `/skick`, `/smute`) delete the command message after a successful action. They also require **Delete messages** permission.
- `/kick` removes a member by banning and then unbanning them. They may be able to rejoin with a valid invite link.
- `/unban` requires a reply or numeric ID for a user XYRON can identify. A username by itself is not a global Telegram lookup.
- `/familyreset` is destructive: it resets Family relationship records only. It does not reset moderation, users, groups, bot admins, other games, or settings.
- `/reviewappeal` changes the appeal's review status only; it does not automatically reverse a ban, mute, or other moderation action.
