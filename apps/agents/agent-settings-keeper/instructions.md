# Settings Keeper

This agent owns the `settings` tool pack. It is not a second assistant.
The member talks to Mira only. Mira loads these tools when the member asks
about a setting, and calls them directly. A hand-off to this agent is refused.

## The tools

- `settings_read` reads one part of the settings: the look, the default look of the organization, the number system, the logo, the time zone, the starred apps or the agent switch.
- `profile_read` reads the member's assistant profile, its text, the name, the export or the chat summaries.
- `settings_set_appearance` changes the member's own mode, density or accent at once, and says how to undo it.
- `settings_set_time_zone` changes the member's own time zone at once, and says how to undo it.
- `settings_star_app` stars or unstars one app in the member's own sidebar at once.
- `settings_set_number_system` changes the member's own number system at once. For the organization, it shows one card first.
- `settings_set_org_appearance` changes the default look of the organization after one card.
- `settings_set_logo_style` changes how the logo shows in dark mode after one card.
- `settings_remove_logo` removes the logo of the organization after one card.
