---
name: morning-briefing
description: Give a short spoken morning briefing — weather, today's calendar, important mail and due reminders.
triggers: [good morning, morning briefing, brief me, what's my day like]
---
Give the user a calm, compact briefing they can listen to while making coffee. Aim for 20–40 seconds
of speech in total, in this order, skipping anything that has nothing to report:

1. Weather: call `weather` with `days: 1`. One sentence: conditions, the high, and rain chance only if
   it's above 30 per cent ("Bring an umbrella").
2. Calendar: if a calendar tool is available, list today's events. Mention at most the next three by
   time and title; say how many more there are, if any.
3. Mail: if a mail tool is available, count unread important messages and name the senders of the
   top two. Don't read message bodies unless asked.
4. Reminders: call `timers_list` and mention anything due today.

End with one short, dry line — never a question unless something needs a decision.
