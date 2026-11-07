---
slug: kit/plugin-guide
version: 1
title: Kit: the plugin guide
summary: How to install the Metorite plugin for your coding tool.
audience: admin
---

# Kit: the plugin guide

The Metorite plugin adds the connection, the session hooks, a set of commands and two starter agents to your coding tool.

## Install

1. Add the plugin source that your administrator gives you.
2. Install the plugin.
3. Sign in to Metorite when the tool asks.
4. Run the claim command with a task number to check that it works.

## What the plugin does not do

- It does not read your sign-in token in a hook.
- It does not change your repository. The kit files in this playbook are text that your own agent writes.
- It does not set a status line for you. A setup command asks before it writes your settings.

## For administrators

- Decide who may install it.
- Decide which commands you want in your organization.
- Keep your own rules in your organization guidance, not in the plugin.
